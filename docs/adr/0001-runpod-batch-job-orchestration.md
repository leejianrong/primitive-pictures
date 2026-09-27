# ADR-0001: RunPod diffusion step runs as a vendored, self-contained batch job

- Status: Accepted
- Date: 2026-09-26
- Deciders: Jian, Claude (planning session)

## Context

Phase 1 needs a step that turns a batch of text prompts into seed images using a
diffusion model, on rented GPU compute, without leaking a bill (RunPod has no
native idle-terminate, no max-lifetime, and no per-pod spend cap — see the
runpod-jobs skill). Two shape questions and one model question were open:

1. Does the pod run as a long-lived service (HTTP inference server) the driver
   calls per-prompt, or as a one-shot batch job that processes the whole prompt
   set and exits?
2. Does the project depend on the `runpod-jobs` Claude Code skill's local
   `scripts/rp` wrapper, or carry its own safety logic?
3. Which diffusion model, and at what resolution?

On (3), the user's answer was to compare rather than pick on paper: SDXL-Turbo,
SD1.5, and Flux.1-schnell (SDXL full dropped — too many GPU-seconds per image for
a "keep it cheap" budget), with the resolution note that 256×256 photos are fine.

## Decision

**Shape:** the RunPod step is a one-shot batch job, not a service. The pod boots
once per pipeline run (not once per prompt), a script on the pod reads the entire
prompt file, generates every image, ships them all back over the `runpodctl`
relay (no public IP assumed — see the runpod-jobs skill's data-transport
reference), and the pod self-terminates. This matches the skill's explicit
guidance against using RunPod as a request-path dependency, and it amortizes pod
boot time (image pull, model load) across the whole batch instead of paying it
per prompt.

