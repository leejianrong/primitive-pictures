import base64
import json
import subprocess
from unittest.mock import patch

import pytest

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
        rc = orchestrate.execute(plan, base_env={})

    assert rc == 1
    assert calls == ["up", "relay-get", "down"]  # down MUST run even though relay-get failed
