# Primitive Pictures — Phase 3: Plan

Status: agreed · Milestone: Video coherence

## Problem

Phase 1 gave `primitive` a batch pipeline for *still* images: prompt in, one
primitive-art rendering out. The phase 1 roadmap left a single placeholder
sentence for phase 3 — "frame-by-frame `primitive` processing of a generated
video, with warm-started shape search between frames to avoid flicker" —
without settling what produces the video, what "warm-started" means
mechanically, or where the seam between the Go core and the Python pipeline
falls. None of that is derivable from the sentence alone, and naively running
today's per-image algorithm on each frame of a video independently would
flicker badly: every frame's shape search is randomized from scratch, so even
a static region of the picture gets a completely different, unrelated set of
shapes each frame.

## Solution

A pipeline command that takes a prompt, generates a short coherent video clip
(starting from a still image via the existing image step, then animating it),
and comes back with a directory of frame-by-frame primitive renderings whose
shapes visibly persist from one frame to the next — largely-unchanged regions
keep the same shapes; new content is expressed as new shapes layered in, at a
rate the user controls. A per-run report shows how much continuity was
achieved, so the flicker/dynamism tradeoff can be tuned to taste rather than
guessed at — flicker itself is not treated as a defect to eliminate.

## Users and actors

Same as phase 1 (Q1 there, unchanged): Jian, or an agent acting on his behalf.
No auth, no multi-tenant story.

## Scope

**In this milestone.**
- A new RunPod job type: image-to-video generation via Wan2.2-TI2V-5B,
  chained from the existing still-image step (ADR-0008).
- Pod-side frame saving: the video pipeline's own output frames, saved
  directly (no separate extraction step over an encoded clip).
- `primitive/` Go core: a new in-process frame-sequence entrypoint with
  static carry-forward warm start between frames (ADR-0007, ADR-0009).
- `pipeline/` wiring to drive that entrypoint over a relayed frame sequence,
  and an extended manifest schema for a video run.
- A shape-level continuity metric, reported per run as a tuning aid
  (ADR-0010) — not an optimization objective, not a quality gate.
- A small, pre-approved real RunPod spend (~$1-5) to validate the new job
  type end-to-end, the same way phase 1's V1 proved its riskiest mechanism
  for real before building the rest around it.

**Out.**
- Phase 4 (storyboarded movies) and phase 5 (trained draw-model) — still just
  roadmap notes, untouched by this milestone.
- Active refinement of already-placed shapes (a touch-up pass reoptimizing
  inherited shapes against a new frame) — deferred, logged as future work
  (ADR-0009, QUESTIONS-phase3.md D1), not built.
- Any mechanism that actively suppresses flicker (a temporal-loss term in the
  hill-climb objective) — the continuity metric measures flicker, nothing
  enforces a target; the user considers some flicker an acceptable aesthetic.
- `bot/` integration — separate, unrelated component (ADR-0002 precedent);
  this phase's output is not assumed to feed it.
- A hosted UI, real-time/interactive playback, or any multi-user access model
  — still a CLI/pipeline-only project.
- A repeat of phase 1's 3-way model bake-off for the video step — Wan2.2 is
  committed to on paper research (ADR-0008); no comparison step is built.

## Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | Given a prompt, produce a short video's worth of frame-by-frame primitive renderings, unattended, with per-frame manifest tracking and confirmed pod teardown | Core goal |
| R1 | New RunPod job generates a short video clip via Wan2.2-TI2V-5B I2V, chained from a still image, gated by cost safety | Must-have |
| R2 | `primitive/` gets an in-process frame-sequence mode: static carry-forward warm start across frames, existing hill-climb loop unchanged | Must-have |
| R3 | Manifest extended to a per-frame item schema (frame index, status, continuity stats), `schema_version` bumped | Must-have |
| R4 | A shape-level continuity metric reported per run, exposing the new-shapes-per-frame budget as a tunable dial | Must-have |
| R5 | Small real end-to-end RunPod validation run (video-gen + frame saving + warm-started primitive pass), spend capped and pre-approved | Must-have |

## Shape

| Part | Mechanism | ADR |
|------|-----------|-----|
| S1 | New RunPod job type: pod generates an I2V clip via Wan2.2-TI2V-5B from a supplied still, saves the pipeline's own output frames directly (plus a viewable `.mp4` for inspection), ships back over the existing `runpodctl` relay | ADR-0008 |
| S2 | `primitive/` in-process frame-sequence entrypoint: seeds each frame's `Model` from the previous frame's finished shapes (drawn onto `Current`), runs existing `Model.Step` for a capped new-shape budget, writes each frame's output incrementally (`--resume`-capable) | ADR-0007, ADR-0009 |
| S3 | Shape-continuity metric: pure Go function over two consecutive shape lists → carried/added fractions, surfaced per-frame and as a run-level summary | ADR-0010 |
| S4 | `pipeline/`'s video mode: builds the video-gen job command, pulls frames back, invokes S2 once per run (not once per frame), writes the extended manifest | ADR-0008, extends ADR-0004 |

## Affordances

**Non-UI** (still a CLI-only project — no UI surface).

