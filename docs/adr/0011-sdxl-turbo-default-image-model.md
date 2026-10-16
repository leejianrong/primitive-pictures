# ADR-0011: Default image-generation model briefly switched to SDXL-Turbo, then reverted to Flux.1-schnell

- Status: Accepted (reverted same day — see Decision)
- Date: 2026-09-28
- Deciders: Jian

## Context

ADR-0001 picked Flux.1-schnell as `config.DEFAULT_MODEL` from a real 3-model
comparison run, weighing output quality against license: Flux.1-schnell's
Apache-2.0 license was the deciding factor over SDXL-Turbo's competitive
quality, specifically because SDXL-Turbo's Stability AI Non-Commercial
Research Community License "rules it out for this project's `bot/` roadmap" —
`bot/` is a separate, public-facing Twitter bot (ADR-0002); if it ever posts
pipeline-generated output, that's the kind of public/commercial-adjacent use
SDXL-Turbo's license doesn't clearly permit without a separate paid license
from Stability AI.

While scoping phase 3, the user stated a direct preference for SDXL-Turbo's
output over Flux.1-schnell's, independent of that license tradeoff, and asked
to switch the default. That switch was made and tested (30/30 pipeline tests
still passed, since none assert the default's value directly). On revisiting
the license/`bot/` conflict explained plainly, the user chose to keep
Flux.1-schnell as the default after all — the output-quality preference
didn't outweigh keeping the door open for `bot/` once the tradeoff was
concrete rather than abstract.

## Decision

`config.DEFAULT_MODEL` stays `"flux-schnell"`. No net code change from
ADR-0001's original pick. This ADR exists to record that the alternative was
seriously considered and tried, not silently rejected — a future reader
re-litigating "why not SDXL-Turbo" should find this, not rediscover the same
tradeoff from scratch.

SDXL-Turbo and SD1.5 remain selectable via `--model` for any run where
`bot/`-compatibility isn't a concern (e.g., one-off local experimentation).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep SDXL-Turbo as default, accept the `bot/` constraint | User's own call once the tradeoff was concrete: output-quality preference didn't outweigh foreclosing `bot/`'s future option |
| Split the default by intended use (SDXL-Turbo for local/personal, Flux.1-schnell for anything bot/public-facing) | More configuration surface for a distinction that doesn't exist as a real invocation-time signal today — `pipeline/` has no notion of "this run's output might reach `bot/`" to key off of |

## Consequences

- No functional change from ADR-0001 — this ADR is purely a record of a
  considered-and-reverted alternative.
- The `bot/` constraint is now documented in two places (ADR-0001's original
  reasoning and this ADR's retelling) rather than one, which is intentional:
  it's the kind of constraint that's easy to forget and re-litigate later.
- If `bot/`'s roadmap is ever formally dropped or changed such that it will
  never consume pipeline output, this constraint disappears and SDXL-Turbo
  becomes a free choice again — worth a fresh ADR at that point, not an edit
  to this one.
