import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import manifest
import orchestrate


def test_main_loads_dotenv_from_repo_root_before_anything_else():
    # Verifies main() wires up .env loading at all, without touching the
    # real filesystem or a real .env -- load_dotenv is mocked, so this can't
    # pick up (or require) an actual secret.
    with (
        patch.object(orchestrate, "load_dotenv") as mock_load_dotenv,
        patch.object(orchestrate, "promptbank") as mock_bank,
    ):
        mock_bank.CATEGORY_NAMES = ()
        mock_bank.STYLES = ()
        orchestrate.main(["--list-bank"])

    mock_load_dotenv.assert_called_once_with(orchestrate.REPO_ROOT / ".env")


def test_build_job_cmd_embeds_files_recoverably():
    job_cmd = orchestrate.build_job_cmd(["a cat"], ["sd15"], "ppics-test")

    # every base64 blob in the command must decode to real content, and the
    # prompts payload must round-trip exactly -- this is the whole mechanism
    # ADR-0001 relies on (nothing fetched over the network at boot).
    b64_blobs = [
        part.split("echo ", 1)[1].split(" | base64", 1)[0]
        for part in job_cmd.split(" && ")
        if part.startswith("echo ") and " | base64 -d > " in part
    ]
    assert len(b64_blobs) == 4
    decoded = [base64.b64decode(b).decode() for b in b64_blobs]
    assert "def main" in decoded[0]  # generate.py
    assert "BACKENDS" in decoded[1]  # config.py
    assert "diffusers" in decoded[2]  # pod-requirements.txt
    prompts_payload = json.loads(decoded[3])
    assert prompts_payload == {"prompts": ["a cat"], "models": ["sd15"]}

    assert "python3 /workspace/generate.py" in job_cmd
    assert "--relay-code-base ppics-test" in job_cmd
    # pip install must NOT be silenced (-q): with no output during a
    # multi-minute dependency install, the idle-watchdog could kill the job
    # mid-install on a slow connection.
    assert "pip install -r /workspace/pod-requirements.txt" in job_cmd
    assert "pip install -q" not in job_cmd


def test_build_job_cmd_never_shells_out_unsafely():
    # The command is a single string BY DESIGN (it becomes the pod's
    # dockerStartCmd), but the *driver's own* invocation of it must never go
    # through a local shell -- that's asserted in test_run_launcher_argv_shape.
    job_cmd = orchestrate.build_job_cmd(["x"], ["sd15"], "base")
    assert isinstance(job_cmd, str)


def test_build_video_job_cmd_embeds_files_recoverably():
    job_cmd = orchestrate.build_video_job_cmd(
        ["a cat"], "flux-schnell", "wan2.2-ti2v-5b", "ppics-video-test"
    )

    b64_blobs = [
        part.split("echo ", 1)[1].split(" | base64", 1)[0]
        for part in job_cmd.split(" && ")
        if part.startswith("echo ") and " | base64 -d > " in part
    ]
    assert len(b64_blobs) == 5
    decoded = [base64.b64decode(b).decode() for b in b64_blobs]
    assert "def main" in decoded[0]  # generate.py
    assert "def generate_video_one" in decoded[1]  # generate_video.py
    assert "BACKENDS" in decoded[2]  # config.py
    assert "diffusers" in decoded[3]  # pod-requirements-video.txt
    prompts_payload = json.loads(decoded[4])
    assert prompts_payload == {
        "prompts": ["a cat"],
        "image_model": "flux-schnell",
        "video_model": "wan2.2-ti2v-5b",
    }

    assert "python3 /workspace/generate_video.py" in job_cmd
    assert "--relay-code-base ppics-video-test" in job_cmd
    assert "pip install -q" not in job_cmd


def test_build_video_plan_rejects_unknown_image_model():
    with pytest.raises(ValueError, match="unknown model"):
        orchestrate.build_video_plan(["a prompt"], image_model_name="not-a-real-model")


