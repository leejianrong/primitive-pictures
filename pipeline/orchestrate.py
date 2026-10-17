"""Driver for the RunPod diffusion batch job (slice V1 of docs/SLICES.md).

Builds a self-contained JOB_CMD (generate.py + config.py + the prompt payload
embedded directly, per ADR-0001 -- nothing fetched over the network at pod
boot), launches it through the vendored `runpod/launch.sh`, pulls the result
back over the relay, and unpacks it into `runs/<run_id>/`.

Only ever invokes launch.sh as an argv list, never through a shell (ADR-0002).
Secrets (RUNPOD_API_KEY) are passed through the environment only -- never
constructed into argv, never logged.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

import manifest
import promptbank
from config import (
    DEFAULT_CONTAINER_DISK_GB,
    DEFAULT_MAX_HOURLY_USD,
    DEFAULT_MAX_LIFETIME_SECS,
    DEFAULT_MODEL,
    DEFAULT_POD_IMAGE,
    DEFAULT_VIDEO_MODEL,
    DEFAULT_VIDEO_POD_IMAGE,
    resolve_backends,
    resolve_video_backend,
)

PIPELINE_DIR = Path(__file__).resolve().parent
RUNPOD_DIR = PIPELINE_DIR / "runpod"
REPO_ROOT = PIPELINE_DIR.parent
RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_PRIMITIVE_BIN = REPO_ROOT / "bin" / "primitive"
DEFAULT_SHAPE_COUNT = 100
# Video runs default to "dynamic" frame-sequence settings (reviewed 2026-10-16: a 30% drop
# looks better than 10%; 200 shapes read as abstract/jittery, 500 as cleaner). Starting
# points, not decisions -- see the sweeps in docs/SLICES-phase3.md V6.
DEFAULT_VIDEO_SHAPE_COUNT = 500
DEFAULT_DROP_RATE = 0.3
DEFAULT_PLAYBACK_FPS = 8  # generation is 24 fps; slower playback reads better for this look


@dataclass
class LaunchPlan:
    """Everything needed to launch the batch job -- constructed once, so it can
    be inspected (--dry-run) before a single dollar is spent."""

    run_id: str
    prompts: list[str]
    model_names: list[str]
    job_cmd: str
    env_overrides: dict[str, str]
    run_dir: Path


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def build_job_cmd(prompts: list[str], model_names: list[str], relay_code_base: str) -> str:
    """The pod-side command: write embedded files, install deps, run generate.py.

    Every input file is embedded as base64 literal content in this string --
    nothing is fetched at boot. See ADR-0001 and the runpod-jobs skill's
    warning about network steps ahead of the dead-man's-switch.
    """
    generate_py = (RUNPOD_DIR / "generate.py").read_text()
    config_py = (PIPELINE_DIR / "config.py").read_text()
    pod_requirements = (RUNPOD_DIR / "pod-requirements.txt").read_text()
    prompts_json = json.dumps({"prompts": prompts, "models": model_names})

    parts = [
        "set -eu",
        "mkdir -p /workspace",
        f"echo {_b64(generate_py)} | base64 -d > /workspace/generate.py",
        f"echo {_b64(config_py)} | base64 -d > /workspace/config.py",
        f"echo {_b64(pod_requirements)} | base64 -d > /workspace/pod-requirements.txt",
        f"echo {_b64(prompts_json)} | base64 -d > /workspace/prompts.json",
        # No -q: pip's own progress output keeps the idle-watchdog's "log
        # growing" check satisfied during the (potentially multi-minute)
        # dependency install, which produces zero output otherwise.
        "echo 'pod: installing dependencies'",
        "pip install -r /workspace/pod-requirements.txt",
        (
            "python3 /workspace/generate.py --prompts-file /workspace/prompts.json "
            f"--out-dir /workspace/out --relay-code-base {relay_code_base}"
        ),
    ]
    return " && ".join(parts)


def build_video_job_cmd(
    prompts: list[str], image_model_name: str, video_model_name: str, relay_code_base: str
) -> str:
    """The pod-side command for the video-gen job (SLICES-phase3.md V5):
    write embedded files, install the video job's own dependencies (separate
    from the image job's -- see pod-requirements-video.txt), run
    generate_video.py. Same nothing-fetched-at-boot discipline as
    build_job_cmd (ADR-0001)."""
    generate_py = (RUNPOD_DIR / "generate.py").read_text()
    generate_video_py = (RUNPOD_DIR / "generate_video.py").read_text()
    config_py = (PIPELINE_DIR / "config.py").read_text()
    pod_requirements = (RUNPOD_DIR / "pod-requirements-video.txt").read_text()
    prompts_json = json.dumps(
        {"prompts": prompts, "image_model": image_model_name, "video_model": video_model_name}
    )

    parts = [
        "set -eu",
        "mkdir -p /workspace",
        f"echo {_b64(generate_py)} | base64 -d > /workspace/generate.py",
        f"echo {_b64(generate_video_py)} | base64 -d > /workspace/generate_video.py",
        f"echo {_b64(config_py)} | base64 -d > /workspace/config.py",
        f"echo {_b64(pod_requirements)} | base64 -d > /workspace/pod-requirements.txt",
        f"echo {_b64(prompts_json)} | base64 -d > /workspace/prompts.json",
        "echo 'pod: installing dependencies'",
        "pip install -r /workspace/pod-requirements.txt",
        (
            "python3 /workspace/generate_video.py --prompts-file /workspace/prompts.json "
            f"--out-dir /workspace/out --relay-code-base {relay_code_base}"
        ),
    ]
    return " && ".join(parts)


def build_video_plan(
    prompts: list[str],
    image_model_name: str = DEFAULT_MODEL,
    video_model_name: str = DEFAULT_VIDEO_MODEL,
    run_id: str | None = None,
    max_hourly_usd: float = DEFAULT_MAX_HOURLY_USD,
    max_lifetime_secs: int = DEFAULT_MAX_LIFETIME_SECS,
    pod_image: str = DEFAULT_VIDEO_POD_IMAGE,
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    hf_token: str | None = None,
) -> LaunchPlan:
    if not prompts:
        raise ValueError("no prompts given")
    resolve_backends(image_model_name)  # raises a clear error on an unknown image model
    resolve_video_backend(video_model_name)  # same, for the video model

    run_id = run_id or time.strftime("run-%Y%m%d-%H%M%S", time.gmtime())
    relay_code_base = f"ppics-video-{run_id}"
    job_cmd = build_video_job_cmd(prompts, image_model_name, video_model_name, relay_code_base)

    env_overrides = {
        "RP_POD_NAME": f"ppics-video-{run_id}",
        "RP_POD_IMAGE": pod_image,
        "RP_JOB_CMD": job_cmd,
        "RP_COMPUTE_TYPE": "GPU",
        "RP_MAX_HOURLY_USD": str(max_hourly_usd),
        "RP_MAX_LIFETIME_SECS": str(max_lifetime_secs),
        "RP_CONTAINER_DISK_GB": str(container_disk_gb),
        "RP_STATE_FILE": str(RUNS_DIR / run_id / ".rp-state"),
    }
    if hf_token:
        env_overrides["RP_POD_ENV_JSON"] = json.dumps({"HF_TOKEN": hf_token})
    return LaunchPlan(
        run_id=run_id,
        prompts=prompts,
        model_names=[image_model_name, video_model_name],
        job_cmd=job_cmd,
        env_overrides=env_overrides,
        run_dir=RUNS_DIR / run_id,
    )


@dataclass(frozen=True)
class VideoSettings:
    """How `primitive -frames` is run over each clip (ADR-0007/0009). The new-shapes
    budget defaults to drop_rate * shape_count, which holds the shape count steady."""

    shape_count: int = DEFAULT_VIDEO_SHAPE_COUNT
    shape_mode: int | None = None
    drop_rate: float = DEFAULT_DROP_RATE
    new_shapes: int | None = None
    playback_fps: int = DEFAULT_PLAYBACK_FPS

    @property
    def new_shapes_per_frame(self) -> int:
        if self.new_shapes is not None:
            return self.new_shapes
        return round(self.drop_rate * self.shape_count)

    def as_dict(self) -> dict:
        return {
            "shape_count": self.shape_count,
            "shape_mode": self.shape_mode,
            "drop_rate": self.drop_rate,
            "new_shapes_per_frame": self.new_shapes_per_frame,
            "playback_fps": self.playback_fps,
        }


def _run_rel(run_dir: Path, path: str) -> str:
    """A path primitive reported (absolute, as we passed it) -> relative to the run dir."""
    p = Path(path)
    try:
        return str(p.relative_to(run_dir)) if p.is_absolute() else str(p)
    except ValueError:
        return str(p)


def _read_frame_items(run_dir: Path, frames_json: Path) -> list[manifest.FrameItem]:
    if not frames_json.exists():
        return []
    try:
        rows = json.loads(frames_json.read_text())
    except json.JSONDecodeError:
        return []
    return [
        manifest.FrameItem(
            index=r["index"],
            source=_run_rel(run_dir, r["source"]),
            status=r["status"],
            output=_run_rel(run_dir, r["output"]),
            shapes=r.get("shapes", 0),
            carried=r.get("carried", 0),
            dropped=r.get("dropped", 0),
            added=r.get("added", 0),
            carried_fraction=r.get("carried_fraction", 0.0),
            added_fraction=r.get("added_fraction", 0.0),
            seconds=r.get("seconds", 0.0),
        )
        for r in rows
    ]


def _encode_preview(
    ffmpeg_bin: str, frames_dir: Path, out: Path, fps: int
) -> subprocess.CompletedProcess:
    # pad: primitive's output height can be odd, which libx264 rejects.
    cmd = [
        ffmpeg_bin, "-y", "-loglevel", "error",
        "-framerate", str(fps), "-i", str(frames_dir / "%04d.png"),
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ]  # fmt: skip
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def process_video_run(
    run_dir: Path,
    primitive_bin: Path,
    settings: VideoSettings | None = None,
    ffmpeg_bin: str | None = None,
) -> manifest.VideoManifest:
    """Run `primitive -frames` once per clip over the pod's generated frames, export a
    slowed preview mp4 of the result, and write runs/<run_id>/manifest.json (schema 2).

    One subprocess per clip, not per frame (ADR-0007): each frame's shapes seed the next.
    A clip's failure doesn't abort the rest, and frames finished before it are kept in the
    manifest, since primitive writes them incrementally.
    """
    settings = settings or VideoSettings()
    ffmpeg_bin = ffmpeg_bin or shutil.which("ffmpeg")
    rows = json.loads((run_dir / "out" / "generation-manifest.json").read_text())

    items: list[manifest.VideoItem] = []
    for i, row in enumerate(rows):
        common = {
            "index": i,
            "prompt": row["prompt"],
            "image_model": row.get("image_model", ""),
            "video_model": row.get("video_model", ""),
            "generation_seconds": row.get("seconds"),
            "settings": settings.as_dict(),
        }
        frame_files = row.get("frame_files") or []
        if row.get("error") or not frame_files:
            # Pod-side failure: no frames to process. Carry the pod's own error through.
            items.append(
                manifest.VideoItem(
                    **common,
                    status="failed",
                    error=row.get("error") or "pod produced no frames",
                )
            )
            continue

        item_rel = Path(frame_files[0]).parent.parent
        frames_dir = run_dir / "out" / item_rel / "frames"
        out_dir = run_dir / "out" / item_rel / "primitive"
        common["still"] = f"out/{row['still_file']}" if row.get("still_file") else None
        common["source_clip"] = f"out/{row['clip_file']}" if row.get("clip_file") else None
        common["primitive_dir"] = f"out/{item_rel}/primitive"

        cmd = [
            str(primitive_bin),
            "-frames", str(frames_dir),
            "-out", str(out_dir),
            "-n", str(settings.shape_count),
            "-new-shapes-per-frame", str(settings.new_shapes_per_frame),
            "-drop-rate", str(settings.drop_rate),
        ]  # fmt: skip
        if settings.shape_mode is not None:
            cmd += ["-m", str(settings.shape_mode)]

        started = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        common["primitive_seconds"] = round(time.monotonic() - started, 2)
        frames = _read_frame_items(run_dir, out_dir / "frames.json")
        warm = [f.carried_fraction for f in frames if f.status == "ok" and f.carried > 0]
        common["frames"] = frames
        common["mean_carried_fraction"] = sum(warm) / len(warm) if warm else None

        complete = result.returncode == 0 and len(frames) == len(frame_files)
        if not complete:
            reason = (result.stderr or result.stdout or "").strip()[-500:]
            if result.returncode == 0:
                reason = f"primitive wrote {len(frames)} of {len(frame_files)} frames"
            items.append(
                manifest.VideoItem(**common, status="failed", error=reason or "primitive failed")
            )
            continue

        warning = None
        clip_rel = None
        if ffmpeg_bin is None:
            warning = "ffmpeg not found; no preview mp4"
        else:
            clip_path = out_dir.parent / "primitive.mp4"
            enc = _encode_preview(ffmpeg_bin, out_dir, clip_path, settings.playback_fps)
            if enc.returncode == 0:
                clip_rel = f"out/{item_rel}/primitive.mp4"
            else:
                warning = f"preview mp4 failed: {(enc.stderr or '').strip()[-300:]}"
        items.append(
            manifest.VideoItem(**common, status="ok", primitive_clip=clip_rel, warning=warning)
        )

    m = manifest.VideoManifest(run_id=run_dir.name, items=items)
    manifest.write_video(run_dir / "manifest.json", m)
    return m


def execute_video(
    plan: LaunchPlan,
    base_env: dict[str, str],
    primitive_bin: Path,
    settings: VideoSettings | None = None,
) -> int:
    """Run the video-gen plan for real: up -> relay-get -> unpack -> primitive
    over each clip's frames -> manifest, always down in finally. The pod is torn
    down in `finally` whatever happens after it, and primitive runs locally."""
    plan.run_dir.mkdir(parents=True, exist_ok=True)
    env = {**base_env, **plan.env_overrides}

    up = _run_launcher(["up"], env)
    print(up.stderr, file=sys.stderr)
    if up.returncode != 0:
        if up.returncode == LAUNCHER_TIMEOUT_RETURNCODE:
            print(
                "orchestrate: launch.sh up timed out, tearing down any pod it made", file=sys.stderr
            )
            _teardown_after_failed_up(env)
        else:
            print("orchestrate: launch.sh up failed, nothing to tear down", file=sys.stderr)
        return 1
    pod_id = up.stdout.strip()
    print(f"orchestrate: pod {pod_id} up, run_id={plan.run_id}")

    try:
        if not _wait_for_boot(env):
            print(
                f"orchestrate: no log output from pod {pod_id} within "
                f"{DEFAULT_BOOT_TIMEOUT_SECS}s of creation -- likely failed to boot "
                "(stuck image pull, host provisioning issue)",
                file=sys.stderr,
            )
            return 1

        pulled = _run_launcher(["relay-get", str(plan.run_dir)], env)
        print(pulled.stderr, file=sys.stderr)
        if pulled.returncode != 0:
            print("orchestrate: relay-get failed", file=sys.stderr)
            return 1

        archive = plan.run_dir / "ppics-output.tar.gz"
        if not archive.exists():
            print(f"orchestrate: expected archive not found at {archive}", file=sys.stderr)
            return 1
        with tarfile.open(archive) as tar:
            tar.extractall(plan.run_dir, filter="data")
        print(f"orchestrate: unpacked into {plan.run_dir / 'out'}")

        m = process_video_run(plan.run_dir, primitive_bin, settings)
        manifest_path = plan.run_dir / "manifest.json"
        print(f"orchestrate: {m.ok_count} ok, {m.failed_count} failed -> {manifest_path}")
        return 1 if m.ok_count == 0 else 0
    finally:
        down = _run_launcher(["down"], env)
        print(down.stderr, file=sys.stderr)


def build_plan(
    prompts: list[str],
    model_selection: str,
    run_id: str | None = None,
    max_hourly_usd: float = DEFAULT_MAX_HOURLY_USD,
    max_lifetime_secs: int = DEFAULT_MAX_LIFETIME_SECS,
    pod_image: str = DEFAULT_POD_IMAGE,
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    hf_token: str | None = None,
) -> LaunchPlan:
    if not prompts:
        raise ValueError("no prompts given")
    backends = resolve_backends(model_selection)
    model_names = [b.name for b in backends]

    run_id = run_id or time.strftime("run-%Y%m%d-%H%M%S", time.gmtime())
    relay_code_base = f"ppics-{run_id}"
    job_cmd = build_job_cmd(prompts, model_names, relay_code_base)

    env_overrides = {
        "RP_POD_NAME": f"ppics-{run_id}",
        "RP_POD_IMAGE": pod_image,
        "RP_JOB_CMD": job_cmd,
        "RP_COMPUTE_TYPE": "GPU",
        "RP_MAX_HOURLY_USD": str(max_hourly_usd),
        "RP_MAX_LIFETIME_SECS": str(max_lifetime_secs),
        "RP_CONTAINER_DISK_GB": str(container_disk_gb),
        "RP_STATE_FILE": str(RUNS_DIR / run_id / ".rp-state"),
    }
    # HF_TOKEN needs to travel as a literal container env value for gated
    # models (Flux.1-schnell) -- huggingface_hub picks it up automatically
    # from the env, no code change needed on the pod side. This is the
    # sanctioned exception in the handling-secrets skill: the secret has to
    # land in a resource we're creating. NOTE: RunPod's own pod-get/create
    # response echoes the `env` field back verbatim (same as PUBLIC_KEY) --
    # never print a raw pod-create/get response anywhere this could surface.
    if hf_token:
        env_overrides["RP_POD_ENV_JSON"] = json.dumps({"HF_TOKEN": hf_token})
    return LaunchPlan(
        run_id=run_id,
        prompts=prompts,
        model_names=model_names,
        job_cmd=job_cmd,
        env_overrides=env_overrides,
        run_dir=RUNS_DIR / run_id,
    )


# Python-side ceilings on the short launcher subcommands -- defense in depth behind launch.sh's own
# curl --connect-timeout/--max-time (ADR-0001, 2026-09-27 incident). Each is sized above the worst
# case of the curl calls the subcommand makes in sequence (30s apiece): `up` creates and checks a
# pod, `down` looks the pod up, deletes it and verifies. `relay-get` is deliberately absent: it
# waits for the whole job and is long by design.
LAUNCHER_TIMEOUT_SECS: dict[str, int] = {"up": 180, "down": 150, "logs": 60}
LAUNCHER_TIMEOUT_RETURNCODE = 124  # same as timeout(1)


def _run_launcher(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Argv-list only -- never shell=True (ADR-0002).

    Short subcommands get a timeout; on expiry this returns a CompletedProcess with returncode
    LAUNCHER_TIMEOUT_RETURNCODE instead of raising, so callers' existing failure paths (and the
    `finally: down`) handle it unchanged.
    """
    timeout = LAUNCHER_TIMEOUT_SECS.get(args[0]) if args else None
    try:
        return subprocess.run(
            ["bash", str(RUNPOD_DIR / "launch.sh"), *args],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (
            exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        return subprocess.CompletedProcess(
            args=exc.cmd,
            returncode=LAUNCHER_TIMEOUT_RETURNCODE,
            stdout="",
            stderr=f"{partial or ''}\norchestrate: launch.sh {args[0]} timed out after {timeout}s",
        )


def _teardown_after_failed_up(env: dict[str, str]) -> None:
    """`up` timed out. A pod may have been created just before the stall with its id never
    returned. `down` finds the pod by name and is idempotent, so attempt it."""
    down = _run_launcher(["down"], env)
    print(down.stderr, file=sys.stderr)


DEFAULT_BOOT_TIMEOUT_SECS = 180
DEFAULT_BOOT_POLL_INTERVAL_SECS = 15


def _wait_for_boot(
    env: dict[str, str],
    timeout_secs: int = DEFAULT_BOOT_TIMEOUT_SECS,
    poll_interval_secs: int = DEFAULT_BOOT_POLL_INTERVAL_SECS,
    sleep=time.sleep,
) -> bool:
    """Poll `launch.sh logs` until the pod produces ANY log output, or
    timeout_secs elapses with none.

    A silent pod this long means the container itself never started (a
    stuck image pull, a host provisioning issue) -- distinct from the job
    legitimately running long once it *has* started, which relay-get's own
    much longer deadline (RP_MAX_LIFETIME_SECS) already covers. Without
    this, the only way to notice a stuck boot is the full job deadline, or
    a human manually polling `pod list`/`pod logs` -- which is exactly what
    caught a real ~20-minute stuck `awaiting_container` pod during
    SLICES-phase3.md V5's validation run (see ADR-0001's addendum).

    Prints a heartbeat each poll (the wait was previously silent start to
    finish -- indistinguishable from a hang while it was happening) and, on
    timeout, the pod's `system`-source logs (RunPod's own platform
    narration -- image pull progress, container create/start -- separate
    from the job's own `container`-source output) so a stuck pull is
    visible in the failure itself instead of a bare "no output" message.
    """
    start = time.time()
    deadline = start + timeout_secs
    while time.time() < deadline:
        result = _run_launcher(["logs"], env)
        if result.stdout.strip():
            return True
        elapsed = int(time.time() - start)
        print(
            f"orchestrate: waiting for pod boot... {elapsed}s elapsed, no output yet",
            file=sys.stderr,
        )
        sleep(poll_interval_secs)

    diagnostic = _run_launcher(["logs", "--source", "system", "--tail", "50"], env)
    print(
        "orchestrate: no output within the boot timeout -- platform (system-source) logs:",
        file=sys.stderr,
    )
    print(diagnostic.stdout.strip() or "(no system-source log lines either)", file=sys.stderr)
    if diagnostic.stderr:
        print(diagnostic.stderr, file=sys.stderr)
    return False


def resolve_primitive_bin(explicit: str | None) -> Path:
    candidate = Path(explicit) if explicit else DEFAULT_PRIMITIVE_BIN
    if not candidate.exists():
        raise FileNotFoundError(
            f"primitive binary not found at {candidate} -- run `make build` first, "
            "or pass --primitive-bin"
        )
    return candidate


def process_run(
    run_dir: Path,
    primitive_bin: Path,
    shape_count: int = DEFAULT_SHAPE_COUNT,
    shape_mode: int | None = None,
) -> manifest.Manifest:
    """Run `primitive` on every pod-generated seed image, building
    runs/<run_id>/manifest.json. Reads the pod's own out/generation-manifest.json
    (written by runpod/generate.py) to know what to process.

    One item's failure (bad seed image, primitive non-zero exit) doesn't abort
    the rest -- partial-result-with-gaps-flagged, recorded per item.
    """
    pod_manifest_path = run_dir / "out" / "generation-manifest.json"
    rows = json.loads(pod_manifest_path.read_text())

    items: list[manifest.Item] = []
    for i, row in enumerate(rows):
        common = {
            "index": i,
            "model": row["model"],
            "prompt": row["prompt"],
            "generation_seconds": row.get("seconds"),
        }
        if row.get("file") is None:
            # Pod-side failure (model load or generation itself failed) --
            # there's no seed image, so there's nothing for primitive to run
            # on. Carry the pod's own error through unchanged.
            items.append(
                manifest.Item(**common, seed_image="", status="failed", error=row.get("error"))
            )
            continue

        seed_rel = f"out/{row['file']}"
        stem = Path(row["file"]).stem
        parent = Path(row["file"]).parent
        primitive_png_rel = f"out/{parent}/{stem}.primitive.png"
        primitive_svg_rel = f"out/{parent}/{stem}.primitive.svg"

        cmd = [
            str(primitive_bin),
            "-i",
            str(run_dir / seed_rel),
            "-o",
            str(run_dir / primitive_png_rel),
            "-o",
            str(run_dir / primitive_svg_rel),
            "-n",
            str(shape_count),
        ]
        if shape_mode is not None:
            cmd += ["-m", str(shape_mode)]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        common["seed_image"] = seed_rel
        if result.returncode == 0:
            items.append(
                manifest.Item(
                    **common,
                    status="ok",
                    primitive_png=primitive_png_rel,
                    primitive_svg=primitive_svg_rel,
                )
            )
        else:
            error = (result.stderr or result.stdout or "unknown error").strip()[:500]
            items.append(manifest.Item(**common, status="failed", error=error))

    m = manifest.Manifest(run_id=run_dir.name, items=items)
    manifest.write(run_dir / "manifest.json", m)
    return m


def execute(
    plan: LaunchPlan,
    base_env: dict[str, str],
    primitive_bin: Path,
    shape_count: int = DEFAULT_SHAPE_COUNT,
    shape_mode: int | None = None,
) -> int:
    """Run the plan for real: up -> relay-get -> unpack -> primitive per item,
    always down in finally."""
    plan.run_dir.mkdir(parents=True, exist_ok=True)
    env = {**base_env, **plan.env_overrides}

    up = _run_launcher(["up"], env)
    print(up.stderr, file=sys.stderr)
    if up.returncode != 0:
        if up.returncode == LAUNCHER_TIMEOUT_RETURNCODE:
            print(
                "orchestrate: launch.sh up timed out, tearing down any pod it made", file=sys.stderr
            )
            _teardown_after_failed_up(env)
        else:
            print("orchestrate: launch.sh up failed, nothing to tear down", file=sys.stderr)
        return 1
    pod_id = up.stdout.strip()
    print(f"orchestrate: pod {pod_id} up, run_id={plan.run_id}")

    try:
        if not _wait_for_boot(env):
            print(
                f"orchestrate: no log output from pod {pod_id} within "
                f"{DEFAULT_BOOT_TIMEOUT_SECS}s of creation -- likely failed to boot "
                "(stuck image pull, host provisioning issue)",
                file=sys.stderr,
            )
            return 1

        pulled = _run_launcher(["relay-get", str(plan.run_dir)], env)
        print(pulled.stderr, file=sys.stderr)
        if pulled.returncode != 0:
            print("orchestrate: relay-get failed", file=sys.stderr)
            return 1

        archive = plan.run_dir / "ppics-output.tar.gz"
        if not archive.exists():
            print(f"orchestrate: expected archive not found at {archive}", file=sys.stderr)
            return 1
        with tarfile.open(archive) as tar:
            tar.extractall(plan.run_dir, filter="data")
        print(f"orchestrate: unpacked into {plan.run_dir / 'out'}")

        m = process_run(plan.run_dir, primitive_bin, shape_count, shape_mode)
        manifest_path = plan.run_dir / "manifest.json"
        print(f"orchestrate: {m.ok_count} ok, {m.failed_count} failed -> {manifest_path}")
        return 1 if m.ok_count == 0 else 0
    finally:
        down = _run_launcher(["down"], env)
        print(down.stderr, file=sys.stderr)


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def resolve_prompts(args: argparse.Namespace) -> list[str]:
    """Exactly one prompt source: --prompts a file, or --count from the bank."""
    if args.prompts and args.count:
        raise ValueError("pass either --prompts or --count (bank sampling), not both")
    if args.prompts:
        return [
            line.strip() for line in Path(args.prompts).read_text().splitlines() if line.strip()
        ]
    if args.count:
        return promptbank.sample(
            count=args.count,
            seed=args.seed,
            categories=_split_csv(args.category),
            styles=_split_csv(args.style),
        )
    raise ValueError("give either --prompts <file> or --count N (bank sampling)")


def main(argv: list[str] | None = None) -> int:
    # Loads RUNPOD_API_KEY / RP_GPU_TYPE etc. from a gitignored .env at the
    # repo root if present (see .env.example) -- never printed, never
    # constructed into argv, only ever read into the process environment.
    # A missing .env is not an error: load_dotenv() is a silent no-op then.
    load_dotenv(REPO_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", help="path to a newline-delimited prompt file")
    parser.add_argument(
        "--category",
        help="comma-separated bank categories (default: all); see --list-bank",
    )
    parser.add_argument(
        "--style",
        help="comma-separated bank styles (default: any); see --list-bank",
    )
    parser.add_argument("--count", type=int, help="number of prompts to sample from the bank")
    parser.add_argument("--seed", type=int, default=0, help="bank sampling seed (default 0)")
    parser.add_argument(
        "--list-bank",
        action="store_true",
        help="print the bank's categories and styles, then exit",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"backend name (default: {DEFAULT_MODEL}), or 'compare' to run all of them",
    )
    parser.add_argument("--max-hourly-usd", type=float, default=DEFAULT_MAX_HOURLY_USD)
    parser.add_argument("--max-lifetime-secs", type=int, default=DEFAULT_MAX_LIFETIME_SECS)
    parser.add_argument(
        "--pod-image",
        help=(
            f"pod container image (default: {DEFAULT_POD_IMAGE}, or "
            f"{DEFAULT_VIDEO_POD_IMAGE} with --video, which needs torch >= 2.5)"
        ),
    )
    parser.add_argument(
        "--container-disk-gb",
        type=int,
        default=DEFAULT_CONTAINER_DISK_GB,
        help=(
            f"pod container disk size in GB (default {DEFAULT_CONTAINER_DISK_GB}, "
            "sized for --model compare downloading all 3 models' weights at once)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and print the launch plan (job command, env) without spending anything",
    )
    parser.add_argument(
        "--video",
        action="store_true",
        help=(
            "video-gen mode (SLICES-phase3.md V5): --model generates a still per "
            "prompt, --video-model animates it (I2V), frames land under "
            "runs/<run_id>/out/, then `primitive -frames` runs over each clip and a "
            "slowed preview mp4 is written next to it (see --playback-fps)"
        ),
    )
    parser.add_argument(
        "--video-model",
        default=DEFAULT_VIDEO_MODEL,
        help=f"video backend name (default: {DEFAULT_VIDEO_MODEL})",
    )
    parser.add_argument(
        "--shape-count",
        type=int,
        help=(
            f"shapes per primitive run (default {DEFAULT_SHAPE_COUNT}; "
            f"{DEFAULT_VIDEO_SHAPE_COUNT} with --video, where it is each frame's total)"
        ),
    )
    parser.add_argument(
        "--drop-rate",
        type=float,
        default=DEFAULT_DROP_RATE,
        help=(
            "--video: fraction of the previous frame's shapes dropped before new ones "
            f"are added (default {DEFAULT_DROP_RATE}; higher = more dynamic)"
        ),
    )
    parser.add_argument(
        "--new-shapes-per-frame",
        type=int,
        help=(
            "--video: new shapes per frame "
            "(default drop-rate x shape-count, which holds the count steady)"
        ),
    )
    parser.add_argument(
        "--playback-fps",
        type=int,
        default=DEFAULT_PLAYBACK_FPS,
        help=f"--video: fps of the preview mp4 (default {DEFAULT_PLAYBACK_FPS}; generation is 24)",
    )
    parser.add_argument(
        "--shape-mode",
        type=int,
        help="primitive's -m mode (default: primitive's own default, triangles)",
    )
    parser.add_argument(
        "--primitive-bin",
        help=f"path to the primitive binary (default: {DEFAULT_PRIMITIVE_BIN})",
    )
    parser.add_argument(
        "--process-only",
        metavar="RUN_DIR",
        help=(
            "skip RunPod entirely -- just (re-)run primitive over an existing "
            "run directory's out/generation-manifest.json (with --video, over its "
            "clips' frames). No prompts/model "
            "needed; useful for iterating on shape-count/mode locally, or for "
            "testing this step without spending anything"
        ),
    )
    args = parser.parse_args(argv)

    video_settings = VideoSettings(
        shape_count=args.shape_count or DEFAULT_VIDEO_SHAPE_COUNT,
        shape_mode=args.shape_mode,
        drop_rate=args.drop_rate,
        new_shapes=args.new_shapes_per_frame,
        playback_fps=args.playback_fps,
    )
    image_shape_count = args.shape_count or DEFAULT_SHAPE_COUNT

    if args.list_bank:
        print("categories:", ", ".join(promptbank.CATEGORY_NAMES))
        print("styles:", ", ".join(promptbank.STYLES))
        return 0

    if args.process_only:
        primitive_bin = resolve_primitive_bin(args.primitive_bin)
        run_dir = Path(args.process_only)
        if args.video:
            m = process_video_run(run_dir, primitive_bin, video_settings)
        else:
            m = process_run(run_dir, primitive_bin, image_shape_count, args.shape_mode)
        print(f"orchestrate: {m.ok_count} ok, {m.failed_count} failed")
        return 1 if m.ok_count == 0 else 0

    prompts = resolve_prompts(args)
    if args.video:
        plan = build_video_plan(
            prompts,
            args.model,
            args.video_model,
            max_hourly_usd=args.max_hourly_usd,
            max_lifetime_secs=args.max_lifetime_secs,
            pod_image=args.pod_image or DEFAULT_VIDEO_POD_IMAGE,
            container_disk_gb=args.container_disk_gb,
            hf_token=os.environ.get("HF_TOKEN"),
        )
    else:
        plan = build_plan(
            prompts,
            args.model,
            max_hourly_usd=args.max_hourly_usd,
            max_lifetime_secs=args.max_lifetime_secs,
            pod_image=args.pod_image or DEFAULT_POD_IMAGE,
            container_disk_gb=args.container_disk_gb,
            hf_token=os.environ.get("HF_TOKEN"),
        )

    # Writing the resolved prompt set to disk is free (no RunPod/network
    # involved) and is what makes a bank-sampled run reproducible/inspectable
    # after the fact -- do this whether or not we go on to spend anything.
    plan.run_dir.mkdir(parents=True, exist_ok=True)
    (plan.run_dir / "prompts.txt").write_text("\n".join(plan.prompts) + "\n")

    if args.dry_run:
        print(f"run_id: {plan.run_id}")
        print(f"models: {plan.model_names}")
        print(f"prompts ({len(plan.prompts)}):")
        for p in plan.prompts:
            print(f"  - {p}")
        print("env overrides:")
        for k, v in plan.env_overrides.items():
            if k == "RP_JOB_CMD":
                shown = f"<{len(v)} chars, see below>"
            elif k == "RP_POD_ENV_JSON":
                # May carry HF_TOKEN -- never print it, even in a dry-run.
                shown = f"<{len(v)} chars, redacted>"
            else:
                shown = v
            print(f"  {k}={shown}")
        print("--- RP_JOB_CMD ---")
        print(plan.job_cmd)
        return 0

    primitive_bin = resolve_primitive_bin(args.primitive_bin)

    if args.video:
        return execute_video(plan, dict(os.environ), primitive_bin, video_settings)

    return execute(
        plan,
        dict(os.environ),
        primitive_bin,
        shape_count=image_shape_count,
        shape_mode=args.shape_mode,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # execute()'s finally already tore the pod down by this point -- this
        # only replaces the default KeyboardInterrupt traceback with a clean
        # exit, matching the conventional SIGINT exit code.
        print("orchestrate: interrupted", file=sys.stderr)
        raise SystemExit(130) from None
