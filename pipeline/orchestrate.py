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

from config import (
    DEFAULT_MAX_HOURLY_USD,
    DEFAULT_MAX_LIFETIME_SECS,
    DEFAULT_POD_IMAGE,
    resolve_backends,
)

PIPELINE_DIR = Path(__file__).resolve().parent
RUNPOD_DIR = PIPELINE_DIR / "runpod"
REPO_ROOT = PIPELINE_DIR.parent
RUNS_DIR = REPO_ROOT / "runs"


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
        "pip install -q -r /workspace/pod-requirements.txt",
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


def execute(plan: LaunchPlan, base_env: dict[str, str]) -> int:
    """Run the plan for real: up -> relay-get -> unpack, always down in finally."""
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
        return 0
    finally:
        down = _run_launcher(["down"], env)
        print(down.stderr, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", required=True, help="path to a newline-delimited prompt file")
    parser.add_argument(
        "--model",
        default="compare",
        help="backend name, or 'compare' to run all of them (default)",
    )
    parser.add_argument("--max-hourly-usd", type=float, default=DEFAULT_MAX_HOURLY_USD)
    parser.add_argument("--max-lifetime-secs", type=int, default=DEFAULT_MAX_LIFETIME_SECS)
    parser.add_argument("--pod-image", default=DEFAULT_POD_IMAGE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and print the launch plan (job command, env) without spending anything",
    )
    args = parser.parse_args(argv)

    prompts = [line.strip() for line in Path(args.prompts).read_text().splitlines() if line.strip()]
    plan = build_plan(
        prompts,
        args.model,
        max_hourly_usd=args.max_hourly_usd,
        max_lifetime_secs=args.max_lifetime_secs,
        pod_image=args.pod_image,
    )

    if args.dry_run:
        print(f"run_id: {plan.run_id}")
        print(f"models: {plan.model_names}")
        print(f"prompts: {len(plan.prompts)}")
        print("env overrides:")
        for k, v in plan.env_overrides.items():
            shown = v if k != "RP_JOB_CMD" else f"<{len(v)} chars, see below>"
            print(f"  {k}={shown}")
        print("--- RP_JOB_CMD ---")
        print(plan.job_cmd)
        return 0

    import os

    return execute(plan, dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