**Ownership:** the project vendors its own cost-safety launcher
(`pipeline/runpod/launch.sh`, adapted from the runpod-jobs skill's `scripts/rp`)
rather than depending on `~/.claude/skills/runpod-jobs/scripts/rp`. That path only
exists inside a Claude Code session with this skill installed; anyone else who
clones this repo (a CI job, a future contributor, Jian on a different machine)
needs the pipeline to work standalone. The vendored launcher keeps the same
non-negotiables: a hard max-lifetime + idle-watchdog dead-man's-switch armed as
the literal first thing the pod's start command runs, an account spend-cap
check before launch, and a driver-side `trap … EXIT INT TERM` that terminates
the pod on success, failure, or Ctrl-C.

**Model selection:** slice 1 does not hardcode one model. It runs the same small
prompt set through all three candidates (SDXL-Turbo, SD1.5, Flux.1-schnell) via a
`--model` parameter on the same batch-job script, and the choice of default is
made from the real cost/quality/speed tradeoff produced by that comparison, not
from paper specs. This is cheap to do (the harness — pod launch, prompt loop,
relay transport — is identical across all three; only the model-loading branch
differs), and it directly answers the open fork with evidence instead of a guess.

**Resolution:** generate at each model's practical native/economical resolution
(typically 512×512-class for SD1.5/SDXL-Turbo, native for Flux.1-schnell) rather
than literally forcing 256×256 output. Pixel-diffusion models trained at a given
resolution tend to produce structurally broken images well below it, and there's
no cost or quality reason to fight that: `primitive` already resizes its input to
256px by default (the `-r` flag, currently defaulted in `main.go`), so the
256×256 requirement is satisfied by that existing downsizing step, not by asking
the diffusion model to natively output tiny images. This is a deliberate,
flagged deviation from the literal ask — the underlying goal (don't pay for or
need high-resolution photos) is preserved; the mechanism achieving it moved to
where it already lived in the codebase.

**Licenses of the three candidates** (check before picking a default, not
after):

| Model | License | Note |
|-------|---------|------|
| SDXL-Turbo | Stability AI Non-Commercial Research Community License | Research/personal use only; commercial use needs a separate Stability AI license |
| SD1.5 | CreativeML Open RAIL-M | Broadly permissive, including commercial use, subject to the RAIL-M usage restrictions (no illegal/harmful content generation) |
| Flux.1-schnell | Apache-2.0 | Fully permissive — matches this project's own license |

This project's own code is Apache-2.0; a model whose license restricts
commercial/redistribution use doesn't change the *code's* license, but it does
constrain what can honestly be claimed about outputs produced with it if this
project or its outputs are ever shared more broadly. Not a blocker for
experiment-scale personal use of any of the three, but the license column is a
real input to slice 1's model choice, not just quality/cost.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Long-lived inference service pod (AUTOMATIC1111/ComfyUI API), driver calls it per prompt | Matches the runpod-jobs skill's explicitly discouraged shape (RunPod as a request-path dependency); pod stays up and bills between calls; more moving parts (HTTP server, proxy quirks) for no benefit at this batch size |
| Depend on the runpod-jobs skill's `scripts/rp` directly | Not present outside a Claude Code session with this skill installed; makes the pipeline non-standalone |
| Force literal 256×256 diffusion output | Below most models' effective training resolution; produces visibly broken images for no cost savings once `primitive`'s existing resize is accounted for |
| Lock in one model now (e.g. SDXL-Turbo) without comparing | Faster to plan, but the user explicitly asked to compare; picking blind risks committing to a worse cost/quality point when a three-way comparison costs barely more than a one-way test |

## Consequences

- Slice 1's build plan is larger than "call one model" — it needs a
  `--model` switch and a small side-by-side comparison step before the default is
  locked (tracked in SLICES.md V1).
- The vendored launcher duplicates logic that already exists in the runpod-jobs
  skill; it must be kept in sync by hand if that skill's safety recipe changes.
  Accepted: standalone-usability outweighs that maintenance cost here.
- Diffusion generation resolution is decoupled from `primitive`'s input
  resolution by design — if `primitive`'s default resize value ever changes, the
  pipeline's cost assumptions should be revisited, not just the flag value.
- **Container disk sizing (2026-09-27 addendum):** `--model compare`
  downloads all three models' weights onto one pod. `launch.sh`'s own
  built-in default (`RP_CONTAINER_DISK_GB`, 20GB) is sized for a single small
  job, not this — Flux.1-schnell alone (bf16 transformer + T5-XXL text
  encoder) is in the tens-of-GB range. `config.DEFAULT_CONTAINER_DISK_GB` is
  80GB, threaded through as its own `--container-disk-gb` flag rather than
  left to `launch.sh`'s default. Caught by comparing against another
  project's RunPod `.env.example` that happened to size its own disk
  explicitly — worth double-checking against real download sizes before
  the first live run, since this is still an estimate, not a measurement.
- **Spot pods discontinued; GPU type list needs fallbacks (2026-09-27
  addendum, from the first real launch attempt):** `launch.sh` defaulted to
  `interruptible: true` (spot); RunPod's pod-create endpoint now 500s that
  with `"Spot pods are no longer offered"` — confirmed by posting the exact
  create body directly. Default flipped to on-demand (`false`) in
  `launch.sh`. Separately, `runpodctl gpu list`'s `gpuId` values aren't all
  valid for pod creation — a MIG variant that `gpu list` shows 400s at
  create time; the endpoint's actual enum only surfaces in a 400's error
  body. GPU availability also shifts hour to hour, so `RP_GPU_TYPE` should
  always carry 2-3 comma-separated fallback ids, not one. `.env.example`'s
  default cost cap moved 0.60 → 0.80 to clear real on-demand pricing for a
  GPU with enough VRAM for Flux.1-schnell (RTX PRO 4500 Blackwell, 32GB,
  quoted $0.72/hr secure on this date). Verified end-to-end: a real pod was
  created (id, cost, and dead-man's-switch-free dockerStartCmd all
  confirmed via the REST response), then terminated and confirmed gone via
  `runpodctl pod list` — total spend for the check was a few seconds at
  $0.72/hr.
- **Flux.1-schnell needs HF authentication; generate.py isolates per-model
  failures (2026-09-27 addendum, from the real 3-model comparison run):**
  SD1.5 and SDXL-Turbo both succeeded end-to-end on the first real
  production run. Flux.1-schnell failed with `huggingface_hub.errors.
  GatedRepoError: 401` — it's a gated HF repo requiring (1) accepting its
  license at huggingface.co and (2) an access token. Neither is wired up
  yet: `launch.sh`'s pod-create body has no `env` field, so there's
  currently no way to hand a token into the container. Separately, this run
  exposed a real robustness bug: `generate.py`'s tar-and-send step only ran
  after its full model loop, so Flux's crash lost SD1.5 and SDXL-Turbo's
  already-generated images too — nothing was ever shipped back. Fixed by
  isolating both model-load and per-prompt generation in try/except inside
  the loop; a failed model now gets `file: None` + its error recorded per
  prompt, and every other model's results still ship. `orchestrate.py`'s
  `process_run` updated to handle a `None` file (no seed image, so no
  `primitive` invocation — the pod's error carries straight through).
  Verified: intentionally broke the new `None`-handling branch and
  confirmed the test fails with a clear `TypeError` before re-fixing it.
