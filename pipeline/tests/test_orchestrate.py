import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import manifest
import orchestrate


def test_build_job_cmd_embeds_files_recoverably():
    job_cmd = orchestrate.build_job_cmd(["a cat"], ["sd15"], "ppics-test")

    # every base64 blob in the command must decode to real content, and the
    # prompts payload must round-trip exactly -- this is the whole mechanism
    # ADR-0001 relies on (nothing fetched over the network at boot).
    b64_blobs = [
        part.split("echo ", 1)[1].split(" | base64", 1)[0]
        for part in job_cmd.split(" && ")
        if part.startswith("echo ")
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


def test_build_job_cmd_never_shells_out_unsafely():
    # The command is a single string BY DESIGN (it becomes the pod's
    # dockerStartCmd), but the *driver's own* invocation of it must never go
    # through a local shell -- that's asserted in test_run_launcher_argv_shape.
    job_cmd = orchestrate.build_job_cmd(["x"], ["sd15"], "base")
    assert isinstance(job_cmd, str)


def test_build_plan_rejects_empty_prompts():
    with pytest.raises(ValueError, match="no prompts"):
        orchestrate.build_plan([], "sd15")


def test_build_plan_env_overrides_shape():
    plan = orchestrate.build_plan(["a prompt"], "compare", run_id="run-test-0001")
    assert plan.run_id == "run-test-0001"
    assert plan.model_names == ["sd15", "sdxl-turbo", "flux-schnell"]
    assert plan.env_overrides["RP_COMPUTE_TYPE"] == "GPU"
    assert plan.env_overrides["RP_POD_NAME"] == "ppics-run-test-0001"
    assert "RP_JOB_CMD" in plan.env_overrides
    # the secret must never be constructed into a plan value -- it only ever
    # travels through the process environment, untouched by this code.
    for value in plan.env_overrides.values():
        assert "RUNPOD_API_KEY" not in value


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


def test_execute_always_tears_down_even_if_relay_get_fails():
    plan = orchestrate.build_plan(["a prompt"], "sd15", run_id="run-test-0002")

    up_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="pod-abc\n", stderr="")
    relay_fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    down_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    calls = []

    def fake_run_launcher(args, env):
        calls.append(args[0])
        if args[0] == "up":
            return up_result
        if args[0] == "relay-get":
            return relay_fail
        if args[0] == "down":
            return down_result
        raise AssertionError(f"unexpected launcher subcommand {args}")

    with patch.object(orchestrate, "_run_launcher", side_effect=fake_run_launcher):
        rc = orchestrate.execute(plan, base_env={}, primitive_bin=Path("/bin/true"))

    assert rc == 1
    assert calls == ["up", "relay-get", "down"]  # down MUST run even though relay-get failed


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
