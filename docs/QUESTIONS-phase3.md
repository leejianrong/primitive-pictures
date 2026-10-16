# Questions — Phase 3 (Video coherence)

Scope: turning phase 1's roadmap placeholder ("frame-by-frame `primitive`
processing of a generated video, with warm-started shape search between
frames to avoid flicker") into a real plan. Phase 4 (storyboarded movies) and
phase 5 (trained draw-model) are out of scope here; carried only as a roadmap
note in PLAN-phase3.md. `bot/` is unrelated (ADR-0002 precedent).

Statuses: `DECIDED` (user answered) · `ASSUMED` (default taken, correct it if
wrong) · `FORK` (waiting on the user) · `DEFERRED` (not needed this
milestone).

## Open forks

_(empty — round closed)_

## Register

| ID | Question | Status | Answer or default | Landed |
|----|----------|--------|-------------------|--------|
| Q1 | Who drives this — human, agent, both? | ASSUMED | Same as phase 1: Jian or an agent acting for him, no auth/multi-tenant | PLAN-phase3 §Users |
| Q2 | Scope boundary for this milestone | ASSUMED | In: video-gen job, frame saving, warm-start Go core, pipeline wiring, continuity metric. Out: phase 4/5, `bot/`, active refinement, flicker-suppression objective, a video-model bake-off | PLAN-phase3 §Scope |
| Q3 | Data model/identity for a video run | ASSUMED | Extends ADR-0004's run/item model: frame items nested under a run, addressed by frame index, `schema_version` bumped | PLAN-phase3 §Shape |
| Q4 | Where does data physically live | ASSUMED | Same `runs/` (git-ignored) filesystem convention; the still, the per-frame PNGs, and a viewable `.mp4` (for inspection only) all stored under the run dir | PLAN-phase3 §Shape |
| Q5 | Concurrency: two writers? | ASSUMED | Same as phase 1: one pipeline invocation at a time, no locking | PLAN-phase3 §Shape |
| Q6 | How does frame-to-frame shape state cross the process boundary between pipeline and `primitive`? | ASSUMED (load-bearing) | It doesn't — frame-sequence processing is one in-process Go invocation per video run, not one subprocess per frame with serialized state | ADR-0007 |
| Q7 | Failure behaviour on a bad frame or a crash mid-run | ASSUMED | File-presence-based: each frame's output is written incrementally; `--resume` skips already-completed frames on a rerun, not a manifest-status-driven retry like phase 1's | PLAN-phase3 §Assumed defaults |
| Q8 | Which video-diffusion model, and by comparison or by research? | DECIDED | Wan2.2-TI2V-5B, chosen from published cost/license/speed research, not a real bake-off (unlike phase 1's F1) — the gap to the alternatives checked was wide enough on paper | ADR-0008 |
| Q9 | Default image-generation model | DECIDED | Briefly switched to SDXL-Turbo for output quality, reverted the same day once the `bot/` license conflict was concrete — stays Flux.1-schnell | ADR-0011 |
| Q10 | Runtime shape for the video job: service or batch job; cost caps | ASSUMED | One-shot batch job, same as ADR-0001's precedent; small pre-approved spend cap (~$1-5) for slice 1's validation run, explicit go-ahead required first | ADR-0008 |
| Q11 | Measurable success for warm-start / how to know it worked | DECIDED | A shape-level continuity metric (carried/added shape fractions), used as an observability/tuning aid — not a pass/fail gate, since the user is fine with some flicker | ADR-0010 |
| Q12 | Secrets/gating: is Wan2.2 gated like Flux.1-schnell was? | ASSUMED | Assumed ungated; unverified until slice 1's real run. If wrong, reuses ADR-0001's existing `HF_TOKEN`/`RP_POD_ENV_JSON` plumbing unchanged | PLAN-phase3 §Implementation decisions |
| Q13 | Manifest versioning for the new frame-item schema | ASSUMED | Additive extension of ADR-0004's schema, `schema_version` bumped — exactly what that field was added for | PLAN-phase3 §Shape |
| Q14 | Warm-start depth: static carry-forward, or active refinement of already-placed shapes? | DECIDED | Static carry-forward — cheapest build, matches the user's stated tolerance for flicker; active refinement deferred (D1) | ADR-0009 |
| Q15 | Sequencing: I2V chained from phase 1's image step, or standalone T2V? | DECIDED | I2V, chained from the existing image step — reuses the tuned prompt bank and gives warm-start a real first frame to seed from | ADR-0008 |
| Q16 | Default clip length/frame count for slice 1 | ASSUMED | Short — on the order of a couple of seconds / a few dozen frames, to bound cost while the mechanism is validated; a tunable parameter, not architecture | PLAN-phase3 §Assumed defaults |
| Q17 | Default `new-shapes-per-frame` budget | ASSUMED | A modest default (a small fraction of the total per-frame shape count); user-facing tuning parameter by design | PLAN-phase3 §Assumed defaults |

## Deferred

| ID | Question | Why it can wait |
|----|----------|------------------|
| D1 | Active refinement of already-placed shapes | Explicitly deferred per ADR-0009 — logged as future work (Pandan card), revisit only if static carry-forward's output or the continuity metric shows a concrete problem worth the added `Model`/`Worker` surface |
| D2 | A temporal-loss term that actively suppresses flicker | The user does not want flicker suppressed, only visible/tunable (ADR-0010) — out of scope, not just this milestone but as a stated non-goal |
| D3 | Phase 4 (storyboarded movies), Phase 5 (trained draw-model) | Out of this milestone; roadmap note only in PLAN-phase3.md |
| D4 | `bot/` integration with phase 3 output | Unrelated, separate component (ADR-0002); not assumed without a real decision |

## Coverage

| Category | Covered by |
|----------|-----------|
| Primary user and actors | Q1 |
| Scope boundary | Q2 |
| Data model and identity | Q3 |
| State and storage | Q4 |
| Concurrency and conflict | Q5 |
| Interfaces and contracts | Q6 |
| Failure behaviour | Q7 |
| External dependencies | Q8, Q9, Q12 |
| Runtime and deployment | Q10 |
| Measurable success | Q11 |
| Security and secrets | Q12 |
| Versioning and migration | Q13 |
| Domain: warm-start mechanism | Q14 |
| Domain: video/image pipeline sequencing | Q15, Q16, Q17 |
