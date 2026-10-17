# primitive-pictures

Reproduces an image using a small number of geometric primitives (triangles,
ellipses, rectangles, etc.), picking one shape at a time via hill-climbing to
minimize RMSE against the target image. Fork of
[fogleman/primitive](https://github.com/fogleman/primitive); see NOTICE.md for
the original license attribution.

Trust the code over this file. If a command below doesn't work, the code is
right and this file is stale — fix the file.

## Layout

- `main.go` — CLI entrypoint
- `primitive/` — the core library (shapes, rasterization, optimizer, model).
  Public API — other Go programs import this package directly, so avoid
  moving it under `internal/`.
- `bot/` — a standalone Python Twitter bot (Flickr sourcing + posting).
  Not part of the Go build/test loop.
- `pipeline/` — the RunPod generation pipeline (uv-managed Python; see
  `docs/PLAN.md` and `docs/SLICES.md`). `orchestrate.py` drives a batch
  diffusion job via the vendored `runpod/launch.sh`, then runs `primitive`
  (argv-list subprocess, never `shell=True`) on every seed image and writes
  `runs/<run_id>/manifest.json` (`manifest.py`, ADR-0004) — one failed item
  doesn't abort the rest. `orchestrate.py --process-only RUN_DIR` re-runs
  just that primitive step against an existing run directory, no RunPod
  needed — the way to test/iterate on this locally. `runpod/generate.py` runs
  on the pod, not locally. `promptbank.py` is a static, seeded prompt
  template bank (`--category`/`--style`/`--count`/`--seed`) — no LLM, no
  external dependency (ADR-0006, supersedes the earlier LLM-based ADR-0005;
  if an LLM step ever comes back it's OpenRouter/DeepSeek, not Anthropic).
  Separate from `bot/` — different job (ADR-0002).
- `scripts/` — `pre-push` (git hook, see below), plus `html.py`/`process.py`
  helper scripts for batch runs.
- `examples/` — sample input images used by `make run` and tests.
- `docs/` — `PLAN.md`, `SLICES.md`, `QUESTIONS.md`, `adr/` for the RunPod
  pipeline + pointillism-mode phase. Read `PLAN.md` before touching `pipeline/`.

## Commands

Run `make` with no target for the full list. The important ones:

- `make check` — fmt, vet, lint, go.mod/go.sum tidiness (fast, no infra)
- `make test` — race-enabled unit tests (fast, no infra)
- `make test-all` — same, including anything tagged `integration` (none yet)
- `make build` — build `bin/primitive` for the current platform
- `make xbuild` — cross-compile for linux/darwin/windows, amd64/arm64
- `make run` — build and run against `examples/monalisa.png` (100 triangles)
- `make vulncheck` — `govulncheck ./...`
- `make python-check` — lint + format-check `pipeline/` (ruff, via `uv`)
- `make python-test` — `pipeline/`'s fast tests (no RunPod/network involved)
- `make ci` — everything CI runs, in one shot
- `bin/primitive -frames DIR -out DIR -n N [-drop-rate R] [-new-shapes-per-frame K] [-resume]` —
  frame-sequence mode: one process over an ordered frame directory, each frame seeded from the
  previous one's shapes (ADR-0007/0009); writes per-frame PNG/SVG + `frames.json`, prints a
  continuity summary (ADR-0010). Dynamic by default: drop 30% (R=0.3), K defaults to R x N so the
  count holds steady.
- `pipeline/orchestrate.py --video --prompts FILE` — generate clips on RunPod, then run that mode
  over each clip's frames and write a slowed preview `primitive.mp4` (`--playback-fps`, default 8)
  plus a schema-2 `manifest.json`. `--video --process-only RUN_DIR` redoes just the local part
  (free). Settings: `--shape-count` (default 500), `--shape-mode`, `--drop-rate`,
  `--new-shapes-per-frame`.
- `scripts/sweep.py FRAMES OUT [--fps 8] name:N:ADD:DROP ...` — render a frame dir under several
  settings and stitch slowed side-by-side mp4s for review (needs `bin/primitive` and ffmpeg).

Before any paid *video* launch, run `pipeline/runpod/offline_video_smoke.py` (setup is in
its docstring): it runs the pod-side video path on CPU with a tiny random model, and found
bugs that cost a pod launch each (missing package, wrong frame type).

`pipeline/orchestrate.py --dry-run` builds and prints the full RunPod launch
plan (the embedded job command, env overrides) without spending anything —
always sanity-check a change this way before a real run. A real run needs
`RUNPOD_API_KEY` + `RP_GPU_TYPE` and an account spend cap set in the RunPod
console first (see docs/adr/0001). Copy `.env.example` to `.env` (git-ignored)
and fill in real values — `orchestrate.py` loads it automatically on every
run via `python-dotenv`; nothing needs to be exported by hand. Never
`cat`/echo/print `.env` or its values.

There is no separate "integration" test layer yet: the whole library is pure,
in-process Go with no database/network/filesystem dependency to isolate. If
that changes, gate the new tests behind `//go:build integration` and add them
to `test-all`.

## Conventions

- One branch per change, PR into `main`, no direct pushes.
- Pre-push hook mirrors CI's fast jobs. Install once per clone:
  `ln -sf ../../scripts/pre-push .git/hooks/pre-push`. Skip deliberately with
  `git push --no-verify`.
- `gofmt -l` and `go vet` are hard failures, not suggestions.
- Every shape type implements the `Shape` interface in `primitive/shape.go`
  (`Rasterize`, `Copy`, `Mutate`, `Draw`, `SVG`) — that's the extension point
  for adding a new primitive.
