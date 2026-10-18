"""Offline smoke test of the pod-side video path, on CPU, with a tiny random model.

Run this BEFORE any paid video launch. It calls the real `generate_video_one` and diffusers'
`export_to_video` against a tiny randomly initialised Wan pipeline (real code path, nonsense
pixels), with the pod's dtype split (bfloat16 transformer/text encoder, float32 VAE). On
2026-10-16 it found two bugs that real pods had not yet reached or had hit one at a time:
a missing `ftfy` package, and frames coming back as numpy arrays that have no `.save()`.

It also runs the two-phase `run_batch` flow (all stills, free the image model, then load and run
Wan) and asserts the image pipeline is unreachable before the video model loads.

It does not need a GPU or the real 5B weights, only a throwaway venv that mirrors the pod:

    uv venv /tmp/video-smoke --python 3.11
    uv pip install --python /tmp/video-smoke/bin/python torch==2.8.0 \\
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python /tmp/video-smoke/bin/python \\
        -r pipeline/runpod/pod-requirements-video.txt
    /tmp/video-smoke/bin/python -I pipeline/runpod/offline_video_smoke.py

(`config.DEFAULT_VIDEO_POD_IMAGE` names the pod image's torch; keep the CPU torch version in step
with it.) It downloads only a tiny test tokenizer and text encoder. It cannot verify CUDA
behaviour, memory, or output quality -- only a real pod run can.
"""

import gc
import sys
import tempfile
import weakref
from pathlib import Path
from types import SimpleNamespace

import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diffusers import (  # noqa: E402
    AutoencoderKLWan,
    FlowMatchEulerDiscreteScheduler,
    WanImageToVideoPipeline,
    WanTransformer3DModel,
)
from diffusers.utils import export_to_video  # noqa: E402
from generate_video import generate_video_one, run_batch  # noqa: E402
from transformers import AutoTokenizer, T5EncoderModel  # noqa: E402

TINY_T5 = "hf-internal-testing/tiny-random-t5"


def build_tiny_pipeline() -> WanImageToVideoPipeline:
    torch.manual_seed(0)
    vae = AutoencoderKLWan(
        base_dim=3,
        z_dim=16,
        dim_mult=[1, 1, 1, 1],
        num_res_blocks=1,
        temperal_downsample=[False, True, True],
    )
    transformer = WanTransformer3DModel(
        patch_size=(1, 2, 2),
        num_attention_heads=2,
        attention_head_dim=12,
        in_channels=16,
        out_channels=16,
        text_dim=32,
        freq_dim=256,
        ffn_dim=32,
        num_layers=2,
        cross_attn_norm=True,
        qk_norm="rms_norm_across_heads",
        rope_max_seq_len=32,
    )
    pipe = WanImageToVideoPipeline(
        tokenizer=AutoTokenizer.from_pretrained(TINY_T5),
        text_encoder=T5EncoderModel.from_pretrained(TINY_T5),
        transformer=transformer,
        vae=vae,
        scheduler=FlowMatchEulerDiscreteScheduler(shift=5.0),
        image_encoder=None,
        image_processor=None,
        expand_timesteps=True,  # what the TI2V-5B checkpoint's model_index.json sets
    )
    # The pod's dtype split (generate_video.build_video_pipeline).
    pipe.transformer.to(torch.bfloat16)
    pipe.text_encoder.to(torch.bfloat16)
    pipe.vae.to(torch.float32)
    return pipe


class FakeImagePipe:
    """Stands in for FLUX: only its lifetime matters here."""


def check_two_phase_flow(video_pipe) -> None:
    """The image model must be unreachable before the video model loads (PRI-45), or FLUX and
    Wan2.2 sit in VRAM together (about 56GB) and 48GB cards fail. Runs the real `run_batch` with a
    stand-in image pipe and the tiny Wan pipeline; weakref + gc is the CPU stand-in for VRAM."""
    events: list[str] = []
    image_refs: list[weakref.ref] = []
    image_backend = SimpleNamespace(name="fake-image", model_id="fake/image")
    video_backend = SimpleNamespace(
        name="tiny-wan",
        model_id="tiny/wan",
        fps=8,
        height=32,
        width=32,
        num_frames=5,
        num_inference_steps=2,
        guidance_scale=5.0,
    )

    def build_image_pipe():
        pipe = FakeImagePipe()
        image_refs.append(weakref.ref(pipe))
        events.append("load-image")
        return pipe

    def build_video_pipe():
        # Loading the video model while the image model is still alive is the bug.
        gc.collect()
        assert image_refs[0]() is None, "image pipeline still alive when video model loads"
        events.append("load-video")
        return video_pipe

    def make_still(_pipe, _backend, _prompt):
        events.append("still")
        return Image.new("RGB", (64, 64), (120, 160, 200))

    def free_gpu():
        events.append("free")

    with tempfile.TemporaryDirectory() as tmp:
        rows = run_batch(
            ["a small wooden sailboat", "a red barn in a field"],
            image_backend,
            video_backend,
            Path(tmp),
            build_image_pipe=build_image_pipe,
            build_video_pipe=build_video_pipe,
            make_still=make_still,
            make_frames=generate_video_one,
            export_clip=export_to_video,
            slugify=lambda p: p.replace(" ", "-"),
            free_gpu=free_gpu,
        )
        assert [r["error"] for r in rows] == [None, None], rows
        assert all(len(r["frame_files"]) == 5 for r in rows), rows
        assert all((Path(tmp) / f).exists() for r in rows for f in r["frame_files"])

    assert events == ["load-image", "still", "still", "free", "load-video", "free"], events


def main() -> int:
    pipe = build_tiny_pipeline()
    backend = SimpleNamespace(
        height=32, width=32, num_frames=5, num_inference_steps=2, guidance_scale=5.0
    )
    still = Image.new("RGB", (64, 64), (120, 160, 200))

    frames = generate_video_one(pipe, backend, still, "a small wooden sailboat on a lake")
    assert len(frames) == backend.num_frames, f"expected 5 frames, got {len(frames)}"

    with tempfile.TemporaryDirectory() as tmp:
        for i, frame in enumerate(frames):
            frame.save(Path(tmp) / f"{i:04d}.png")  # numpy frames have no .save()
        clip = Path(tmp) / "clip.mp4"
        export_to_video(frames, str(clip), fps=24)
        assert clip.stat().st_size > 0, "mp4 export wrote an empty file"

    check_two_phase_flow(pipe)

    print(f"OK: {len(frames)} frames saved as PNG and exported to mp4; two-phase flow frees first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
