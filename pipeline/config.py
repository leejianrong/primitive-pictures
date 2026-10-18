"""Model registry and RunPod defaults for the generation pipeline.

Nothing here is a secret. RUNPOD_API_KEY / ANTHROPIC_API_KEY come from the
environment only (pipeline.runpod.launch reads them by reference), never from
this file.

Resolution note (ADR-0001): each backend generates at its own practical
resolution, not a forced 256x256 -- `primitive` already resizes its input to
256px by default, so there's no reason to fight a model below its effective
training resolution to get there.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Backend:
    """One diffusion backend's model id + practical inference defaults.

    Steps/guidance/dtype here are the diffusers-documented defaults for each
    model as of this pipeline's initial build (2026-09). Diffusers' API and
    each model card's recommended settings do shift -- re-check
    huggingface.co/<model_id> before a real run if this code has aged.
    """

    name: str
    model_id: str
    pipeline_class: str  # diffusers class name, resolved in runpod/generate.py
    resolution: int
    steps: int
    guidance_scale: float
    dtype: str
    license: str


BACKENDS: dict[str, Backend] = {
    "sd15": Backend(
        name="sd15",
        model_id="runwayml/stable-diffusion-v1-5",
        pipeline_class="StableDiffusionPipeline",
        resolution=512,
        steps=25,
        guidance_scale=7.5,
        dtype="float16",
        license="CreativeML Open RAIL-M",
    ),
    "sdxl-turbo": Backend(
        name="sdxl-turbo",
        model_id="stabilityai/sdxl-turbo",
        pipeline_class="AutoPipelineForText2Image",
        resolution=512,
        steps=1,
        guidance_scale=0.0,
        dtype="float16",
        license="Stability AI Non-Commercial Research Community License",
    ),
    "flux-schnell": Backend(
        name="flux-schnell",
        model_id="black-forest-labs/FLUX.1-schnell",
        pipeline_class="FluxPipeline",
        resolution=512,
        steps=4,
        guidance_scale=0.0,
        dtype="bfloat16",
        license="Apache-2.0",
    ),
    # Added 2026-09-28. Newer generation from the same lab as flux-schnell,
    # so the same license trust carries over -- but note the 9B variant of
    # this model is released non-commercial; only the 4B is Apache-2.0.
    # Reference settings per BFL's model card: steps=4/guidance=1.0 for the
    # distilled checkpoint, height/width multiples of 16. Not yet run for
    # real on this project's own hardware/cost -- selectable via `--model`,
    # not the default, until a real run backs it up (same bar ADR-0001 set
    # for flux-schnell before it became the default).
    "flux2-klein-4b": Backend(
        name="flux2-klein-4b",
        model_id="black-forest-labs/FLUX.2-klein-4B",
        pipeline_class="Flux2KleinPipeline",
        resolution=1024,
        steps=4,
        guidance_scale=1.0,
        dtype="bfloat16",
        license="Apache-2.0",
    ),
}

ALL_BACKEND_NAMES = tuple(BACKENDS.keys())


@dataclass(frozen=True)
class VideoBackend:
    """One image-to-video backend's model id + practical inference defaults
    (phase 3, ADR-0008). Shaped differently from `Backend`: video generation
    takes a conditioning image (not just a prompt) and produces a frame
    sequence, not a single image -- `num_frames`/`fps` have no equivalent on
    the image side.

    height/width/num_frames/steps below are a deliberately short, low-cost
    starting point (docs/PLAN-phase3.md Q16), not a considered quality
    target -- unverified until SLICES-phase3.md V5's real validation run.
    """

    name: str
    model_id: str
    pipeline_class: str  # diffusers class name, resolved in runpod/generate_video.py
    height: int
    width: int
    num_frames: int  # Wan's temporal VAE expects 4k+1 frames
    fps: int
    num_inference_steps: int
    guidance_scale: float
    dtype: str
    vae_dtype: str  # Wan's own model card loads the VAE at higher precision than the transformer
    license: str


# GPU sizing for the video job: generate_video.py runs two phases (all FLUX stills, free FLUX, then
# Wan2.2), so peak VRAM is the larger model, not the 56 GB sum V5 needed. A 48 GB card (A40) should
# fit, at about a fifth of an 80 GB card's price, but that is unverified on a real pod (ADR-0008,
# PRI-45 addendum). It needs its own RP_GPU_TYPE and a lower --max-hourly-usd (about 0.6).
VIDEO_BACKENDS: dict[str, VideoBackend] = {
    # Apache-2.0, same license tier as flux-schnell. height/width/num_frames
    # here are the cheaper "480p-class" tier documented across Wan2.1/2.2
    # community benchmarks, not the model card's 720p reference example
    # (704x1280, 121 frames) -- deliberately smaller for a first validation
    # run's cost/time, per ADR-0008's slice-1 spend cap. num_inference_steps
    # (50) and guidance_scale (5.0) are the non-distilled checkpoint's
    # documented reference settings; the first things to reduce if V5's real
    # run is too slow/expensive.
    "wan2.2-ti2v-5b": VideoBackend(
        name="wan2.2-ti2v-5b",
        model_id="Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        pipeline_class="WanImageToVideoPipeline",
        height=480,
        width=832,
        num_frames=81,  # 3.4 s at 24 fps; 41 (1.7 s) validated 2026-10-15, 81 first run 2026-10-16
        fps=24,
        num_inference_steps=50,
        guidance_scale=5.0,
        dtype="bfloat16",
        vae_dtype="float32",
        license="Apache-2.0",
    ),
}

ALL_VIDEO_BACKEND_NAMES = tuple(VIDEO_BACKENDS.keys())

# 2026-09-27: chosen from a real 3-model comparison run (docs/SLICES.md V1
# steps 5-6; same 3 prompts, same run, all at each model's native
# resolution -- see runs/run-20260927-113021). All three produced usable
# `primitive` source material at negligible cost (9 images, all <2s each,
# well under $0.01 of GPU time total). Flux.1-schnell won that round on the
# combination of sharpest/most consistent composition and its Apache-2.0
# license -- the only one of the three with no commercial-use restriction,
# which matters if `bot/` (a separate, public-facing Twitter bot -- ADR-0002)
# ever consumes pipeline output: SDXL-Turbo's Non-Commercial Research license
# doesn't clearly permit that kind of public/commercial-adjacent use.
#
# 2026-09-28 (ADR-0011): briefly switched to SDXL-Turbo for its output
# quality, then reverted the same day after reviewing the `bot/` license
# conflict above -- see ADR-0011 for the full back-and-forth. Flux.1-schnell
# and SD1.5 remain selectable via `--model`.
DEFAULT_MODEL = "flux-schnell"

# Only one candidate registered so far (ADR-0008) -- no bake-off needed to
# call it the default.
DEFAULT_VIDEO_MODEL = "wan2.2-ti2v-5b"

# Verify this tag is still current at https://hub.docker.com/r/runpod/pytorch/tags
# before a real run -- RunPod's official images are updated frequently.
DEFAULT_POD_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"

# The video job needs torch >= 2.5: diffusers 0.35 (the first release with Wan2.2 TI2V support)
# calls scaled_dot_product_attention(enable_gqa=...), which torch 2.4.0 rejects -- found on a real
# pod 2026-10-15 and reproduced offline (see ADR-0008's addendum). 2.8.0 is the only
# `runpod/pytorch` devel/ubuntu22.04 tag newer than 2.4.0 that exists on Docker Hub as of this
# date; it is CUDA 12.8.1, so a pod landing on a host with an older NVIDIA driver could fail to
# start the container -- the boot check (orchestrate._wait_for_boot) would catch that in 3 min.
# The image job keeps DEFAULT_POD_IMAGE above: its diffusers==0.31.0 / torch 2.4.0 pairing is
# proven.
DEFAULT_VIDEO_POD_IMAGE = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"

# Conservative default; launch.sh (RP_MAX_HOURLY_USD) refuses to launch above this,
# and separately verifies the actual quoted cost after creation. Raise deliberately,
# not by habit.
#
# 1.75, not 0.80: RunPod discontinued spot pods (verified 2026-09-27 -- see
# launch.sh's RP_INTERRUPTIBLE comment). Worse, real Flux.1-schnell runs OOM'd
# on both a 24GB card (failed loading weights, ~22GB used) and a 32GB card
# (failed mid-inference, ~31.36 of 31.37GB used) -- 32GB is not safe headroom
# for this model. The A40 (48GB, the obvious next tier) has vanished from
# RunPod's catalog entirely as of this date ("no instances currently
# available" against every region). The next real tier with enough VRAM is an
# A100 80GB, quoted at $1.59/hr secure on 2026-09-27. Re-check pricing/stock
# before trusting this blindly if it's been a while -- this whole GPU
# landscape has shifted twice already within one session.
DEFAULT_MAX_HOURLY_USD = 1.75

# Generous enough for a model download + a handful of images; each real run should
# set this from the actual batch size rather than trust the default blindly.
DEFAULT_MAX_LIFETIME_SECS = 2400

# launch.sh's own default (RP_CONTAINER_DISK_GB) is 20GB, sized for a single small
# job -- nowhere near enough for `--model compare`, which downloads every
# registered model's weights onto one pod. Flux.1-schnell alone (bf16
# transformer + T5-XXL text encoder) is in the tens-of-GB range; combined with
# SD1.5, SDXL-Turbo, and flux2-klein-4b (added 2026-09-28; smaller at 4B params,
# but still real weight), 20GB fails partway through. 80GB gives real headroom
# for all four plus pip/package overhead -- re-verify against actual download
# sizes if `--model compare` starts failing on disk before a real run.
DEFAULT_CONTAINER_DISK_GB = 80


def resolve_backends(selection: str) -> list[Backend]:
    """`--model` CLI value -> list of Backends. 'compare' means all of them."""
    if selection == "compare":
        return list(BACKENDS.values())
    if selection not in BACKENDS:
        raise ValueError(
            f"unknown model {selection!r}; choose one of {ALL_BACKEND_NAMES} or 'compare'"
        )
    return [BACKENDS[selection]]


def resolve_video_backend(selection: str) -> VideoBackend:
    """`--video-model` CLI value -> one VideoBackend. No 'compare' -- unlike
    the image registry, there's only one candidate (ADR-0008)."""
    if selection not in VIDEO_BACKENDS:
        raise ValueError(
            f"unknown video model {selection!r}; choose one of {ALL_VIDEO_BACKEND_NAMES}"
        )
    return VIDEO_BACKENDS[selection]
