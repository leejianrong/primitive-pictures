#!/usr/bin/env python3
"""Pod-side batch image-to-video generation (phase 3, ADR-0008). Runs INSIDE
the RunPod pod, not locally.

For each prompt: generates a still image with the existing image backend
(reusing generate.py's `build_pipeline`/`generate_one`), then animates that
still with the registered video backend (Wan2.2-TI2V-5B I2V by default),
saves every output frame as its own PNG, and exports a viewable `.mp4` purely
for human inspection (diffusers' `export_to_video` utility, not a separate
frame-extraction step -- the frames themselves come directly from the video
pipeline's own output, not from decoding the exported clip).

This file is embedded verbatim into the pod's dockerStartCmd by
pipeline/orchestrate.py, the same way generate.py is (ADR-0001) -- nothing
fetched over the network at boot. It expects `config.py` and `generate.py`
alongside it in the same directory, embedded the same way, and imports
`build_pipeline`/`generate_one`/`slugify` from `generate` rather than
duplicating them.

Imports of torch/diffusers are deliberately deferred into main(), matching
generate.py's own reasoning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path


def build_video_pipeline(video_backend, dtype_map):
    import diffusers

    dtype = dtype_map[video_backend.dtype]
    vae_dtype = dtype_map[video_backend.vae_dtype]

    # Wan's own model card loads the VAE at higher precision than the
    # transformer -- unlike generate.py's image backends, which load
    # everything at one dtype via a plain from_pretrained(). Unverified
    # against a real pod; if this errors, the fallback is a plain
    # from_pretrained(model_id, torch_dtype=dtype) at the cost of some
    # quality, not a hard requirement.
    vae = diffusers.AutoencoderKLWan.from_pretrained(
        video_backend.model_id, subfolder="vae", torch_dtype=vae_dtype
    )
    pipeline_cls = getattr(diffusers, video_backend.pipeline_class)
    pipe = pipeline_cls.from_pretrained(video_backend.model_id, vae=vae, torch_dtype=dtype)
    pipe = pipe.to("cuda")
    return pipe


def generate_video_one(pipe, video_backend, still_image, prompt: str):
    """Returns a list of frames (PIL Images), directly from the pipeline's
    own output -- no separate frame-extraction step over an encoded clip."""
    output = pipe(
        image=still_image,
        prompt=prompt,
        height=video_backend.height,
        width=video_backend.width,
        num_frames=video_backend.num_frames,
        num_inference_steps=video_backend.num_inference_steps,
        guidance_scale=video_backend.guidance_scale,
        # Default is numpy arrays, which have no .save(); main() saves each frame as a PNG. Found by
        # running this function offline on a tiny pipeline -- a pod would only have hit it *after* a
        # successful generation, losing the video.
        output_type="pil",
    )
    return output.frames[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompts-file",
        required=True,
        help="JSON: {prompts: [...], image_model: str, video_model: str}",
    )
    parser.add_argument("--out-dir", default="/workspace/out")
    parser.add_argument("--relay-code-base", default="ppics-video")
    args = parser.parse_args()

    import torch

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from generate import build_pipeline, generate_one, slugify  # noqa: E402  (same)

    from config import BACKENDS, VIDEO_BACKENDS  # noqa: E402  (embedded alongside this file)

    dtype_map = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}

    payload = json.loads(Path(args.prompts_file).read_text())
    prompts: list[str] = payload["prompts"]
    image_backend = BACKENDS[payload["image_model"]]
    video_backend = VIDEO_BACKENDS[payload["video_model"]]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []

    print(f"pod: loading still-image backend {image_backend.model_id}", flush=True)
    image_pipe = build_pipeline(image_backend, dtype_map)
    print(f"pod: loading video backend {video_backend.model_id}", flush=True)
    video_pipe = build_video_pipeline(video_backend, dtype_map)

    from diffusers.utils import export_to_video

    for i, prompt in enumerate(prompts):
        item_dir = out_dir / f"{i:03d}-{slugify(prompt)}"
        item_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "index": i,
            "prompt": prompt,
            "image_model": image_backend.name,
            "video_model": video_backend.name,
            "still_file": None,
            "clip_file": None,
            "frame_files": [],
            "error": None,
        }

        t0 = time.time()
        try:
            still = generate_one(image_pipe, image_backend, prompt)
        except Exception as e:  # noqa: BLE001 -- one prompt's failure must not lose the rest
            print(f"pod: FAILED still [{i + 1}/{len(prompts)}]: {e}", flush=True)
            row["error"] = f"still generation failed: {e}"
            manifest_rows.append(row)
            continue
        still_rel = f"{item_dir.name}/still.png"
        still.save(out_dir / still_rel)
        row["still_file"] = still_rel

        try:
            frames = generate_video_one(video_pipe, video_backend, still, prompt)
        except Exception as e:  # noqa: BLE001 -- same isolation for the video step
            print(f"pod: FAILED video [{i + 1}/{len(prompts)}]: {e}", flush=True)
            row["error"] = f"video generation failed: {e}"
            manifest_rows.append(row)
            continue

        frame_files = []
        for f, frame in enumerate(frames):
            frame_rel = f"{item_dir.name}/frames/{f:04d}.png"
            (out_dir / item_dir.name / "frames").mkdir(parents=True, exist_ok=True)
            frame.save(out_dir / frame_rel)
            frame_files.append(frame_rel)
        row["frame_files"] = frame_files

        clip_rel = f"{item_dir.name}/clip.mp4"
        try:
            export_to_video(frames, str(out_dir / clip_rel), fps=video_backend.fps)
            row["clip_file"] = clip_rel
        except Exception as e:  # noqa: BLE001 -- clip export is for inspection only,
            # a failure here shouldn't lose the frames already saved above.
            print(f"pod: WARNING clip export failed [{i + 1}/{len(prompts)}]: {e}", flush=True)

        elapsed = time.time() - t0
        row["seconds"] = round(elapsed, 2)
        manifest_rows.append(row)
        print(
            f"pod: [{i + 1}/{len(prompts)}] {len(frame_files)} frames in {elapsed:.1f}s",
            flush=True,
        )

    del image_pipe, video_pipe
    torch.cuda.empty_cache()

    (out_dir / "generation-manifest.json").write_text(json.dumps(manifest_rows, indent=2))

    archive_path = out_dir.parent / "ppics-output.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(out_dir, arcname="out")

    # Same relay convention as generate.py (references/data-transport.md):
    # the driver's relay-get greps stdout for a literal RP_ARTIFACT_CODE=...
    # line.
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
