# ADR-0010: Flicker is measured with a shape-level continuity metric, used as a tunable observability aid, not an enforced objective

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian, Claude (planning session)

## Context

Phase 3 needs *some* way to know whether warm-starting (ADR-0009) is doing
anything measurable, and to give the new-shapes-per-frame dial a number to
tune against. Two shapes for a metric were considered: pixel-level (diff the
rendered PNG output between consecutive frames) or shape-level (compare the
two frames' `[]Shape` lists directly — how many shapes were carried unchanged
vs. newly added).

The user does not want flicker eliminated or penalized — some is an accepted,
possibly desirable, aesthetic outcome of this mode — so the metric's purpose
is observability and tuning, not a pass/fail gate or an optimization target
fed back into the hill-climb loop.

## Decision

A shape-level continuity metric, implemented as a pure Go function over two
consecutive frames' shape lists: the fraction of shapes carried unchanged
versus newly added (and, if active refinement is ever built per ADR-0009's
deferred alternative, modified). This is directly checkable — given a fixed
carry-forward budget and a fixed new-shape count, the expected fractions are
exact arithmetic, not a judgement call — and needs no image-diff plumbing, no
new dependency, and no GPU: it folds into `make test` the same way ADR-0003's
grid-jitter unit test does.

The metric is surfaced per-frame and as a run-level summary (in the extended
video-run manifest), so a user can see the effect of a given
new-shapes-per-frame setting and retune it to taste. It is not used inside
`Model.Step`'s scoring or hill-climbing — nothing in this milestone tries to
minimize or maximize it automatically.

A pixel-level frame-to-frame diff (computed pipeline-side on the rendered
outputs) remains a secondary, optional sanity check a user could add later if
the shape-level number and the visual result ever diverge — not built in this
milestone.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Pixel-level frame-diff as the primary metric | Noisier signal (conflates legitimate motion with shape-search instability), needs new image-diff plumbing/dependency; the warm-start mechanism operates in shape-space, so a shape-space metric is the more direct measurement of it |
| A temporal-loss term fed into `Model.Step`'s scoring, actively suppressing flicker | Rejected per ADR-0009's context: the user does not want flicker suppressed, only visible/tunable — building an enforcement mechanism for a non-goal would be pure waste |

## Consequences

- No new Python or Go dependency; the metric is pure, deterministic, and
  unit-testable the same way the rest of `primitive/`'s scoring code is.
- The metric is descriptive only — a run with a "bad" continuity number is not
  a failed run; nothing in the pipeline treats it as an error state.
- If a future need for flicker *suppression* (rather than tolerance) emerges,
  this ADR's scope explicitly does not cover it — that would be a new
  decision, not an extension of this metric's role.
