#!/usr/bin/env python3
"""Pod-side batch image generation. Runs INSIDE the RunPod pod, not locally.

Reads a prompt/model-selection payload, generates one image per (prompt, backend)
pair, tars the results, and ships them back to the driver over the runpodctl relay
(no public IP required -- see the runpod-jobs skill's data-transport reference).

This file is embedded verbatim into the pod's dockerStartCmd by
pipeline/orchestrate.py (never fetched over the network at boot -- see ADR-0001
and the runpod-jobs skill's warning about network steps ahead of the
dead-man's-switch). It expects `config.py` (the model registry) alongside it in
the same directory, embedded the same way.

Imports of torch/diffusers are deliberately deferred into main() so that
`--help` and argument-parsing errors don't require the (large, slow-to-import)
ML stack to be installed first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path


def build_pipeline(backend, dtype_map):
    import diffusers

    dtype = dtype_map[backend.dtype]
    pipeline_cls = getattr(diffusers, backend.pipeline_class)
    pipe = pipeline_cls.from_pretrained(backend.model_id, torch_dtype=dtype)
    pipe = pipe.to("cuda")
    return pipe


def generate_one(pipe, backend, prompt: str):
    kwargs = {
        "prompt": prompt,
        "num_inference_steps": backend.steps,
        "guidance_scale": backend.guidance_scale,
        "height": backend.resolution,
        "width": backend.resolution,
    }
    result = pipe(**kwargs)
    return result.images[0]


def slugify(text: str, max_len: int = 40) -> str:
    keep = [c if c.isalnum() else "-" for c in text.lower()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:max_len] or "prompt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompts-file", required=True, help="JSON: {prompts: [...], models: [...]}"
    )
    parser.add_argument("--out-dir", default="/workspace/out")
    parser.add_argument("--relay-code-base", default="ppics")
    args = parser.parse_args()

    # Deferred imports: keep --help cheap, and fail with a clear message if the
    # pod image is missing the ML stack rather than an opaque ImportError deep
    # inside argument parsing.
    import torch

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import BACKENDS  # noqa: E402  (embedded alongside this file)

    dtype_map = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}

    payload = json.loads(Path(args.prompts_file).read_text())
    prompts: list[str] = payload["prompts"]
    model_names: list[str] = payload["models"]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []

    for model_name in model_names:
        backend = BACKENDS[model_name]
        print(f"pod: loading {backend.model_id} ({model_name})", flush=True)
        t0 = time.time()
        pipe = build_pipeline(backend, dtype_map)
        print(f"pod: loaded {model_name} in {time.time() - t0:.1f}s", flush=True)

        model_dir = out_dir / model_name
        model_dir.mkdir(parents=True, exist_ok=True)

        for i, prompt in enumerate(prompts):
            t0 = time.time()
            image = generate_one(pipe, backend, prompt)
            elapsed = time.time() - t0
            filename = f"{i:03d}-{slugify(prompt)}.png"
            image.save(model_dir / filename)
            manifest_rows.append(
                {
                    "model": model_name,
                    "prompt": prompt,
                    "file": f"{model_name}/{filename}",
                    "seconds": round(elapsed, 2),
                }
            )
            print(f"pod: [{model_name}] {i + 1}/{len(prompts)} in {elapsed:.1f}s", flush=True)

        del pipe
        torch.cuda.empty_cache()

    (out_dir / "generation-manifest.json").write_text(json.dumps(manifest_rows, indent=2))

    archive_path = out_dir.parent / "ppics-output.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(out_dir, arcname="out")

    # Send the archive back over the relay. Follows the runpod-jobs skill's
    # documented convention exactly (references/data-transport.md): the code
    # `runpodctl send` prints looks like "<base>-<digits>"; the driver's
    # `relay-get` greps stdout for a literal `RP_ARTIFACT_CODE=...` line, so we
    # must emit one, not invent our own log format.
    import re

    send_log = out_dir.parent / "send.log"
    with open(send_log, "w") as f:
        proc = subprocess.Popen(
            ["runpodctl", "send", "--code", args.relay_code_base, str(archive_path)],
            stdout=f,
            stderr=subprocess.STDOUT,
        )
    code_pattern = re.compile(rf"{re.escape(args.relay_code_base)}[-0-9]+")
    code = None
    deadline = time.time() + 60
    while time.time() < deadline:
        text = send_log.read_text()
        match = code_pattern.search(text)
        if match:
            code = match.group(0)
            break
        time.sleep(1)
    if code is None:
        print("pod: WARNING: no relay code seen within 60s; dumping send.log", flush=True)
        print(send_log.read_text(), flush=True)
        return 1
    print(f"RP_ARTIFACT_CODE={code}", flush=True)
    proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