def test_build_video_plan_rejects_unknown_video_model():
    with pytest.raises(ValueError, match="unknown video model"):
        orchestrate.build_video_plan(["a prompt"], video_model_name="not-a-real-video-model")


def test_build_video_plan_env_overrides_shape():
    plan = orchestrate.build_video_plan(
        ["a prompt"], "flux-schnell", "wan2.2-ti2v-5b", run_id="run-test-0004"
    )
    assert plan.run_id == "run-test-0004"
    assert plan.model_names == ["flux-schnell", "wan2.2-ti2v-5b"]
    assert plan.env_overrides["RP_POD_NAME"] == "ppics-video-run-test-0004"
    assert "RP_JOB_CMD" in plan.env_overrides


def test_execute_video_always_tears_down_even_if_relay_get_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "RUNS_DIR", tmp_path / "runs")
    plan = orchestrate.build_video_plan(
        ["a prompt"], "flux-schnell", "wan2.2-ti2v-5b", run_id="run-test-0005"
    )

    up_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="pod-abc\n", stderr="")
    logs_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="pod: booted\n", stderr=""
    )
    relay_fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    down_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    calls = []

    def fake_run_launcher(args, env):
        calls.append(args[0])
        if args[0] == "up":
            return up_result
        if args[0] == "logs":
            return logs_result
        if args[0] == "relay-get":
            return relay_fail
        if args[0] == "down":
            return down_result
        raise AssertionError(f"unexpected launcher subcommand {args}")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        rc = orchestrate.execute_video(plan, base_env={}, primitive_bin=Path("/bin/true"))

    assert rc == 1
    # down MUST run even though relay-get failed, and the boot check must
    # run before relay-get, not after
    assert calls == ["up", "logs", "relay-get", "down"]


def test_execute_video_runs_primitive_after_pull_and_returns_its_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "RUNS_DIR", tmp_path / "runs")
    plan = orchestrate.build_video_plan(
        ["a prompt", "another"], "flux-schnell", "wan2.2-ti2v-5b", run_id="run-test-0006"
    )

    results = {"up": 0, "logs": 0, "relay-get": 0, "down": 0}

    def fake_run_launcher(args, env):
        results[args[0]] += 1
        if args[0] == "up":
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="pod-xyz\n", stderr="")
        if args[0] == "logs":
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="pod: booted\n", stderr=""
            )
        if args[0] == "relay-get":
            # Simulate the pod having shipped back a real archive by writing
            # what unpacking it would produce directly -- exercises the
            # counting logic in execute_video, not tarfile/relay mechanics
            # (already covered by test_run_launcher_argv_shape).
            out_dir = plan.run_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "generation-manifest.json").write_text(
                json.dumps(
                    [
                        {"index": 0, "prompt": "a prompt", "error": None},
                        {"index": 1, "prompt": "another", "error": "still generation failed"},
                    ]
                )
            )
            (plan.run_dir / "ppics-output.tar.gz").write_bytes(b"")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    processed = manifest.VideoManifest(
        run_id="run-test-0006",
        items=[
            manifest.VideoItem(
                index=0, prompt="a prompt", image_model="f", video_model="w", status="ok"
            ),
            manifest.VideoItem(
                index=1, prompt="another", image_model="f", video_model="w", status="failed"
            ),
        ],
    )
    with (
        patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher),
        patch.object(orchestrate.tarfile, "open"),
        patch.object(orchestrate, "process_video_run", return_value=processed) as proc,
    ):
        rc = orchestrate.execute_video(plan, base_env={}, primitive_bin=Path("/bin/true"))

    assert rc == 0
    assert results == {"up": 1, "logs": 1, "relay-get": 1, "down": 1}
    proc.assert_called_once()  # the local primitive step runs after the pull, once per run


def test_build_plan_rejects_empty_prompts():
    with pytest.raises(ValueError, match="no prompts"):
        orchestrate.build_plan([], "sd15")


