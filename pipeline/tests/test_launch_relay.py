"""`launch.sh relay-get` against a fake `runpodctl` on PATH -- no network, no RunPod.

Covers the 2026-10-15 gap: a job that dies on the pod takes its logs with it, and
relay-get used to poll the resulting 404 until the full lifetime deadline without
keeping anything. Now it must save the last real logs and fail fast.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

LAUNCH_SH = Path(__file__).resolve().parent.parent / "runpod" / "launch.sh"

# Behaviour is driven by a counter file so the fake can change between polls.
FAKE_RUNPODCTL = """#!/usr/bin/env bash
state_dir="$FAKE_STATE_DIR"
case "$1 $2" in
  "pod logs")
    n=$(cat "$state_dir/polls" 2>/dev/null || echo 0)
    echo $((n + 1)) > "$state_dir/polls"
    if [ "$FAKE_MODE" = "gone-after-first" ] && [ "$n" -ge 1 ]; then
      echo '{"error":"pod not found","code":"not_found","status":404}'
      exit 1
    fi
    if [ "$FAKE_MODE" = "code-on-first" ]; then
      echo '{"source":"container","line":"pod: generating","ts":"t0"}'
      echo '{"source":"container","line":"RP_ARTIFACT_CODE=abc-123","ts":"t1"}'
      exit 0
    fi
    echo '{"source":"container","line":"pod: installing dependencies","ts":"t0"}'
    echo '{"source":"container","line":"ImportError: no Wan pipeline","ts":"t1"}'
    ;;
  "receive "*)
    echo "$2" > "$state_dir/received"
    ;;
esac
"""


def _run_relay_get(tmp_path: Path, mode: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "runpodctl"
    fake.write_text(FAKE_RUNPODCTL)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    state_dir = tmp_path / "fake-state"
    state_dir.mkdir()
    pod_state = tmp_path / "rp-state"
    pod_state.write_text("pod-test")
    dest = tmp_path / "run"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUNPOD_API_KEY": "fake-key-for-tests",
        # Refused instantly, so find_pod_id() falls back to the state file.
        "RP_API_BASE": "http://127.0.0.1:9",
        "RP_STATE_FILE": str(pod_state),
        "RP_MAX_LIFETIME_SECS": "60",
        "RP_RELAY_POLL_SECS": "0",
        "FAKE_STATE_DIR": str(state_dir),
        "FAKE_MODE": mode,
    }
    result = subprocess.run(
        ["bash", str(LAUNCH_SH), "relay-get", str(dest)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, dest, state_dir


def test_relay_get_fails_fast_and_keeps_logs_when_pod_disappears(tmp_path):
    result, dest, _ = _run_relay_get(tmp_path, "gone-after-first")

    assert result.returncode != 0
    assert "is gone" in result.stderr
    # the last real log lines are surfaced in the error itself...
    assert "ImportError: no Wan pipeline" in result.stderr
    # ...and the full capture survives on disk, not overwritten by the 404 reply
    saved = (dest / "pod.log").read_text()
    assert "ImportError: no Wan pipeline" in saved
    assert "not_found" not in saved


def test_relay_get_receives_when_code_appears(tmp_path):
    result, dest, state_dir = _run_relay_get(tmp_path, "code-on-first")

    assert result.returncode == 0, result.stderr
    assert (state_dir / "received").read_text().strip() == "abc-123"
    saved_lines = [json.loads(line) for line in (dest / "pod.log").read_text().splitlines()]
    assert saved_lines[-1]["line"] == "RP_ARTIFACT_CODE=abc-123"


FAKE_CURL = """#!/usr/bin/env bash
cat > /dev/null  # the real call feeds its auth header on stdin
if printf '%s ' "$@" | grep -q -- '-X POST'; then
  echo '{"error":"no instances currently available (token hf_secret_value)","status":500}'
  exit 22
fi
echo '[]'
"""


def test_up_shows_why_pod_create_failed_without_leaking_pod_env_values(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "curl"
    fake.write_text(FAKE_CURL)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUNPOD_API_KEY": "fake-key-for-tests",
        "RP_API_BASE": "http://example.invalid",
        "RP_STATE_FILE": str(tmp_path / "rp-state"),
        "RP_POD_IMAGE": "example/image:1",
        "RP_COMPUTE_TYPE": "GPU",
        "RP_GPU_TYPE": "NVIDIA A100 80GB PCIe",
        "RP_MAX_HOURLY_USD": "2",
        "RP_POD_ENV_JSON": json.dumps({"HF_TOKEN": "hf_secret_value"}),
    }
    result = subprocess.run(
        ["bash", str(LAUNCH_SH), "up"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert "no instances currently available" in result.stderr
    assert "hf_secret_value" not in result.stderr
    assert "<redacted>" in result.stderr
