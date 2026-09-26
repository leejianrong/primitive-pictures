# ADR-0003: Pointillism mode is a constrained-random extension of the existing hill-climb loop

- Status: Accepted
- Date: 2026-09-26
- Deciders: Jian, Claude (planning session)

## Context

`primitive`'s existing algorithm (`primitive/model.go`, `worker.go`) picks one
shape per `Model.Step()` call by generating random candidate shapes across the
whole canvas, hill-climbing each, and keeping the best-scoring one. Shape
constructors (e.g. random circle/ellipse placement) already take a `*Worker`
carrying canvas dimensions and an RNG, so centers are sampled uniformly across
`[0,W) x [0,H)` today.

A pointillism/stipple mode wants shapes placed on a regular-ish grid instead of
freely, and the question was how deep that goes: keep the existing per-step
global competitive search and just constrain where candidates can land, or
replace it with a different loop that walks grid cells directly.

## Decision

Constrained-random: add a new mode (`ShapeTypeStipple` or similar, next `-m`
number after the existing 0–8) whose random shape generator snaps candidate
centers to the nearest cell of a jittered grid (grid spacing and jitter radius
as new CLI-exposed parameters) instead of sampling anywhere on canvas. Radius,
color and alpha stay under the existing hill-climb search. `Model.Step`, the
worker pool, and scoring are otherwise untouched — the constraint lives entirely
in the candidate generator for this mode.

This does not guarantee every cell is visited exactly once (nothing prevents the
competitive search from favoring the same well-scoring cell twice), but for a
stippled aesthetic that's not a visible defect — overlapping dots are normal in
pointillism — and it ships the smallest change that gets a real grid-aligned
look. If the visual result under-covers or clusters more than acceptable, the
deterministic grid-walk alternative (below) is the documented next step; it is
not being ruled out, just deferred until there's a concrete reason from real
output.

## Alternatives considered

| Option | Why not (for this milestone) |
|--------|-------------------------------|
| Deterministic grid walk: iterate every cell once (shuffled order), local search per cell for size/color/alpha only | Guarantees complete, non-overlapping coverage — the more "literal" stippling algorithm — but needs a new entry point alongside `Model.Step` (e.g. `StepAt`) and a cell-tracking structure; more implementation work for a first version, and only clearly worth it if constrained-random's output looks under-covered in practice |

## Consequences

- Smallest possible diff against the existing algorithm: no changes to
  `Model.Step`, `Worker`, or scoring; the new mode is additive at the shape
  constructor / CLI-flag layer.
- Grid coverage is probabilistic, not guaranteed. Acceptable per the reasoning
  above; revisit only if real test-image output looks wrong.
- Grid spacing and jitter become new tunable parameters (mirroring the existing
  `-n`/`-a`/etc. flag pattern), which is straightforward CLI surface growth but
  is more flags for users to learn.
- The success test from QUESTIONS.md Q10 (shape centers grid-aligned within a
  jitter bound) is checkable without a GPU or the pipeline — a pure Go unit test
  against a fixed test image, independent of phase 1's RunPod work.
