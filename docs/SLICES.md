# Primitive Pictures — Phase 1: Slices

Vertical increments. Each ends in something you can demonstrate. Slice 1
confronts the riskiest unknown: real RunPod orchestration, cost safety, and
whether the diffusion output is actually good enough source material.

## V1: RunPod batch-job mechanism + model comparison

**Delivers:** R1 (fully), R0 (partial — proves the hardest piece), R5

**Build plan**

1. Set up Python tooling scaffold: `pipeline/pyproject.toml`, `ruff` (lint) +
   `pytest` (tests), and `make python-check` / `make python-test` targets
   alongside the existing Go Makefile targets — the fast, no-infra gate this
   and later slices' unit tests run against.
2. Write `pipeline/runpod/launch.sh`, adapted from the runpod-jobs skill's
   `scripts/rp`: account spend-cap check, dead-man's-switch (max-lifetime +
   idle watchdog) armed as the literal first thing the pod's start command
   runs, driver-side `trap … EXIT INT TERM`.
3. Write `pipeline/runpod/generate.py` (pod-side): reads a JSON prompt list,
   generates one image per prompt using the selected backend
   (`sdxl-turbo` / `sd15` / `flux-schnell`), writes them locally on the pod,
   sends them back via `runpodctl send` (or the relay equivalent), prints the
   transfer code.
4. Write the driver-side pull: `pipeline/runpod/launch.sh` (or a thin Python
   wrapper) calls `runpodctl` to receive the relayed files into
   `runs/<run_id>/`.
5. Manual comparison run: same 3–5 prompts, all three models, at each model's
   practical native resolution (not forced 256×256 — see ADR-0001). Eyeball
   quality, note wall-clock and $ cost per model from the RunPod console —
   and weigh each model's license (ADR-0001) alongside quality/cost.
6. Record the chosen default model in a small config value (e.g.
   `pipeline/config.py`), with the other two still selectable via `--model`.

**Demo:** run the launcher against a 3-prompt file with `--model compare`;
watch it boot one pod, generate 9 images (3 prompts × 3 models), relay them
back, and terminate. `runpodctl pod list` shows nothing running afterward.

**Rests on assumptions:** Q4/Q9 (batch job, not a service; vendored launcher)
— if wrong, S2/S3 need reworking into a service shape, a bigger change.

### Test plan

#### End-to-end

- Given a 3-prompt file and `--model compare`, the run produces 9 seed images
  under `runs/<run_id>/`, and `runpodctl pod list` shows the pod terminated
  within 60 seconds of the script exiting.
- Killing the driver mid-run (Ctrl-C) still results in the pod being
  terminated (proves the trap, not just the happy path).

#### Integration

- `launch.sh` refuses to launch if the configured hourly cost estimate exceeds
  the configured cap (no real pod created).
- The relay-pull step correctly maps N transfer codes back to N local files
  even when one of them fails (doesn't hang waiting on a code that never
  arrives).

#### Unit

- Argv construction for the `runpodctl`/pod-create calls is correct for each
  of the three model backends (no live network call in this test).

## V2: Full prompt-file pipeline (primitive step + manifest)

**Delivers:** R0 (fully), R3

**Build plan**

1. Write `pipeline/manifest.py`: run/item data model (ADR-0004),
   `schema_version`, read/write JSON.
2. Write `pipeline/run.py`: takes `--prompts <file>` and the chosen
   `--model`, drives V1's launcher, then for each returned seed image invokes
   `primitive` as an argv-list subprocess (no `shell=True` — ADR-0002),
   writing `primitive.png`/`primitive.svg` alongside the seed image.
3. Wire per-item failure handling: one failed item (bad seed image, `primitive`
   non-zero exit) is recorded as `status: failed` in the manifest; the run
   continues to the next item.
4. `runs/` added to `.gitignore`.

**Demo:** `pipeline/run.py --prompts examples/pipeline-prompts.txt` (5 prompts)
completes unattended and prints a summary; `runs/<run_id>/manifest.json` shows
5 `ok` items, each with a viewable `primitive.png`.

**Rests on assumptions:** Q13 (Python driver) — if wrong, this whole slice's
implementation language changes, though the shape (manifest, per-item status)
carries over.

### Test plan

#### End-to-end

- A 5-prompt file run against a cost-capped small batch produces 5 manifest
  entries and 5 viewable `primitive.png` outputs, and total RunPod spend for
  the run is under $0.50 (checked against the account's spend log).
- A prompt file with one deliberately-broken entry (e.g. an empty prompt
  string) still produces 4 `ok` items and 1 `failed` item with a readable
  reason — the run does not abort.

#### Integration

- The `primitive` subprocess call is built correctly (right binary path,
  right flags) and its exit code is captured into the manifest, without a
  shell being invoked (assert on the `subprocess.run` call shape, not just its
  output).

#### Unit

- `manifest.py` round-trips (write then read) an example run with mixed
  ok/failed items and preserves `schema_version`.

## V3: LLM prompt-gen layer

**Delivers:** R4

**Build plan**

1. Write `pipeline/promptgen.py`: theme + count → N short prompts via the
   Anthropic API (ADR-0005), returning the same prompt-list shape V2 already
   consumes.
2. Extend `pipeline/run.py` with `--theme "..." --count N` as an alternative
   to `--prompts <file>`; the generated prompts get written to
   `runs/<run_id>/prompts.txt` so the run is reproducible/inspectable even
   though the input wasn't a hand-written file.

**Demo:** `pipeline/run.py --theme "underwater cities" --count 5` generates 5
prompts, writes them to disk, then runs exactly V2's pipeline against them.

**Rests on assumptions:** Q8 (hosted API, not RunPod) — if wrong (e.g. a
future no-external-API requirement), only this module changes.

### Test plan

#### End-to-end

- `--theme "..." --count 5` produces a 5-line `prompts.txt` and then the same
  5-item manifest/output structure as V2's direct-file path.

#### Integration

- With the Anthropic API mocked to return malformed output (not N lines), the
  driver fails fast with a clear error rather than silently passing a bad
  prompt list into V2's pipeline.

#### Unit

- Given a mocked API response, `promptgen.py` returns exactly N prompt
  strings, trimmed and non-empty.

## V4: Pointillism mode

**Delivers:** R2

Independent of V1–V3 — pure Go core, no RunPod/pipeline dependency. Can be
built in any order relative to the others.

**Build plan**

1. Add a new mode constant (next available `-m` number) and CLI flags for
   grid spacing and jitter radius in `main.go`.
2. Implement the constrained-random candidate generator (ADR-0003): snap a
   random shape's center to the nearest jittered grid cell instead of
   sampling uniformly across the canvas. Reuses the existing
   `Model.Step`/`Worker` hill-climb loop unchanged.
3. Add an example output to `examples/`/README showing the pointillism mode
   against one of the existing sample images.

**Demo:** `primitive -i examples/monalisa.png -o out.png -m <pointillism>
-n 400 --grid 8 --jitter 2` produces a visibly stippled rendering.

**Rests on assumptions:** none from the register — this slice's own design
question (F2) was resolved in the grill round, not assumed.

### Test plan

#### End-to-end

- Running the pointillism mode against a fixed test image produces an output
  file (PNG and SVG) with no error, in under the same rough time budget as the
  existing circle mode at the same shape count.

#### Integration

- N/A — no external dependency for this slice.

#### Unit

- Given a fixed grid spacing/jitter and a seeded RNG, generated shape centers
  fall within the jitter radius of a grid-cell center (never fully free-form).
- Shape count and canvas size are respected (no candidate center generated
  outside canvas bounds even from an edge grid cell plus jitter).