| Affordance | Kind | Wires to |
|------------|------|----------|
| `primitive --frames <dir> --out <dir> -n <total> --new-shapes-per-frame <n> [--resume]` | CLI flag (existing binary, new mode) | S2, S3 |
| `pipeline/orchestrate.py --video --prompts ... [--model wan2.2-ti2v-5b]` | CLI command | S1, S4 |
| `pipeline/orchestrate.py --video --process-only RUN_DIR` | CLI command (local iteration, no spend) | S4 |
| `runs/<run_id>/manifest.json` (extended schema) | Generated artifact | S3, S4 |

## Implementation decisions

- All `primitive/` changes are additive: one new entrypoint (ADR-0007), no
  changes to `Model.Step`, `Worker`, or the `Shape` interface's signatures —
  other Go programs importing this package see no breaking change.
- The frame-sequence entrypoint keeps the whole run in one process and one
  memory space; no shape-serialization format is introduced (ADR-0007).
- `new-shapes-per-frame` is a real user-facing tuning parameter, not a value
  the system computes automatically — it is, in effect, the continuity/
  flicker dial (ADR-0009).
- Secrets discipline is unchanged from phase 1: `RUNPOD_API_KEY`/`HF_TOKEN`
  from environment only, never logged, never in the manifest. Wan2.2 is
  assumed ungated pending slice 1's real validation run — if it turns out
  gated, ADR-0001's existing `HF_TOKEN`/`RP_POD_ENV_JSON` plumbing is reused
  unchanged, not redesigned.
- `runs/` keeps its existing git-ignored, filesystem-addressed convention
  (ADR-0004); the video-run manifest is an additive schema extension,
  `schema_version` bumped, exactly what ADR-0004 designed room for.
- All subprocess invocation (of `primitive`, and the pod-side `runpodctl
  send` relay call) stays argv-list, never `shell=True` (ADR-0002,
  unchanged).

## Testing approach

Two seams, same split as phase 1:

- **Go core (no external dependency):** carry-forward seeding (does frame N+1
  start from exactly frame N's shapes before any new shape is added?) and the
  continuity metric's arithmetic are pure functions over fixed fixtures —
  fast, no GPU, folded into `make test`.
- **Pipeline (external dependencies by design):** the video job-command/argv
  construction, the extended manifest's round-trip, and frame-count
  bookkeeping are unit-testable with no network/GPU, same as phase 1's V1-V2.
  The real RunPod video run — real cost, real secrets — is slice 1's
  acceptance test, run manually/on demand with explicit go-ahead, not wired
  into CI (same deferral phase 1 made, still accepted here).

## Assumed defaults

| ID | Assumed | Cost if wrong |
|----|---------|---------------|
| Q7 | Frame-run failure recovery is file-presence-based (`--resume` skips completed frames), not manifest-status-driven like phase 1's per-item retries | Low — additive; a manifest-driven retry could be layered on later without touching the seeding mechanism |
| Q12 | Wan2.2-TI2V-5B weights are ungated on Hugging Face | Low — if wrong, reuses ADR-0001's existing `HF_TOKEN` wiring, already built |
| Q13 | Video-run manifest is an additive extension of ADR-0004's schema (frame items nested under a run), not a new data model | Low — this is exactly what `schema_version` was added for |
| Q16 | Slice 1's target clip is short (on the order of a couple of seconds / a few dozen frames) to bound cost while the mechanism is validated | Low — a CLI-tunable parameter, not an architectural choice |
| Q17 | `new-shapes-per-frame` starts at a modest default (a small fraction of the total per-frame shape budget) | Low — user-facing tuning parameter by design (ADR-0009) |

## Open risks

- **Wan2.2 might turn out gated or harder to run than published benchmarks
  suggest** (Flux.1-schnell's HF-gating and GPU-availability surprises in
  ADR-0001 are the direct precedent) — revealed by slice 1 (SLICES-phase3.md
  V5)'s real validation run.
- **Static carry-forward might look visibly worse than acceptable**, beyond
  the flicker the user is fine with — e.g. genuinely broken continuity, not
  just stylistic flicker. Revealed by V6/V7's real output; active refinement
  (ADR-0009) is the documented next step if so, not ruled out.
- **The video job's dependency set is verified offline but not on a GPU.**
  It needs a newer `diffusers` than the image job's 0.31.0, tracked in a
  separate `pod-requirements-video.txt`. The first guessed range was wrong
  (ADR-0008's addendum); the exact pins now in place (`diffusers==0.35.2`,
  `transformers==4.45.2`) were tested on CPU torch 2.4.0, the pod image's
  version. Inference itself and CUDA behaviour are unverified until a real V5
  run completes.
- **Clip length/frame-count defaults are drawn from published benchmarks, not
  this project's own measurement** — revealed by V5's real timing, same as
  phase 1's diffusion-resolution assumption was revealed by its own slice 1.

## Roadmap (not planned in this pass)

Captured so it isn't lost, not committed to:

- **Active refinement.** A bounded touch-up pass that re-optimizes already-
  placed shapes against a new frame, instead of only ever adding new ones
  (ADR-0009's deferred alternative). Revisit if static carry-forward's output,
  or the continuity metric built to observe it, shows a concrete problem
  worth the added `Model`/`Worker` surface.
- **Phase 4 — Storyboarded movies.** LLM-driven storyboarding → per-shot clips
  (this phase) → stitched into a longer animation.
- **Phase 5 — Trained draw-model.** Distill an (image, primitive-shape-
  sequence) dataset at scale, then train a CLIP-conditioned transformer that
  emits primitive draw commands directly from a text prompt.
