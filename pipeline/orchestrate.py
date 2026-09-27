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
    DEFAULT_POD_IMAGE,
    resolve_backends,
)

PIPELINE_DIR = Path(__file__).resolve().parent
RUNPOD_DIR = PIPELINE_DIR / "runpod"
REPO_ROOT = PIPELINE_DIR.parent
RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_PRIMITIVE_BIN = REPO_ROOT / "bin" / "primitive"
DEFAULT_SHAPE_COUNT = 100


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


def build_plan(
    prompts: list[str],
    model_selection: str,
    run_id: str | None = None,
    max_hourly_usd: float = DEFAULT_MAX_HOURLY_USD,
    max_lifetime_secs: int = DEFAULT_MAX_LIFETIME_SECS,
    pod_image: str = DEFAULT_POD_IMAGE,
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
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
    return LaunchPlan(
        run_id=run_id,
        prompts=prompts,
        model_names=model_names,
        job_cmd=job_cmd,
        env_overrides=env_overrides,
        run_dir=RUNS_DIR / run_id,
    )


def _run_launcher(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Argv-list only -- never shell=True (ADR-0002)."""
    return subprocess.run(
        ["bash", str(RUNPOD_DIR / "launch.sh"), *args],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


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
        common = {
            "index": i,
            "model": row["model"],
            "prompt": row["prompt"],
            "seed_image": seed_rel,
            "generation_seconds": row.get("seconds"),
        }
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
        print("orchestrate: launch.sh up failed, nothing to tear down", file=sys.stderr)
        return 1
    pod_id = up.stdout.strip()
    print(f"orchestrate: pod {pod_id} up, run_id={plan.run_id}")

    try:
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
        default="compare",
        help="backend name, or 'compare' to run all of them (default)",
    )
    parser.add_argument("--max-hourly-usd", type=float, default=DEFAULT_MAX_HOURLY_USD)
    parser.add_argument("--max-lifetime-secs", type=int, default=DEFAULT_MAX_LIFETIME_SECS)
    parser.add_argument("--pod-image", default=DEFAULT_POD_IMAGE)
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
        "--shape-count",
        type=int,
        default=DEFAULT_SHAPE_COUNT,
        help=f"shapes per primitive run (default {DEFAULT_SHAPE_COUNT})",
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
            "run directory's out/generation-manifest.json. No prompts/model "
            "needed; useful for iterating on shape-count/mode locally, or for "
            "testing this step without spending anything"
        ),
    )
    args = parser.parse_args(argv)

    if args.list_bank:
        print("categories:", ", ".join(promptbank.CATEGORY_NAMES))
        print("styles:", ", ".join(promptbank.STYLES))
        return 0

    if args.process_only:
        primitive_bin = resolve_primitive_bin(args.primitive_bin)
        m = process_run(Path(args.process_only), primitive_bin, args.shape_count, args.shape_mode)
        print(f"orchestrate: {m.ok_count} ok, {m.failed_count} failed")
        return 1 if m.ok_count == 0 else 0

    prompts = resolve_prompts(args)
    plan = build_plan(
        prompts,
        args.model,
        max_hourly_usd=args.max_hourly_usd,
        max_lifetime_secs=args.max_lifetime_secs,
        pod_image=args.pod_image,
        container_disk_gb=args.container_disk_gb,
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
            shown = v if k != "RP_JOB_CMD" else f"<{len(v)} chars, see below>"
            print(f"  {k}={shown}")
        print("--- RP_JOB_CMD ---")
        print(plan.job_cmd)
        return 0

    primitive_bin = resolve_primitive_bin(args.primitive_bin)

    import os

    return execute(
        plan,
        dict(os.environ),
        primitive_bin,
        shape_count=args.shape_count,
        shape_mode=args.shape_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