def test_build_plan_env_overrides_shape():
    plan = orchestrate.build_plan(["a prompt"], "compare", run_id="run-test-0001")
    assert plan.run_id == "run-test-0001"
    assert plan.model_names == ["sd15", "sdxl-turbo", "flux-schnell", "flux2-klein-4b"]
    assert plan.env_overrides["RP_COMPUTE_TYPE"] == "GPU"
    assert plan.env_overrides["RP_POD_NAME"] == "ppics-run-test-0001"
    assert "RP_JOB_CMD" in plan.env_overrides
    # `--model compare` downloads all 3 models' weights onto one pod --
    # launch.sh's own built-in default (20GB) isn't enough headroom for that.
    assert int(plan.env_overrides["RP_CONTAINER_DISK_GB"]) > 20
    # the secret must never be constructed into a plan value -- it only ever
    # travels through the process environment, untouched by this code.
    for value in plan.env_overrides.values():
        assert "RUNPOD_API_KEY" not in value


def test_build_plan_without_hf_token_has_no_pod_env_json():
    plan = orchestrate.build_plan(["a prompt"], "sd15", run_id="run-test-0002")
    assert "RP_POD_ENV_JSON" not in plan.env_overrides


def test_build_plan_with_hf_token_sets_pod_env_json():
    plan = orchestrate.build_plan(
        ["a prompt"], "flux-schnell", run_id="run-test-0003", hf_token="hf_fake_test_value"
    )
    assert json.loads(plan.env_overrides["RP_POD_ENV_JSON"]) == {"HF_TOKEN": "hf_fake_test_value"}


def test_dry_run_never_prints_the_pod_env_json_value(tmp_path, monkeypatch, capsys):
    """Exercises the real --dry-run code path in main(), not a re-implementation."""
    prompts_file = tmp_path / "prompts.txt"
    prompts_file.write_text("a prompt\n")
    monkeypatch.setenv("HF_TOKEN", "hf_should_never_appear")
    monkeypatch.setattr(orchestrate, "RUNS_DIR", tmp_path / "runs")
    with patch.object(orchestrate, "load_dotenv"):
        rc = orchestrate.main(
            ["--prompts", str(prompts_file), "--model", "flux-schnell", "--dry-run"]
        )
    out = capsys.readouterr().out
    assert rc == 0
    assert "hf_should_never_appear" not in out
    assert "RP_POD_ENV_JSON=<" in out
    assert "redacted" in out


def test_wait_for_boot_returns_true_as_soon_as_logs_appear():
    calls = []

    def fake_run_launcher(args, env):
        calls.append(args)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="pod: booted\n", stderr="")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        result = orchestrate._wait_for_boot({}, timeout_secs=999, sleep=lambda _: None)

    assert result is True
    assert calls == [["logs"]]  # returns on the FIRST sign of output, no polling needed


def test_wait_for_boot_returns_false_after_timeout_with_no_output():
    # A negative timeout guarantees the deadline is already in the past on
    # the first check, deterministically, without needing to mock time.time()
    # call-by-call (fragile -- coupled to exactly how many times the
    # implementation happens to call it).
    def fake_run_launcher(args, env):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        result = orchestrate._wait_for_boot(
            {}, timeout_secs=-1, poll_interval_secs=1, sleep=lambda _: None
        )

    assert result is False


def test_wait_for_boot_prints_heartbeat_while_waiting(capsys):
    attempts = {"n": 0}

    def fake_run_launcher(args, env):
        if args == ["logs"]:
            attempts["n"] += 1
            if attempts["n"] >= 3:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="pod: booted\n", stderr=""
                )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        result = orchestrate._wait_for_boot(
            {}, timeout_secs=999, poll_interval_secs=0, sleep=lambda _: None
        )

    assert result is True
    assert attempts["n"] == 3
    err = capsys.readouterr().err
    assert "waiting for pod boot" in err


