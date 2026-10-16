# ADR-0007: Frame-sequence processing is one in-process Go invocation, not N subprocess calls with serialized shape state

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian, Claude (planning session)

## Context

Phase 1's pipeline invokes the compiled `primitive` binary once per seed image,
as an independent argv-list subprocess (`pipeline/orchestrate.py:process_run`).
That pattern gives per-item failure isolation for free — one image's failure
doesn't touch the next.

Phase 3 needs the opposite property between frames: frame N's shape search must
start from frame N-1's *finished* shape list (ADR-0009), not from scratch. If
frame-by-frame processing stayed one subprocess per frame, that shape list would
have to cross a process boundary — meaning `Shape` (an interface with nine
concrete implementations: `Triangle`, `Rectangle`, `Ellipse`, `Circle`,
`RotatedRectangle`, `Quadratic`, `RotatedEllipse`, `Polygon`, `Stipple`, per
`primitive/shape.go`) would need a serialization format that preserves which
concrete type each shape is, plus its exact fields — a discriminated-union
encode/decode that doesn't exist today and that every future shape type would
have to remember to extend.

## Decision

Frame-sequence processing is a single, new, in-process Go entrypoint that keeps
the whole sequence in memory: it loops over frames itself, and each frame's
`Model` is seeded directly from the previous frame's live `[]Shape`/`[]Color`
slices (ADR-0009) — no serialization, no process boundary, no new encode/decode
format. `pipeline/`'s side of the contract shrinks to one subprocess invocation
per *video run*, not per frame (passing a frame directory and an ordered file
list), which the pipeline driver already knows how to do.

Per-item failure isolation is replaced with per-frame incremental output plus a
`--resume` flag: each frame's PNG/SVG is written to disk as soon as that frame
completes, so a crash partway through a run still leaves every completed frame
on disk, and a rerun with `--resume` skips frames already present instead of
reprocessing the whole sequence. This is a different mechanism than phase 1's
per-item manifest status, but serves the same goal (partial results, not an
all-or-nothing run).

## Alternatives considered

| Option | Why not |
|--------|---------|
| One `primitive` subprocess per frame, shape state serialized to/from a file between invocations | Needs a new polymorphic `Shape` encode/decode format maintained forever alongside the `Shape` interface itself; every new shape type becomes two changes (`Rasterize`/`Copy`/`Mutate`/`Draw`/`SVG` plus a serialization case) instead of one |
| Keep per-frame subprocess isolation, accept the serialization cost | Buys back per-frame process isolation phase 3 doesn't clearly need (a crash inside one frame's hill-climb is not expected to be more likely or more damaging than a crash inside one image's processing in phase 1, which never had this concern) |

## Consequences

- `primitive/`'s public API gains one new additive entrypoint (a sequence/video
  processing function alongside `NewModel`); `Model.Step`, `Worker`, and the
  `Shape` interface itself are untouched — no other Go program importing this
  package sees a breaking change.
- No new serialization format to design, document, or keep in sync with the
  `Shape` interface as shapes are added.
- The failure/recovery story is now file-presence-based (`--resume` skips
  completed frames) rather than manifest-status-based; `pipeline/`'s video-run
  manifest (ADR-0004's schema, extended) records this per-frame status
  separately, read back after the Go process exits rather than driving it.
- This entrypoint processes one run's frames sequentially in one process; it
  does not parallelize across frames (each frame's own hill-climb already uses
  `Model`'s worker pool internally, per `model.go`'s existing `NewModel(...,
  numWorkers)`). Not a bottleneck at phase 3's short-clip scale; revisit only if
  a later phase needs longer clips.
