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
  diffusion job via the vendored `runpod/launch.sh`; `runpod/generate.py` runs
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

`pipeline/orchestrate.py --dry-run` builds and prints the full RunPod launch
plan (the embedded job command, env overrides) without spending anything —
always sanity-check a change this way before a real run. A real run needs
`RUNPOD_API_KEY` in the environment and an account spend cap set in the
RunPod console first (see docs/adr/0001).

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
