# ADR-0008: Video source is a new RunPod job type running Wan2.2-TI2V-5B in image-to-video mode, chained from the existing still-image step

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian, Claude (planning session)

## Context

The phase 3 roadmap note left open whether "a generated video" means a new
text-to-video diffusion step or frame-by-frame reuse of the existing
image-diffusion pipeline, and which model, at what cost/latency. Published
2026 benchmarks for candidate open video models, at real RunPod on-demand GPU
pricing:

| Model | License | Params/VRAM | Benchmark | Est. cost/clip |
|-------|---------|--------------|-----------|-----------------|
| Wan2.2-TI2V-5B | Apache-2.0 | 5B, runs down to ~8GB (GGUF) up to ~24GB | ~170s for a 480p clip on an A100 | ≈ $0.07 at $1.59/hr |
| HunyuanVideo (13B) | Tencent community license (restrictive) | 13B, 80GB-class | ~45 min for one 5s 720p clip on an A100 | ≈ $1.19+ |
| LTX-2.3 | unclear at time of writing | 32GB (distilled) to 80GB (full) | native-audio, heavier | not estimated — VRAM tier alone rules it out as a first pick |

(See the grill-round message in this planning conversation for search sources.)

Separately, chaining independent per-frame image generation (img2img) to
simulate "a video" was considered and rejected: a real video-diffusion model is
trained specifically for temporal coherence between frames, where img2img
chaining is a pre-video-model workaround that drifts — it would fight exactly
the flicker problem this phase exists to address, at the source, before
`primitive` ever sees a frame.

## Decision

**Model:** Wan2.2-TI2V-5B — Apache-2.0 (matches this project's own license and
ADR-0001's established preference), ungated on Hugging Face as far as could be
determined without a live run, cheapest and fastest of the candidates by a wide
margin. Unlike phase 1's F1 (which ran a real 3-model comparison because SD1.5,
SDXL-Turbo and Flux.1-schnell were genuinely close on quality/cost), this
milestone does not repeat that bake-off — the cost/license gap between Wan2.2
and the alternatives is large enough on paper that a real comparison would
mostly spend money to confirm what the numbers already show. Still, slice 1
(SLICES.md V5) spends a small, pre-approved amount (~$1-5) to validate Wan2.2
actually runs end-to-end on RunPod — real GPU availability, real VRAM headroom,
and whether the repo turns out gated after all — mirroring the real surprises
ADR-0001 hit with Flux.1-schnell (HF gating, GPU OOMs, spot-pod removal) before
trusting a paper spec.

**Mode:** image-to-video (I2V), chained from the existing image-generation
step (Flux.1-schnell or, per ADR-0011, now SDXL-Turbo by default) rather than
text-to-video from nothing. Wan2.2-TI2V-5B supports both T2V and I2V from the
same checkpoint, so this is a mode flag, not an extra model dependency. I2V
reuses phase 1's already-tuned prompt bank and gives the warm-start mechanism
(ADR-0009) a real, `primitive`-compatible first frame to seed from, instead of
committing to a from-scratch video with no prior `primitive` pass to build on.

