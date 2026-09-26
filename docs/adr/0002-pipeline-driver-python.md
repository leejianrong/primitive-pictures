# ADR-0002: Pipeline driver is a new Python package, invoking `primitive` as a subprocess

- Status: Accepted
- Date: 2026-09-26
- Deciders: Jian, Claude (planning session)

## Context

The batch pipeline (prompt-gen → RunPod diffusion → local `primitive` run →
manifest) needs a driver language and a place to live. Two live options: Go
(single-language repo, but weaker RunPod/diffusers/LLM SDK ecosystem) or Python
(matches the existing `bot/` component, and the RunPod/diffusers/LLM ecosystem is
Python-first).

This is a checkable-fact question, not a preference one: `bot/main.py` already
shells out to the compiled `primitive` binary (`bot/main.py:120-129`,
`subprocess.call(cmd, shell=True)`), establishing a real precedent for exactly
this integration pattern in this repo.

## Decision

The pipeline driver is Python, in a new top-level `pipeline/` directory —
separate from `bot/`, which stays the Twitter-specific component it already is.
It invokes the locally-built `primitive` binary as a subprocess, **as an argv
list, never `shell=True`**: `subprocess.run(["primitive", "-i", seed_path, "-o",
out_path, ...], check=False)`. `bot/main.py`'s `shell=True` with an
interpolated command string is a command-injection-shaped pattern (arguments
there are pipeline-internal today, so it hasn't bitten, but it's not a pattern to
copy forward); the new driver does not repeat it. Fixing `bot/main.py` itself is
out of scope for this pass — it's a separate, currently-working component.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Go driver, single-language repo | RunPod's client tooling, `diffusers`, and most LLM SDKs are Python-first; would mean hand-rolling or wrapping REST calls Go has no first-class client for |
| Extend `bot/` instead of a new `pipeline/` dir | `bot/` is a working, scoped component (Flickr sourcing + Twitter posting); folding unrelated batch-pipeline concerns into it muddies both |

## Consequences

- Two language runtimes in one repo (Go core + CLI, Python pipeline + bot).
  Acceptable — they're genuinely different jobs (compute-bound shape search vs.
  API/GPU orchestration), and `dev-playbook`'s Go gates already scope cleanly to
  the Go parts via `go.mod`.
- The Python side needs its own lightweight quality gates (at minimum: a linter,
  a fast unit-test layer for the manifest/prompt logic) — tracked as a slice 1
  task, not assumed to inherit the Go Makefile's gates.
- `primitive` must be built (`make build`) before the pipeline can run; the
  driver should fail fast with a clear message if the binary isn't on `PATH` or
  at the expected build location, rather than a raw `FileNotFoundError`.
