# ADR-0012: Active refinement re-optimises a bounded set of carried shapes, behind a flag

- Status: Accepted 2026-10-19 (Jian approved the recommendations below)
- Date: 2026-10-19
- Deciders: Jian, Claude

## Context

ADR-0009 chose static carry-forward for frame sequences and deferred active
refinement until real output showed a concrete problem. It has: when a subject
moves, the shapes that fitted its old position stay in later frames, because
carried shapes are never looked at again. We call this ghosting. The random
drop rate (default 0.3) eventually removes ghosts, but blindly, so a ghost
survives on average a few frames while a perfectly good background shape is
just as likely to go.

Flicker is still a tunable aesthetic and not something to suppress (ADR-0009,
ADR-0010). Refinement is about the opposite problem: stale shapes that are
wrong for the new frame, not shapes that changed too often.

## Decision

Add `Model.Refine(shapeType ShapeType, alpha, k, age int) int`, which
re-optimises up to `k` already-placed shapes against the current target and
returns how many actually changed. `Model.Step` is not touched. It is off by
default: `SequenceConfig.RefinePerFrame = 0` and a `-refine K` flag (plus
`-refine-age`, default 100 hill-climb steps).

Each frame runs: carry forward, random drop, **refine**, then the usual new
shapes. Refining before the new shapes means they are searched against the
corrected canvas.

How `Refine` works:

1. **Pick the worst shapes.** Score every shape by the mean per-pixel error
   between `Current` and `Target` over its own scanlines (a single pass over
   the shapes' rasterised lines, no replay). Take the `k` highest. A ghost
   covers pixels that no longer match, so it ranks high.
2. **Rebuild the canvas without that shape.** Replay the other shapes in order
   (one pass per refined shape, `n` rasterisations) and `Init` a `Worker` on
   that canvas.
3. **Hill-climb it.** Start from a copy of the shape, run the existing
   `HillClimb` / `State` machinery for `age` steps, and compare against the
   original shape's energy on the same canvas.
4. **Keep it only if it is better.** If improved, the new shape (a new value,
   with a freshly fitted colour) replaces the old one and moves to the top of
   the z-order. If not, the original is left exactly as it was.

Moving an improved shape to the top is a deliberate approximation. Evaluating a
shape in its original z-position would mean re-rendering everything above it for
every candidate, which costs far more than the touch-up is worth. A ghost sits
behind later shapes anyway, so lifting the corrected version to the top is
usually what we want visually.

## Continuity metric (ADR-0010)

Shapes are immutable and the metric compares them by identity. A refined shape
is a new value, so the old metric would count it as one dropped and one added.
That would blur the very thing refinement is meant to show.

`Model` records `refinedFrom map[Shape]Shape` (new shape to the shape it
replaced). `MeasureContinuity` stays as it is. A new `MeasureContinuityRefined`
takes the model, and `Continuity` gains a `Refined` field: shapes present in
this frame that descend from a shape in the previous frame. They are counted
under `Refined`, not `Added`, and the identity of the replaced shape is not
counted under `Dropped`. ADR-0010 already anticipated a "modified" category.
Frames with refinement off report `Refined: 0` and the existing numbers are
unchanged. `frames.json` and the video manifest gain a `refined` count.

## Cost

Selection is one pass over the shapes. Each refined shape costs about `n`
rasterisations for the replay plus `age` energy evaluations. With `n = 500`,
`k = 20`, `age = 100` that is roughly 10,000 rasterisations and 2,000
evaluations per frame. A single `Step` already does about 16,000 random
evaluations plus hill-climbing, so `k = 20` should add less than one extra
`Step` per frame. I will measure this and report it, not assume it.

## Alternatives considered

| Option | Why not (or why later) |
|--------|-------------------------|
| Guided drops: drop the `k` highest-error shapes instead of random ones, then let `Step` fill the holes | Smallest change (it only replaces `keepIndices`) and probably catches most ghosts. It cannot adjust a shape that is nearly right, only delete it. Worth trying first as a comparison arm; if it fixes ghosting well enough, refinement may not be needed |
| Refine in place at the original z-position | Needs a re-render of every shape above it per candidate. Too slow for the gain |
| A temporal loss term inside `Step` | Rejected by ADR-0010: nothing here suppresses flicker |

## Validation

- Unit tests with a seeded `Worker.Rnd` on a small synthetic sequence (a square
  that moves): after `Refine`, the score does not get worse, an unimprovable
  shape stays identical (same pointer), and `k = 0` is a no-op.
- A continuity test that a refined shape counts under `Refined` only.
- `scripts/sweep.py` renders the same clip with refinement off, guided drops,
  and refinement at `k = 10, 30`, same shape count, so you can compare ghosting
  by eye. Per-frame RMSE from `frames.json` goes alongside as a rough number.
- Existing frame-sequence tests pass unchanged with the flag off.

## Consequences

- `Model` gains one method and one map. `Step`, `Worker.Energy` and scoring are
  unchanged.
- Refinement can reduce flicker as a side effect (a kept shape that is nudged
  rather than replaced). That is incidental, and the metric reports it.
- Moving refined shapes to the top changes the layer order over time. If that
  produces visible artefacts in the sweeps, the fallback is guided drops.

## Decisions (Jian, 2026-10-19)

1. Build guided drops first, as a cheap baseline, then `Refine` only if the sweeps still show
   ghosting.
2. Moving a refined shape to the top of the z-order is acceptable.
3. Default `k` once it is on: about 10% of the shape count.