**Job shape:** a new one-shot batch job, following ADR-0001's precedent exactly
(pod boots once per run, dead-man's-switch armed first, driver-side `trap …
EXIT INT TERM`, spend-cap check before launch) — not a new architecture, an
additional model/job-command branch alongside the existing image job.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Frame-by-frame img2img chaining of the existing image pipeline | Doesn't solve temporal coherence at the source — a known-inferior pre-video-model workaround; would still need `primitive`'s warm-start to paper over drift the video model itself would have avoided |
| HunyuanVideo (13B) | ~17x the estimated cost per clip and a more restrictive license, for a resolution/quality tier this milestone's short validation clips don't need |
| A full 3-model bake-off, phase-1-style | The candidates aren't close enough on cost/license to justify spending real money to confirm it — unlike phase 1's image models, which were |
| Long-lived video-inference service pod | Same reasoning as ADR-0001: RunPod-as-request-path-dependency is explicitly discouraged, and a batch job amortizes pod boot time the same way |

## Consequences

- `pipeline/config.py` gains a separate `VideoBackend`/`VIDEO_BACKENDS`
  registry alongside the existing image `Backend`/`BACKENDS` — kept separate
  rather than forced into the same shape, since video generation takes a
  conditioning image and produces a frame sequence (`num_frames`/`fps`),
  which the image registry has no equivalent fields for.
- Slice 1 (SLICES-phase3.md V5) needs a small real-money validation run
  before later slices build against assumed cost/timing numbers; get
  explicit go-ahead before that run, per standing cost-safety practice.
- If Wan2.2 turns out gated (unverified until a real run), the `HF_TOKEN`
  plumbing ADR-0001 already built (`RP_POD_ENV_JSON`) is reused unchanged, not
  redesigned.
- Frames are saved directly from the video pipeline's own output
  (`output.frames[0]`, one PNG per frame) — no ffmpeg-based extraction step
  over an encoded clip. A viewable `.mp4` is exported separately (diffusers'
  `export_to_video` utility, which itself depends on `imageio[ffmpeg]`)
  purely for human inspection; if that export fails, the frames already
  saved are not lost. The video job's pod-side dependencies
  (`pod-requirements-video.txt`) are consequently a separate file from the
  image job's `pod-requirements.txt`, not a shared/merged one — the image
  job's `diffusers==0.31.0` pin predates Wan2.2 support entirely, and the
  two jobs' version needs are not yet known to be compatible.
- This locks in a specific model as the default; if Wan2.2 is later found
  wanting on quality (unlike cost, which is well-supported by benchmarks,
  visual quality wasn't validated by a real comparison here), revisiting the
  model choice touches only this ADR and `config.py`'s registry, not the
  warm-start mechanism (ADR-0009) or the Go-core changes (ADR-0007).
- **First real attempts, and what the dependency pins got wrong (2026-09-27
  to 2026-10-16, SLICES-phase3.md V5):** six launch attempts produced no
  generated video, but each failure taught something and none cost more than
  cents. Pods stuck at `awaiting_container` on an A40 twice (caught by the
  boot check, ADR-0001 addenda); an A100 was reclaimed by RunPod within
  seconds; one attempt died on a missing local prompt file; one was refused
  by the hourly cost cap ($1.79/hr against a $1.75 cap, terminated in
  seconds); and one pod booted, ran, then vanished with its logs — so the
  cause of that last crash was never observed directly (ADR-0001's addendum
  covers why logs are now kept). The likely cause, found afterwards by
  testing offline in a throwaway venv on CPU `torch==2.4.0` (the pod image's
  torch): the first `pod-requirements-video.txt` was a guess —
  `diffusers>=0.32,<0.35`, `transformers>=4.46` unbounded — and both halves
  were wrong. The Wan2.2 TI2V-5B checkpoint's `model_index.json` was written
  by diffusers 0.35.0.dev0 with `expand_timesteps: true`; 0.35.2 has
  `WanImageToVideoPipeline` with that argument, 0.34 and older do not and
  also fail to import against a current transformers. Unbounded
  `transformers` resolves to 5.x, which refuses to load PyTorch below 2.5, so
  the UMT5 text encoder could not have loaded on this image at all. Now
  pinned exactly: `diffusers==0.35.2`, `transformers==4.45.2` (the image
  job's known-good version), `accelerate==0.34.2`. Verified off-GPU: the
  imports, that diffusers 0.35.2 does not hit the old flash-attention
  registration failure on torch 2.4.0, that a `UMT5EncoderModel` builds, and
  that the checkpoint's VAE and transformer configs load. Not verified until
  a real pod runs it: inference itself and CUDA behaviour. The lesson for
  the next unfamiliar job type: test the dependency set offline against the
  pod image's torch before spending a pod on it.
- **Attempt 7 got further and exposed a torch version floor (2026-10-15):**
  with the corrected pins both models loaded on a real A100 (FLUX.1-schnell,
  then Wan2.2-TI2V-5B, so the dependency fix held), but still generation
  failed with `scaled_dot_product_attention() got an unexpected keyword
  argument 'enable_gqa'`. diffusers 0.35 passes `enable_gqa`, which exists
  only in torch 2.5 and newer, and the pod image has torch 2.4.0. The offline
  check had only imported diffusers; it never ran attention. Reproduced
  offline by running one real forward pass through a tiny randomly
  initialised `WanTransformer3DModel` (fails on CPU torch 2.4.0 with the same
  `TypeError`, passes on 2.8.0 with the pinned diffusers/transformers/
  accelerate). The pod's error reached the driver through the manifest and
  `pod.log` (ADR-0001's addendum), which is the first time a pod-side failure
  was fully diagnosable from local files. Fix: the video job now has its own
  `config.DEFAULT_VIDEO_POD_IMAGE`, `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-
  cudnn-devel-ubuntu22.04` (the only `runpod/pytorch` devel/ubuntu22.04 tag
  newer than 2.4.0 on Docker Hub on this date); the image job keeps its proven
  2.4.0 image. `--pod-image` still overrides either. Residual risk: that
  image is CUDA 12.8.1, so a pod on a host with an older NVIDIA driver may not
  start its container, which the 3-minute boot check would catch. A scripted
  edit swapping the two defaults was caught by a new test before any launch.
- **Attempt 8 reached the video step and found two more bugs; an offline
  end-to-end run then found them before a pod did (2026-10-15/16):** on the
  torch 2.8.0 image the still generated and the failure moved to
  `name 'ftfy' is not defined` — diffusers' Wan prompt cleaning calls
  `ftfy.fix_text()` unconditionally and does not depend on `ftfy`. Rather
  than find the next problem one paid pod at a time, the real
  `generate_video_one` plus `export_to_video` were run on CPU against a tiny
  randomly initialised `WanImageToVideoPipeline` (with the pod's bfloat16 /
  float32 dtype split). That reproduced the `ftfy` error exactly and found a
  second bug a pod could not yet have reached: the pipeline returns numpy
  arrays by default, so the pod-side `frame.save(...)` would have crashed
  after a successful, paid generation and discarded the video. Fixed with
  `output_type="pil"` (pinned by a unit test) and `ftfy==6.3.1`, with the
  imageio pair pinned to the tested versions. The check is now a permanent
  script, `pipeline/runpod/offline_video_smoke.py`, which also installs and
  runs from a clean venv built from `pod-requirements-video.txt` itself; it
  is referenced from `CLAUDE.md`. It cannot verify CUDA behaviour, memory or
  output quality, so a real pod run is still the acceptance test.
- **Validated on a real pod (2026-10-15, `runs/run-20261015-171744`, the
  eleventh launch attempt):** one prompt produced a 512×512 still, 41 frames
  at 832×480 and a 106 KB mp4, with the pod torn down and verified. Measured
  from the saved pod log's timestamps: container up in 2 s, dependency install
  7 s, FLUX.1-schnell download+load 3 m 48 s, Wan2.2 download+load about
  4 m 07 s, still + 41-frame video generation 33.8 s (50 denoising steps at
  about 1.9 it/s), artifact on disk 9 m after rental. The pod was an RTX PRO
  6000 Blackwell (96 GB) at $2.49/hr, so about $0.39 for the run. The
  original estimate (about $0.07 for a clip) covered generation only; the
  cost is dominated by downloading about 56 GB of weights on every run, not
  by generating. Across all eleven attempts the spend is roughly $1.2-1.5
  by my estimate from pod durations (not from the billing console, which is
  the authority). Output quality is good: the video is a coherent animation of
  the still, with the boat, its reflection and the sunrise sky consistent
  through the last frame, and frame 0 matches the still reframed to
  widescreen. The A100s were out of stock and priced above the old $1.75 cap
  ($1.79); the run used `--max-hourly-usd 2.6`. Both models are loaded at
  once, which needs about 56 GB and rules out 48 GB cards (an A40 at $0.49/hr
  would cost roughly a fifth as much); loading FLUX, generating the stills,
  freeing it, then loading Wan2.2 would lower the peak and widen the GPU
  choice, logged as a follow-up rather than built here. Still unverified:
  whether the Wan2.2 repo is gated (the HF token was present).

- **81-frame, 8-scene run (2026-10-16, `run-20261016-104959`):** `num_frames` raised
  from 41 (1.7 s at 24 fps) to 81 (3.4 s) after the first review found the clips too
  short. Eight prompts (`pipeline/prompts/video-scenes.txt`) in one pod: 8 ok, 0
  failed, about 70 s of generation per scene (41 frames took about 33 s), cost $0.50
  by the account balance ($8.49 to $7.99). An earlier 41-frame run of the same eight
  prompts cost $0.23 by the billing history. Slowed to 8 fps the clips play for
  about 5 s, which is the length that reviewed well.

- **Two-phase job: free FLUX before loading Wan2.2 (PRI-45, built 2026-10-17, not yet run
  on a real pod):** `generate_video.py` now generates every still first, drops the image
  pipeline (`gc.collect()` + `torch.cuda.empty_cache()`), and only then loads Wan2.2 and
  animates. Peak VRAM becomes the larger of the two models instead of their sum (about
  56 GB), so a 48 GB card such as the A40 ($0.49/hr against $2.49/hr for the RTX PRO 6000)
  should now fit. FLUX was seen OOM on 32 GB mid-inference (~31 GB used, see `config.py`),
  so 48 GB leaves headroom but not a wide margin. `offline_video_smoke.py` covers the
  ordering on CPU: it asserts the image pipeline is unreachable when the video model loads.
  That proves the order, not the memory. **Unverified:** that an A40 actually runs the
  whole job. It needs its own `RP_GPU_TYPE` and a cap near $0.6/hr, and a real pod run to
  confirm. Earlier A40 attempts stuck at `awaiting_container`, and stock has been patchy,
  so check availability first. One side effect: stills now exist for every prompt before any
  clip does, so a crash during the video phase still leaves all the stills in the output.
