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
}

ALL_BACKEND_NAMES = tuple(BACKENDS.keys())

# Verify this tag is still current at https://hub.docker.com/r/runpod/pytorch/tags
# before a real run -- RunPod's official images are updated frequently.
DEFAULT_POD_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"

# Conservative default; launch.sh (RP_MAX_HOURLY_USD) refuses to launch above this,
# and separately verifies the actual quoted cost after creation. Raise deliberately,
# not by habit.
#
# 0.80, not 0.60: RunPod discontinued spot pods (verified 2026-09-27 -- see
# launch.sh's RP_INTERRUPTIBLE comment), so this now has to clear on-demand
# pricing for a GPU with enough VRAM for Flux.1-schnell. RTX PRO 4500 Blackwell
# (32GB) real-quoted at $0.72/hr on 2026-09-27; re-check pricing before trusting
# this blindly if it's been a while.
DEFAULT_MAX_HOURLY_USD = 0.80

# Generous enough for a model download + a handful of images; each real run should
# set this from the actual batch size rather than trust the default blindly.
DEFAULT_MAX_LIFETIME_SECS = 2400

# launch.sh's own default (RP_CONTAINER_DISK_GB) is 20GB, sized for a single small
# job -- nowhere near enough for `--model compare`, which downloads all three
# models' weights onto one pod. Flux.1-schnell alone (bf16 transformer + T5-XXL
# text encoder) is in the tens-of-GB range; combined with SD1.5 and SDXL-Turbo,
# 20GB fails partway through. 80GB gives real headroom for all three plus
# pip/package overhead.
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
