# ADR-0005: Prompt generation uses a hosted LLM API, not a RunPod-hosted model

- Status: Accepted
- Date: 2026-09-26
- Deciders: Jian, Claude (planning session)

## Context

The pipeline's optional first step turns a rough theme/count into a batch of
short image prompts. The user's own framing left this open ("an LLM (API or
runpod) to generate some short prompts"). Given the "keep RunPod spend cheap"
constraint, this needed a concrete default.

## Decision

Prompt generation calls a hosted LLM API (Anthropic's Claude, a small/cheap
model such as Haiku) directly from the Python driver — no RunPod pod involved
for this step. Text generation for a batch of short prompts is seconds of work
and fractions of a cent on a hosted API; spinning a GPU pod (with its own
lifecycle, cost-safety, and teardown-verification requirements) for it would
burn budget and complexity on a step that isn't the point of this project.

## Alternatives considered

| Option | Why not |
|--------|---------|
| RunPod-hosted open LLM (e.g. served alongside or instead of the diffusion pod) | Adds a whole pod-lifecycle/cost-safety surface for a task an API call handles more cheaply and more simply; only worth it if there's a reason to avoid external APIs entirely (offline/fully-self-hosted requirement), which hasn't been stated |
| Skip prompt-gen entirely, require the user to always supply a prompt file | Still fully supported (it's the base case — see SLICES.md), but the LLM step being available is explicitly one of the ideas the user wants |

## Consequences

- Adds a dependency on `ANTHROPIC_API_KEY` being set for the prompt-gen path
  specifically (independent of `RUNPOD_API_KEY` for the diffusion step) —
  handled per the same never-log/never-commit discipline as the RunPod key.
- Prompt-gen and diffusion-gen are now on two different infrastructure paths
  (hosted API vs. rented GPU pod); this is a deliberate asymmetry, not an
  oversight — each step used the infrastructure that fits its actual cost
  profile.
- Swappable later without touching the rest of the pipeline if there's ever a
  concrete reason to self-host prompt generation (e.g. wanting a fully offline
  pipeline) — it's an isolated function behind one interface, not threaded
  through the rest of the driver.
