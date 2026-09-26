# ADR-0004: Pipeline runs are addressed on the filesystem, not a database

- Status: Accepted
- Date: 2026-09-26
- Deciders: Jian, Claude (planning session)

## Context

Each pipeline run produces N items (prompt → seed image → primitive output),
each with metadata (model used, timings, cost estimate, status). This needs to
be addressable and inspectable — by the user, and by a future phase-3 step that
might want to reuse a run's outputs as video frames.

## Decision

No database. A run is a directory: `runs/<run_id>/` where `run_id` is a UTC
timestamp slug (`run-20260926-153000`). Each item is a subdirectory
`<index>-<slug>/` containing `prompt.txt`, `seed.png`, `primitive.png` /
`primitive.svg`, and the item's own metadata. The run as a whole has a
`manifest.json` at its root listing every item, its status (`ok` / `failed` +
reason), the model/params used, and a top-level `schema_version` field.
`runs/` is git-ignored, matching the project's existing convention of not
committing generated image output.

## Alternatives considered

| Option | Why not |
|--------|---------|
| SQLite manifest | More queryable, but this is single-user, experiment-scale, and a human needs to be able to `ls`/`open` a run's outputs directly without a query tool. A JSON manifest is `diff`-able and greppable for free |
| Cloud storage / object store from the start | No stated need yet — everything runs on one machine. Revisit only if a later phase needs shared/remote access to run outputs |

## Consequences

- Trivial to inspect, version-control-friendly to *reference* (the manifest
  schema can be documented and diffed even though the directory itself is
  ignored), and needs no new infrastructure.
- No built-in query capability (e.g. "all items using model X across all runs")
  — would need a small script over the manifests if that's ever wanted. Not
  needed for phase 1's scale.
- `schema_version` is included from day one specifically so phase 3 (video
  frames) can extend the item shape without breaking readers of old manifests.
