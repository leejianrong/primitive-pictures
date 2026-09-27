# Running the diffusion step on RunPod

Phase 1 needs seed images before `primitive` has anything to work on, and generating
those with a real diffusion model needs a GPU we don't have locally. RunPod rents one
by the hour. This document is about how that's wired up and what actually broke while
getting it working, because most of the interesting decisions here came from real
failures rather than anything we planned up front.

## Why a batch job, not a service

The alternative to what we built is a long-lived pod running something like
AUTOMATIC1111 or ComfyUI as an HTTP server, with the driver calling it once per
prompt. We didn't do that. A pod that stays up between calls bills for the idle time,
and it adds a proxy and an HTTP layer for no real benefit at this batch size. Instead,
the pod boots once per run, a script on it reads the whole prompt list, generates
every image, ships the results back, and the pod terminates itself. Boot time (image
pull, model load) gets paid once per run instead of once per prompt, and there's
nothing sitting around between jobs to forget about.

## The part that actually matters: not leaking a bill

RunPod has no native idle-terminate, no max-lifetime, and no per-pod spend cap. A pod
you forget about just keeps running. So the safety here isn't one mechanism, it's
three, stacked so that any single one failing doesn't cost real money:

An account-level spend limit in the RunPod console is the coarse backstop. Below
that, the pod's own start command bakes in a dead-man's-switch before it runs
anything else: a hard max-lifetime (`sleep N; terminate`) and an idle watchdog that
kills the pod if its log stops growing for too long. And on the driver side,
`pipeline/runpod/launch.sh` traps `EXIT`/`INT`/`TERM` so the pod gets torn down
whether the job succeeds, fails, or the laptop's Ctrl-C'd mid-run. After every run,
`runpodctl pod list` should come back empty; that's the actual proof, not just an
assumption.

The launcher itself isn't a dependency on a Claude Code skill. It's vendored into
`pipeline/runpod/launch.sh`, adapted from the `runpod-jobs` skill's own script,
because anyone cloning this repo needs it to work standalone, not only inside a
session that happens to have that skill installed.

## Picking a model by running all three

Rather than argue about SDXL-Turbo versus SD1.5 versus Flux.1-schnell on paper, the
plan was to run the same small prompt set through all three and let the real
cost/speed/quality numbers decide. That turned out to be the right call, because the
practical differences were much bigger than the paper specs suggested: Flux.1-schnell
generated in about 2 seconds a prompt once it was working, against SD1.5's 25
inference steps at a noticeably slower per-image cost. The harness is identical
across all three (pod launch, prompt loop, relay transport), so comparing cost almost
nothing extra beyond running the job three times.

Licensing turned out to matter as much as speed. SDXL-Turbo's Stability AI license is
non-commercial research only. SD1.5 uses the CreativeML Open RAIL-M, broadly
permissive but with usage restrictions. Flux.1-schnell is Apache-2.0, which matches
this project's own license and is the only one of the three with no strings attached.
None of that is a blocker for personal experimentation, but it's a real input to which
model ends up as the default, not an afterthought.

## What actually broke, in order

**Spot pods stopped existing.** The launcher originally defaulted to interruptible
(spot) pods, since they're cheaper and a batch job can just re-run if reclaimed. The
first real launch attempt got a 500 back: "Spot pods are no longer offered." That
wasn't visible through the wrapper at first, because `launch.sh`'s own `set -e` was
swallowing the response body; posting the same create request directly with curl
surfaced the actual message. The default flipped to on-demand.

**GPU stock shifts by the hour, and `gpu list` lies a little.** `runpodctl gpu list`
shows GPU ids that look valid but aren't all acceptable to the create endpoint, MIG
variants in particular. The real enum only shows up in a 400's error body once you
send a bad one. `RP_GPU_TYPE` needs 2-3 comma-separated fallbacks as a matter of
course, not because any one option is unreliable but because availability genuinely
moves between regions and hours.

**One model's crash was losing everyone else's results.** `generate.py` originally
ran its tar-and-send step once, after looping through all three models. When Flux
crashed partway through a real comparison run, that took SD1.5 and SDXL-Turbo's
already-generated images down with it, since nothing had shipped yet. The fix wraps
both model-loading and per-prompt generation in their own try/except, so a failed
model records `file: None` plus its error per prompt and the loop moves on; every
other model's results still ship. `orchestrate.py`'s manifest handling was updated to
treat a `None` file as an expected failure mode, not a crash.

**`diffusers` broke on import, for reasons that had nothing to do with what we were
using it for.** Newer `diffusers` releases eagerly register a flash-attention custom
op at import time, and that registration failed against this pod image's torch build
with `infer_schema(func): Parameter q has unsupported type torch.Tensor`, even though
nothing here asks for flash attention. `diffusers==0.31.0` predates that refactor and
avoids it entirely.

**Pinning versions as ranges made pip hang for over ten minutes.** `transformers` and
`accelerate` as ranged specifiers (`>=4.44,<4.46` and similar) sent pip's resolver
into silent backtracking against whatever the base image had pre-installed, with zero
output the whole time. Exact pins turned the same install into about 13 seconds.

**Flux needed authentication, and then it needed more VRAM than it initially got.**
Flux.1-schnell is a gated HuggingFace repo: accepting its license and creating a
read-scope token are both required before it'll load. The token reaches the pod
through `RP_POD_ENV_JSON`, injected into the container's environment at create time
(`build_plan`'s `hf_token` parameter), which is the one sanctioned exception to never
putting a secret directly into a command: some things genuinely have to arrive as a
literal container env value for the job running on that container to use them. What
it can't do is leak back out, and RunPod's own pod-get/create responses echo the
`env` field verbatim, the same way they do `PUBLIC_KEY`, so every response from that
endpoint gets redacted before anything is displayed, not just when a leak seems
likely.

Getting past the `GatedRepoError` just uncovered the real constraint: VRAM. A 24GB
card OOM'd loading the weights at all, using about 22GB before it even reached
inference. Moving up to a 32GB card looked like it should be plenty of headroom, and
it OOM'd anyway, this time mid-inference, with 31.36 of 31.37GB already in use. Flux's
bf16 weights plus its inference activations sit right at the edge of 32GB; that tier
is a near miss, not real margin. The obvious next step up, an A40 at 48GB, had zero
stock in every region at the time, so the fallback list jumped to an A100 80GB
instead, which meant raising the cost cap from $0.80/hr to $1.75/hr to actually allow
it. By the time the next run launched, A40 stock had come back, so it landed there at
$0.49/hr and generated cleanly with real room to spare. `RP_GPU_TYPE` now lists the
A40 first (cheaper, if it's in stock) with two A100 variants behind it as genuine
fallbacks, not decoration.

That whole diagnosis took three separate real pods, each created and confirmed
terminated afterward. None of the failures needed a code fix, in the end. They needed
a bigger GPU and a wider net of fallbacks to land on one.

## What this looks like once it's working

A single run against all three models, three prompts each, produces a
`runs/<run_id>/manifest.json` with one entry per (model, prompt) pair: the seed image
path, whether `primitive` processed it successfully, and the resulting `.png`/`.svg`.
Flux's output for "rolling sand dunes in a vast desert, in the style of a watercolor
painting, in dramatic golden-hour lighting" took 2.05 seconds to generate and reduced
cleanly to a few hundred triangles. That's the whole point of the exercise: getting a
real image in and a real primitive-rendered image out, cheaply and repeatably,
without babysitting a pod or getting surprised by a bill.
