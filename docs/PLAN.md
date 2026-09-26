# Primitive Pictures — Phase 1: Plan

Status: agreed · Milestone: RunPod generation pipeline + pointillism mode

## Problem

Right now `primitive` only does one thing well: turn a single image you already
have into a primitive-art rendering, by hand, one CLI invocation at a time.
There's no way to (a) generate the *source* images with a modern model and feed
them straight through, in batch, or (b) get a different, more constrained
aesthetic (a stippled/pointillist look) out of the same engine. Both are wanted
as the seed of a much larger direction for this project (video, storyboarded
animation, eventually a trained draw-model) — but that whole direction is
speculative until these two pieces exist and work.

## Solution

Two independent additions:

1. A pipeline you point at a prompt file (or a theme + count, if you want the
   prompts generated for you) that comes back with a directory of primitive-art
   images, each traceable to the prompt and model that produced its seed image,
   for well under a dollar and with no pod left running afterward.
2. A new mode on the existing `primitive` CLI (`-m <n>`) that produces a
   pointillist/stippled rendering of any input image — no new binary, no
   pipeline dependency, just a flag.

## Users and actors

Primary: Jian, running this locally or via an agent acting on his behalf.
Non-human actor: an agent (Claude Code or similar) is expected to be the one
actually invoking the pipeline day-to-day. No multi-user/auth story — single
owner, single machine. (Q1)

## Scope

**In this milestone.**
- `pipeline/` Python package: prompt-gen (optional), RunPod batch-job diffusion
  generation across 3 candidate models, local `primitive` invocation per item,
  manifest tracking.
- A vendored RunPod cost-safety launcher (dead-man's-switch, spend cap,
  terminate-on-exit) — standalone, no dependency on a locally-installed Claude
  Code skill.
- A new pointillism mode in `primitive/` (Go core) and its CLI flag.
- Basic quality gates for the new Python code (lint + fast unit tests).

**Out.**
- Video/frame-by-frame processing, storyboarded multi-clip movies, and the
  trained CLIP-conditioned draw-model (ideas 3–5) — noted below as a roadmap,
  not built here.
- Any hosted UI, service, or multi-user access model.
- Fixing `bot/main.py`'s existing `shell=True` subprocess pattern — it's a
  separate, working component; only the *new* pipeline code is held to the
  stricter standard (ADR-0002).

## Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | Given a prompt file, produce a primitive-art rendering per prompt, unattended, with per-item manifest tracking and confirmed pod teardown | Core goal |
| R1 | RunPod batch job generates seed images across 3 candidate models (SDXL-Turbo, SD1.5, Flux.1-schnell), gated by cost safety | Must-have |
| R2 | `primitive` gets a pointillism mode: grid+jitter constrained shape placement | Must-have |
| R3 | Manifest captures per-item status (ok/failed+reason), model/params used, and a schema version | Must-have |
| R4 | Optional LLM step turns a theme + count into a prompt set | Nice-to-have |
| R5 | Lint + fast unit tests for the new Python pipeline package | Nice-to-have |

## Shape

| Part | Mechanism | ADR |
|------|-----------|-----|
| S1 | Pointillism candidate generator: new `-m` mode snaps random shape centers to a jittered grid cell; `Model.Step`/`Worker` otherwise unchanged | ADR-0003 |
| S2 | Vendored RunPod launcher (`pipeline/runpod/launch.sh`): spend-cap check, dead-man's-switch armed as the pod's first action, driver-side `trap … EXIT INT TERM` | ADR-0001 |
| S3 | Pod-side batch script: reads the full prompt set once, generates every image for the selected model(s), ships results back over the `runpodctl` relay, self-terminates | ADR-0001 |
| S4 | Python driver (`pipeline/run.py`): orchestrates prompt-gen (optional) → pod launch → relay pull → `primitive` subprocess per item → manifest write | ADR-0002, ADR-0004 |
| S5 | Prompt-gen module (`pipeline/promptgen.py`): theme + count → N short prompts via a hosted LLM API, isolated behind one function | ADR-0005 |
| S6 | Run/manifest data model: filesystem-addressed `runs/<run_id>/`, no database | ADR-0004 |

## Affordances

**Non-UI** (this is a CLI-only project — no UI surface).

