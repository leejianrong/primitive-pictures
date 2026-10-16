# ADR-0009: Warm-started shape search uses static carry-forward, not active refinement of already-placed shapes

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian, Claude (planning session)

## Context

`Model.Step()` (`primitive/model.go`) and `Worker` (`primitive/worker.go`)
process one image with no memory of any other: every run starts from a blank
canvas, generates random candidate shapes, hill-climbs each via the shape's own
`Mutate()`, keeps the best, and permanently commits it — repeated until the
requested shape count is reached. Run this independently per video frame and
even a static region of the image gets a different, independently-randomized
set of shapes every frame, since nothing carries over; played back, this
flickers even where the source content didn't change.

"Warm-started shape search between frames" (the phase 3 roadmap note's phrase)
needed a concrete mechanism: how much of a new frame's search re-touches shapes
inherited from the previous frame, versus only adding new ones. Two shapes for
this were considered:

- **Static carry-forward**: the previous frame's shapes are copied in exactly
  as-is (pre-rendered onto the new frame's `Current`) and never revisited; only
  a capped budget of brand-new shapes is searched per frame, via `Model.Step`
  completely unchanged.
- **Active refinement**: besides adding new shapes, a bounded number of
  inherited shapes also get re-hill-climbed against the new frame each pass —
  a touch-up, not just new marks on top. This needs a new method that can
  revisit and re-optimize already-committed shapes, which does not exist today
  (`Step()` only ever operates on the next new shape).

Separately, the user does not consider flicker itself a defect to eliminate —
some is an acceptable, possibly desirable, aesthetic — which changes the
calculus: active refinement's whole purpose is suppressing an artifact the
user is fine with, at the cost of real new `Model`/`Worker` surface.

## Decision

Static carry-forward. A new frame's `Model` is constructed with its `Shapes`,
`Colors`, and `Scores` pre-populated from the previous frame's finished result,
`Current` pre-rendered from those shapes against the new frame's background,
and `Score` computed accordingly (reusing `differenceFull`/`differencePartial`,
unchanged). `Model.Step()` then runs, completely unmodified, for a capped
"new shapes per frame" budget — a new CLI-exposed parameter that is, in effect,
the continuity/flicker dial: a smaller budget keeps more of the previous
frame's shapes untouched (more static, less flicker); a larger budget
introduces more fresh shapes each frame (more dynamic, more flicker). Since the
user is comfortable with flicker as an aesthetic rather than a defect, this
dial is left as a tunable choice, not something the system tries to minimize by
default.

Active refinement is deferred, not rejected — logged as future work (see
QUESTIONS-phase3.md's Deferred table and the corresponding Pandan card), to
revisit only if static carry-forward's output, or the continuity metric
(ADR-0010) built to observe it, shows a concrete problem worth the added
`Model`/`Worker` surface to fix.

## Alternatives considered

| Option | Why not (for this milestone) |
|--------|-------------------------------|
| Active refinement of inherited shapes | Real new `Model`/`Worker` method (revisit-and-reoptimize-in-place), more that can subtly misbehave, built in service of suppressing flicker the user has explicitly said they don't mind |
| No warm start at all (today's behavior, run per frame independently) | Exactly the flicker problem the roadmap note names; not a serious option once frame-sequence processing exists at all |

## Consequences

- Smallest possible diff against the existing algorithm, matching the same
  reasoning ADR-0003 used for the pointillism mode: no changes to
  `Model.Step`, `Worker`, or scoring — the mechanism lives entirely in a new
  constructor path (ADR-0007's frame-sequence entrypoint calls it between
  frames).
- An old shape that's become visibly wrong for the new frame (e.g. content
  that moved) is never corrected, only ever papered over by a later new shape
  landing on top of it. Accepted, given the user's own tolerance for flicker —
  and it's the same accepted tradeoff, not a new one, as ADR-0003's "grid
  coverage is probabilistic, not guaranteed."
- The new-shapes-per-frame budget becomes a real tunable parameter users are
  expected to adjust per video, not a value the system should try to compute
  automatically in this milestone.
- If static carry-forward's visual output turns out clearly worse than
  desired even accounting for flicker tolerance, active refinement is the
  documented next step (not ruled out, just deferred) — same posture ADR-0003
  took toward its own deterministic-grid-walk alternative.