def test_wait_for_boot_prints_system_source_diagnostic_on_timeout(capsys):
    calls = []

    def fake_run_launcher(args, env):
        calls.append(args)
        if args == ["logs", "--source", "system", "--tail", "50"]:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="system: pulling layer 3/8\n", stderr=""
            )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        result = orchestrate._wait_for_boot(
            {}, timeout_secs=-1, poll_interval_secs=1, sleep=lambda _: None
        )

    assert result is False
    assert ["logs", "--source", "system", "--tail", "50"] in calls
    assert "system: pulling layer 3/8" in capsys.readouterr().err


def test_run_launcher_argv_shape():
    """The driver must invoke launch.sh as an argv list, never shell=True."""
    with patch.object(subprocess, "run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="pod-123\n", stderr=""
        )
        orchestrate._run_launcher(["up"], {"FAKE": "env"})

    _, kwargs = mock_run.call_args
    args = mock_run.call_args.args[0]
    assert args[0] == "bash"
    assert args[-1] == "up"
    assert kwargs.get("shell", False) is False


def test_execute_always_tears_down_even_if_relay_get_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "RUNS_DIR", tmp_path / "runs")
    plan = orchestrate.build_plan(["a prompt"], "sd15", run_id="run-test-0002")

    up_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="pod-abc\n", stderr="")
    logs_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="pod: booted\n", stderr=""
    )
    relay_fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    down_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    calls = []

    def fake_run_launcher(args, env):
        calls.append(args[0])
        if args[0] == "up":
            return up_result
        if args[0] == "logs":
            return logs_result
        if args[0] == "relay-get":
            return relay_fail
        if args[0] == "down":
            return down_result
        raise AssertionError(f"unexpected launcher subcommand {args}")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        rc = orchestrate.execute(plan, base_env={}, primitive_bin=Path("/bin/true"))

    assert rc == 1
    assert calls == [
        "up",
        "logs",
        "relay-get",
        "down",
    ]  # down MUST run even though relay-get failed


def test_resolve_primitive_bin_missing_raises_clear_error():
    with pytest.raises(FileNotFoundError, match="make build"):
        orchestrate.resolve_primitive_bin("/not/a/real/path")


def test_resolve_primitive_bin_accepts_existing_path(tmp_path):
    fake_bin = tmp_path / "primitive"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    assert orchestrate.resolve_primitive_bin(str(fake_bin)) == fake_bin


def _write_fake_seed_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A 1x1 PNG is enough for `primitive` to load as a real image.
    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    path.write_bytes(png_1x1)


def test_process_run_mixed_success_and_failure(tmp_path):
    run_dir = tmp_path / "run-test"
    _write_fake_seed_image(run_dir / "out" / "sd15" / "000-a-cat.png")
    (run_dir / "out" / "sd15" / "001-broken.png").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "out" / "sd15" / "001-broken.png").write_bytes(b"not a real png")

    pod_manifest = [
        {"model": "sd15", "prompt": "a cat", "file": "sd15/000-a-cat.png", "seconds": 1.1},
        {"model": "sd15", "prompt": "broken", "file": "sd15/001-broken.png", "seconds": 0.9},
    ]
    (run_dir / "out" / "generation-manifest.json").write_text(json.dumps(pod_manifest))

    def fake_subprocess_run(cmd, **kwargs):
        # Simulate `primitive`: succeed for the real PNG, fail for the corrupt one.
        input_path = Path(cmd[cmd.index("-i") + 1])
        if b"PNG" in input_path.read_bytes()[:8]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="", stderr="unrecognized file extension"
        )

    with patch.object(subprocess, "run", side_effect=fake_subprocess_run):
        m = orchestrate.process_run(run_dir, Path("/fake/primitive"), shape_count=50)

    assert m.ok_count == 1
    assert m.failed_count == 1
    ok_item = next(i for i in m.items if i.status == "ok")
    failed_item = next(i for i in m.items if i.status == "failed")
    assert ok_item.primitive_png == "out/sd15/000-a-cat.primitive.png"
    assert failed_item.error == "unrecognized file extension"

    # process_run must have written the manifest to disk too, not just returned it.
    on_disk = manifest.read(run_dir / "manifest.json")
    assert on_disk == m


