# ADR-0006: A static prompt template bank replaces LLM-based prompt generation

- Status: Accepted
- Date: 2026-09-27
- Deciders: Jian, Claude (planning session)

## Context

ADR-0005 committed V3 to an LLM prompt-gen step (theme + count → N prompts,
via a hosted API). Revisiting this before building it: a hosted LLM call adds
an external dependency, a per-call cost, non-determinism (the same "run"
can't be reproduced exactly), and a second secret to manage
(`ANTHROPIC_API_KEY`) — for a job that a fixed set of prompt templates covers
just as well for this project's actual use case (batches of varied,
reasonably generic image-generation prompts, not open-ended user requests).

Separately, the user no longer wants Anthropic in this project at all, and if
an LLM step is ever added back, wants it on OpenRouter (an OpenAI-compatible
endpoint) against a cheap model rather than Anthropic's API.

## Decision

**V3 becomes a prompt template bank, not an LLM call.** A subject bank
grouped by category (`landscapes`, `cityscapes`, `animal-portraits`,
`animal-groups`, `wildlife-scenes`) crossed with a separate, independent axis
of style/mood modifiers (photorealistic, watercolor, oil painting, retro film
photo, golden-hour lighting, storm lighting, minimalist, vibrant, muted
pastel). A prompt is a subject template plus 0–2 sampled modifiers. Selection
is **seeded random sampling** — the same `--seed` reproduces the exact same
prompt set, which a one-shot exhaustive-combination approach doesn't need to
solve for since randomness with a fixed seed already gives full
reproducibility without enumerating every combination.

This drops the `ANTHROPIC_API_KEY` dependency entirely and makes V3
buildable and fully testable with zero external services — same shape as V4
(pointillism): no blocked cards, nothing waiting on credentials.

**If an LLM step is ever added back** (not being built now — this is a
recorded default for if that changes): OpenRouter, not Anthropic, against a
cheap OpenAI-compatible model — `deepseek-chat` (DeepSeek-V3) as the specific
default to start from. OpenRouter's OpenAI-compatible surface means the
client code would be the standard OpenAI SDK/HTTP shape pointed at
OpenRouter's base URL, not a bespoke client.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep ADR-0005's Anthropic-based LLM step | Explicit user preference against Anthropic in this project; also carries a real external dependency + cost + non-determinism a static bank avoids entirely for this use case |
| LLM step via OpenRouter/DeepSeek, built now instead of a template bank | Still an external dependency + secret + non-determinism for no benefit here; the user specifically asked for the template bank as the actual mechanism, with OpenRouter/DeepSeek recorded only as the fallback if an LLM is wanted later |
| Exhaustive subject × style combinations instead of random sampling | No randomness to reason about, but far less variety per run unless `--count` is large enough to cover most of the cross product; seeded random sampling gets reproducibility for free without that tradeoff |

## Consequences

- No `ANTHROPIC_API_KEY`, no OpenRouter key, no external call, no cost, no
  network dependency for V3 at all. Fully offline and deterministic given a
  seed.
- Prompt variety is bounded by what's in the bank — extending categories or
  modifiers means editing `pipeline/promptbank.py`'s data, not a prompt
  engineering problem. Acceptable; this is a curated bank by design, not an
  open-ended generator.
- `pipeline/promptgen.py` (planned in the original SLICES.md V3, never built)
  is dropped; `pipeline/promptbank.py` replaces it. Nothing to migrate — no
  code existed against the old design.
- The OpenRouter/DeepSeek default is unused today. It exists so a future
  "actually, we do want an LLM step" conversation starts from a decision
  already made, instead of relitigating provider choice from scratch.