| Affordance | Kind | Wires to |
|------------|------|----------|
| `primitive -m <pointillism-mode> ...` | CLI flag (existing binary, extended) | `primitive/` shape generator (S1) |
| `pipeline/run.py --prompts prompts.txt [--model sdxl-turbo\|sd15\|flux-schnell\|compare]` | CLI command | S2–S4, S6 |
| `pipeline/run.py --theme "..." --count N` | CLI command (alternative input) | S5, then same path as above |
| `runs/<run_id>/manifest.json` | Generated artifact | Read by the user; future phase-3 input |

## Implementation decisions

- `primitive/` changes are additive: a new `ShapeType` (or equivalent mode
  constant) plus new CLI flags for grid spacing / jitter radius, following the
  existing `-m`-numbered-mode convention documented in the README's flag table.
- `pipeline/` is a new top-level Python package, independent of `bot/`
  (ADR-0002). Structure: `pipeline/run.py` (CLI entry), `pipeline/promptgen.py`,
  `pipeline/manifest.py`, `pipeline/runpod/launch.sh` (driver-side launcher),
  `pipeline/runpod/generate.py` (pod-side script, one per model backend).
- All subprocess invocation of `primitive` uses argv-list `subprocess.run`,
  never `shell=True` (ADR-0002).
- Secrets (`RUNPOD_API_KEY`, `ANTHROPIC_API_KEY`) come from environment only;
  never logged, never written into the manifest.
- `runs/` is git-ignored, matching the project's existing generated-artifact
  convention.

## Testing approach

Two independent seams, tested at very different costs:

- **Pointillism (Go, no external dependency):** the grid+jitter constraint is a
  pure function of a fixed test image and RNG — verifiable in a fast unit test
  with no GPU, no network, folded into the existing `make test` gate.
- **Pipeline (Python, external dependencies by design):** manifest read/write,
  prompt-gen's own logic (LLM call mocked), and the `primitive` subprocess
  argv-construction (assert no `shell=True`, correct arguments) are all unit-
  testable with no network/GPU. The actual RunPod run — real cost, real
  secrets — is the slice 1 acceptance test, run manually/on demand, not wired
  into CI. (CI cost/secrets handling for this is deliberately deferred; see
  Open risks.)

## Assumed defaults

| ID | Assumed | Cost if wrong |
|----|---------|---------------|
| Q1 | Single-owner tool, no auth | Low — nothing here assumes otherwise structurally |
| Q4/Q9 | RunPod step is a one-shot batch job; project vendors its own safety launcher | Medium — reworking to a service shape would touch S2/S3 |
| Q8 | Prompt-gen uses a hosted LLM API, not RunPod | Low — isolated behind `promptgen.py`, swappable |
| Q13 | Pipeline driver is Python | High if wrong — but grounded in a checkable precedent (`bot/main.py`), not a guess |
| Q3/Q12 | Filesystem + JSON manifest, no DB, `schema_version` from day one | Low — additive if a DB is ever wanted later |

## Open risks

- **Diffusion output quality at cost-conscious resolution/step-count might not
  give `primitive` good source material.** Revealed by slice 1's model
  comparison — if all three look bad, that's the moment to reconsider before
  building the rest of the pipeline around them.
- **`runpodctl` relay transfer time/reliability for N images** is unverified at
  this batch size. Revealed by slice 1's actual timing.
- **Vendored launcher drifting from the runpod-jobs skill's own safety recipe**
  as that skill evolves (ADR-0001, accepted risk).
- **No CI coverage for the real RunPod path** (costs money, needs secrets) —
  acceptable for phase 1 given experiment-scale usage, but means regressions in
  the pod-side script are only caught by manual runs.

## Roadmap (not planned in this pass)

Captured so it isn't lost, not committed to:

- **Phase 3 — Video coherence.** Frame-by-frame `primitive` processing of a
  generated video, with warm-started shape search between frames to avoid
  flicker.
- **Phase 4 — Storyboarded movies.** LLM-driven storyboarding → per-shot clips
  (phase 3) → stitched into a longer animation.
- **Phase 5 — Trained draw-model.** Distill an (image, primitive-shape-sequence)
  dataset at scale (this pipeline, run against an existing image dataset
  instead of diffusion output), then train a CLIP-conditioned transformer that
  emits primitive draw commands directly from a text prompt — a long-horizon
  research bet, sequenced after the tooling here is cheap and fast at scale.
