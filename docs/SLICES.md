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

Built into `pipeline/orchestrate.py` (V1's driver), not a separate `run.py` as
originally sketched — same file, since `orchestrate.py` already owned the
plan/launch lifecycle and the primitive step just extends `execute()`.

**Build plan**

1. Write `pipeline/manifest.py`: run/item data model (ADR-0004),
   `schema_version`, read/write JSON.
2. Extend `orchestrate.execute()`: after unpacking the pod's tar, read its
   `out/generation-manifest.json` (written by `runpod/generate.py`) and, for
   each seed image, invoke `primitive` as an argv-list subprocess (no
   `shell=True` — ADR-0002), writing `<stem>.primitive.png`/`.svg` alongside
   the seed image. New CLI flags: `--shape-count`, `--shape-mode`,
   `--primitive-bin` (fails fast with a clear message pointing at `make
   build` if the binary isn't found).
3. Wire per-item failure handling: one failed item (bad seed image, `primitive`
   non-zero exit) is recorded as `status: failed` in the manifest; the run
   continues to the next item. Exit code is non-zero only if *every* item
   failed (Q7).
4. `runs/` in `.gitignore` — already done in V1.
5. *(Beyond the original plan)* `--process-only RUN_DIR`: re-run the
   primitive step against an already-populated run directory, skipping
   RunPod entirely. This is what makes V2 fully testable and demoable
   without spending anything — the same mechanism a real run uses, pointed
   at a local stand-in `out/` tree instead of one just pulled off a pod.

**Demo:** manually populate a `runs/<id>/out/<model>/*.png` tree plus a
`generation-manifest.json` (i.e. what a real pod run would have produced),
then `orchestrate.py --process-only runs/<id>`. `runs/<id>/manifest.json`
shows every item's status, each `ok` item has a viewable `.primitive.png`.

**Rests on assumptions:** Q13 (Python driver) — if wrong, this whole slice's
implementation language changes, though the shape (manifest, per-item status)
carries over.

### Test plan

#### End-to-end

- `--process-only` against a directory with 3 valid seed images and 1
  corrupt one produces 3 `ok` items with viewable `.primitive.png`/`.svg`
  outputs and 1 `failed` item with `primitive`'s own error message, and the
  run does not abort partway through.
- The real RunPod path (5-prompt run, spend under $0.50) is the manual
  acceptance test once credentials are available — not run in CI.

#### Integration

- The `primitive` subprocess call is built correctly (right binary path,
  right flags) and its exit code is captured into the manifest, without a
  shell being invoked (assert on the `subprocess.run` call shape, not just
  its output).
- `process_run` writes `manifest.json` to disk, not just returns it in
  memory — a caller that crashes right after can still recover the result.

#### Unit

- `manifest.py` round-trips (write then read) an example run with mixed
  ok/failed items and preserves `schema_version`.
- `resolve_primitive_bin` raises a clear, actionable error (not a bare
  `FileNotFoundError` with no context) when the binary is missing.

## V3: Prompt template bank

Redefined by ADR-0006 (2026-09-27): no LLM call, no external dependency.
Independent of V1/V2/V4 — buildable and fully testable today, same as V4.

**Delivers:** R4

**Build plan**

1. Write `pipeline/promptbank.py`: a subject bank grouped by category
   (`landscapes`, `cityscapes`, `animal-portraits`, `animal-groups`,
   `wildlife-scenes`) and a separate style/mood modifier list
   (photorealistic, watercolor, oil painting, retro film photo, golden-hour
   lighting, storm lighting, minimalist, vibrant, muted pastel). A sampling
   function takes `categories`, `styles`, `count`, and `seed`, and returns
   exactly `count` prompt strings (subject + 0–2 sampled modifiers) —
   deterministic for a given seed.
2. Extend `pipeline/orchestrate.py` with `--category ... --style ...
   --count N --seed N` as an alternative to `--prompts <file>` (mutually
   exclusive with it); the generated prompts get written to
   `runs/<run_id>/prompts.txt` so the run is reproducible/inspectable even
   though the input wasn't a hand-written file.
3. `--list-bank` (or similar) prints the available categories/styles, so a
   user doesn't have to read the source to know what's in it.

**Demo:** `pipeline/orchestrate.py --category landscapes,wildlife-scenes
--style watercolor --count 5 --seed 42 --dry-run` prints 5 reproducible
prompts; the same command run again produces byte-identical output.

**Rests on assumptions:** none from the register — this slice's design
(ADR-0006) was a direct decision, not a default.

### Test plan

#### End-to-end

- `--category ... --count 5 --seed 42` produces a 5-line `prompts.txt`, and
  running the identical command again produces an identical file (same
  seed, same output — the reproducibility guarantee this slice exists for).
- `--category` and `--prompts` given together is a clear usage error, not
  silently-one-wins behavior.

#### Integration

- The generated prompt list flows into the same manifest/output structure
  as V2's direct-file path (i.e. `orchestrate.py` doesn't need to know
  whether prompts came from a file or the bank past the point of selection).

#### Unit

- Given a fixed seed, `promptbank.sample()` returns exactly `count` prompts,
  each non-empty and containing a recognizable subject from the requested
  categories.
- Two different seeds produce different prompt sets; the same seed run
  twice produces the same set (this is the core guarantee, tested directly
  rather than just implied by the E2E test).
- Requesting a category or style not in the bank is a clear `ValueError`,
  not a silent empty result.

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