def test_process_run_handles_pod_side_model_load_failure(tmp_path):
    # Real incident (2026-09-27): one model failing to even load (a gated
    # HuggingFace repo, in that case) must not crash process_run or lose the
    # other models' results -- generate.py records file=None + an error for
    # every prompt under that model instead of a file path.
    run_dir = tmp_path / "run-test"
    _write_fake_seed_image(run_dir / "out" / "sd15" / "000-a-cat.png")
    (run_dir / "out").mkdir(parents=True, exist_ok=True)

    pod_manifest = [
        {"model": "sd15", "prompt": "a cat", "file": "sd15/000-a-cat.png", "seconds": 1.1},
        {
            "model": "flux-schnell",
            "prompt": "a cat",
            "file": None,
            "error": "GatedRepoError: 401 Client Error",
        },
    ]
    (run_dir / "out" / "generation-manifest.json").write_text(json.dumps(pod_manifest))

    def fake_subprocess_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=fake_subprocess_run):
        m = orchestrate.process_run(run_dir, Path("/fake/primitive"), shape_count=50)

    assert m.ok_count == 1
    assert m.failed_count == 1
    failed_item = next(i for i in m.items if i.status == "failed")
    assert failed_item.model == "flux-schnell"
    assert failed_item.error == "GatedRepoError: 401 Client Error"
    assert failed_item.primitive_png is None


def test_video_plan_defaults_to_the_video_pod_image_and_image_plan_keeps_its_own():
    video = orchestrate.build_video_plan(["a prompt"], run_id="run-test-img-1")
    image = orchestrate.build_plan(["a prompt"], "sd15", run_id="run-test-img-2")
    assert video.env_overrides["RP_POD_IMAGE"] == orchestrate.DEFAULT_VIDEO_POD_IMAGE
    assert image.env_overrides["RP_POD_IMAGE"] == orchestrate.DEFAULT_POD_IMAGE
    assert orchestrate.DEFAULT_VIDEO_POD_IMAGE != orchestrate.DEFAULT_POD_IMAGE


def test_dry_run_video_mode_uses_the_video_image_and_honours_an_explicit_override(
    tmp_path, monkeypatch, capsys
):
    prompts_file = tmp_path / "prompts.txt"
    prompts_file.write_text("a prompt\n")
    monkeypatch.setattr(orchestrate, "RUNS_DIR", tmp_path / "runs")
    with patch.object(orchestrate, "load_dotenv"):
        orchestrate.main(["--prompts", str(prompts_file), "--video", "--dry-run"])
        default_out = capsys.readouterr().out
        orchestrate.main(
            ["--prompts", str(prompts_file), "--video", "--dry-run", "--pod-image", "my/image:1"]
        )
        override_out = capsys.readouterr().out
    assert f"RP_POD_IMAGE={orchestrate.DEFAULT_VIDEO_POD_IMAGE}" in default_out
    assert "RP_POD_IMAGE=my/image:1" in override_out


def test_generate_video_one_asks_for_pil_frames_so_they_can_be_saved():
    # The pipeline defaults to numpy arrays (no .save()); the pod-side main() saves PNGs.
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, str(orchestrate.RUNPOD_DIR))
    from generate_video import generate_video_one

    seen = {}

    def fake_pipe(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(frames=[["frame-0", "frame-1"]])

    backend = SimpleNamespace(
        height=32, width=32, num_frames=5, num_inference_steps=2, guidance_scale=5.0
    )
    frames = generate_video_one(fake_pipe, backend, "still-image", "a prompt")

    assert seen["output_type"] == "pil"
    assert seen["image"] == "still-image"
    assert frames == ["frame-0", "frame-1"]
