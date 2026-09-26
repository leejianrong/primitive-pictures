# Questions — Phase 1 (RunPod pipeline + pointillism mode)

Scope: (1) a batch pipeline — optional LLM prompt-gen → RunPod diffusion image-gen →
local `primitive` processing — and (2) a new "pointillism" placement mode in the Go
core. Ideas 3–5 (video, storyboarded movies, trained draw-model) are out of scope
here; carried only as a roadmap note in PLAN.md.

Statuses: `DECIDED` (user answered) · `ASSUMED` (default taken, correct it if wrong)
· `FORK` (waiting on the user) · `DEFERRED` (not needed this milestone).

## Open forks

_(empty — round closed)_

## Register

| ID | Question | Status | Answer or default | Landed |
|----|----------|--------|-------------------|--------|
| Q1 | Who drives this — human, agent, both? | ASSUMED | Single-owner tool; Jian or an agent acting for him, no auth/multi-tenant | PLAN §Users |
| Q2 | Scope boundary for this milestone | ASSUMED | In: pointillism mode + `pipeline/` batch driver. Out: video/animation, storyboarded movies, trained draw-model, hosted UI, low-latency serving | PLAN §Scope |
| Q3 | Core data model / identity for a pipeline run | ASSUMED | `Run` (id=`run-<timestamp>`) of N `Item`s, filesystem-addressed under `runs/<run_id>/<index>-<slug>/`, no DB | ADR-0004 |
| Q4 | Where does data physically live; what crosses the pod boundary | ASSUMED | Local `runs/` (git-ignored); RunPod pod is a one-shot batch job, images return over the `runpodctl` relay; `primitive` always runs locally | ADR-0001 |
| Q5 | Concurrency: two writers? | ASSUMED | One pipeline invocation at a time, no locking needed | PLAN §Shape |
| Q6 | Interfaces: single write path? | ASSUMED | One CLI entrypoint, argv-list subprocess calls to `primitive` (not `shell=True`) | ADR-0002 |
| Q7 | Failure behaviour on a bad item | ASSUMED | Partial-result-with-gaps-flagged; batch continues past one item's failure; manifest records per-item status | PLAN §Shape |
| Q8 | Prompt-gen LLM: hosted API or RunPod-hosted OSS model | ASSUMED | Hosted API (Anthropic Claude, e.g. Haiku) — a GPU pod for seconds of text generation wastes the cheap-budget with no upside | ADR-0005 |
| Q9 | Runtime shape: service pod or batch job; whose safety wrapper | ASSUMED | One-shot batch job (boot once per run, not per prompt); project vendors its own cost-safety launcher rather than depending on the runpod-jobs skill's local path | ADR-0001 |
| Q10 | Measurable success, checkably | ASSUMED | Pointillism: shape centers grid-aligned within a jitter bound (unit test). Pipeline: 5-prompt run completes unattended, 5 manifest entries, pod confirmed terminated, spend under $0.50 | PLAN §Testing |
| Q11 | Secrets handling | ASSUMED | `RUNPOD_API_KEY`/`ANTHROPIC_API_KEY` from env only, never logged; `runs/` git-ignored; new driver must not repeat `bot/main.py`'s `shell=True` pattern | ADR-0002 |
| Q12 | Manifest versioning | ASSUMED | `schema_version` field from day one | PLAN §Shape |
| Q13 | Pipeline driver language | ASSUMED | Python — `bot/main.py:120-129` already shells out to the compiled `primitive` binary via `subprocess`, and RunPod/diffusers/LLM SDKs are Python-first. New `pipeline/` dir, not reusing `bot/` | ADR-0002 |
| F1 | Which diffusion model(s) for the RunPod batch job? | DECIDED | Compare SDXL-Turbo, SD1.5, and Flux.1-schnell as part of slice 1 (not decided on paper); generate at each model's practical native resolution rather than forcing 256×256 output, since `primitive` already resizes input to 256 by default — see ADR-0001's resolution note. SDXL (full) dropped, too costly for the stated budget | ADR-0001, SLICES V1 |
| F2 | How does pointillism integrate with the existing hill-climb loop? | DECIDED | Constrained-random: reuse `Model.Step`/`Worker` unchanged; the new mode's random shape generator snaps candidate centers to a jittered grid cell | ADR-0003 |

## Deferred

| ID | Question | Why it can wait |
|----|----------|------------------|
| D1 | Exact GPU flavor for the diffusion pod | Tune during slice implementation via the runpod-jobs skill's sizing reference; one env var to change later |
| D2 | >1 image per prompt (seed variations) | Ship 1:1 prompt→image for phase 1; N-per-prompt is additive later |
| D3 | Ideas 3 (video), 4 (storyboarded movies), 5 (trained draw-model) | Out of this milestone; roadmap note only in PLAN.md |

## Coverage

| Category | Covered by |
|----------|-----------|
| Primary user and actors | Q1 |
| Scope boundary | Q2 |
| Data model and identity | Q3 |
| State and storage | Q4 |
| Concurrency and conflict | Q5 |
| Interfaces and contracts | Q6, Q13 |
| Failure behaviour | Q7 |
| External dependencies | Q8, F1 |
| Runtime and deployment | Q9 |
| Measurable success | Q10 |
| Security and secrets | Q11 |
| Versioning and migration | Q12 |
| Domain: pointillism mechanism | F2 |
