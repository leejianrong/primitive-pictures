# ADR-0001: RunPod diffusion step runs as a vendored, self-contained batch job

- Status: Accepted
- Date: 2026-09-26
- Deciders: Jian, Claude (planning session)

## Context

Phase 1 needs a step that turns a batch of text prompts into seed images using a
diffusion model, on rented GPU compute, without leaking a bill (RunPod has no
native idle-terminate, no max-lifetime, and no per-pod spend cap — see the
runpod-jobs skill). Two shape questions and one model question were open:

1. Does the pod run as a long-lived service (HTTP inference server) the driver
   calls per-prompt, or as a one-shot batch job that processes the whole prompt
   set and exits?
2. Does the project depend on the `runpod-jobs` Claude Code skill's local
   `scripts/rp` wrapper, or carry its own safety logic?
3. Which diffusion model, and at what resolution?

On (3), the user's answer was to compare rather than pick on paper: SDXL-Turbo,
SD1.5, and Flux.1-schnell (SDXL full dropped — too many GPU-seconds per image for
a "keep it cheap" budget), with the resolution note that 256×256 photos are fine.

## Decision

**Shape:** the RunPod step is a one-shot batch job, not a service. The pod boots
once per pipeline run (not once per prompt), a script on the pod reads the entire
prompt file, generates every image, ships them all back over the `runpodctl`
relay (no public IP assumed — see the runpod-jobs skill's data-transport
reference), and the pod self-terminates. This matches the skill's explicit
guidance against using RunPod as a request-path dependency, and it amortizes pod
boot time (image pull, model load) across the whole batch instead of paying it
per prompt.

**Ownership:** the project vendors its own cost-safety launcher
(`pipeline/runpod/launch.sh`, adapted from the runpod-jobs skill's `scripts/rp`)
rather than depending on `~/.claude/skills/runpod-jobs/scripts/rp`. That path only
exists inside a Claude Code session with this skill installed; anyone else who
clones this repo (a CI job, a future contributor, Jian on a different machine)
needs the pipeline to work standalone. The vendored launcher keeps the same
non-negotiables: a hard max-lifetime + idle-watchdog dead-man's-switch armed as
the literal first thing the pod's start command runs, an account spend-cap
check before launch, and a driver-side `trap … EXIT INT TERM` that terminates
the pod on success, failure, or Ctrl-C.

**Model selection:** slice 1 does not hardcode one model. It runs the same small
prompt set through all three candidates (SDXL-Turbo, SD1.5, Flux.1-schnell) via a
`--model` parameter on the same batch-job script, and the choice of default is
made from the real cost/quality/speed tradeoff produced by that comparison, not
from paper specs. This is cheap to do (the harness — pod launch, prompt loop,
relay transport — is identical across all three; only the model-loading branch
differs), and it directly answers the open fork with evidence instead of a guess.

**Resolution:** generate at each model's practical native/economical resolution
(typically 512×512-class for SD1.5/SDXL-Turbo, native for Flux.1-schnell) rather
than literally forcing 256×256 output. Pixel-diffusion models trained at a given
resolution tend to produce structurally broken images well below it, and there's
no cost or quality reason to fight that: `primitive` already resizes its input to
256px by default (the `-r` flag, currently defaulted in `main.go`), so the
256×256 requirement is satisfied by that existing downsizing step, not by asking
the diffusion model to natively output tiny images. This is a deliberate,
flagged deviation from the literal ask — the underlying goal (don't pay for or
need high-resolution photos) is preserved; the mechanism achieving it moved to
where it already lived in the codebase.

**Licenses of the three candidates** (check before picking a default, not
after):

| Model | License | Note |
|-------|---------|------|
| SDXL-Turbo | Stability AI Non-Commercial Research Community License | Research/personal use only; commercial use needs a separate Stability AI license |
| SD1.5 | CreativeML Open RAIL-M | Broadly permissive, including commercial use, subject to the RAIL-M usage restrictions (no illegal/harmful content generation) |
| Flux.1-schnell | Apache-2.0 | Fully permissive — matches this project's own license |

This project's own code is Apache-2.0; a model whose license restricts
commercial/redistribution use doesn't change the *code's* license, but it does
constrain what can honestly be claimed about outputs produced with it if this
project or its outputs are ever shared more broadly. Not a blocker for
experiment-scale personal use of any of the three, but the license column is a
real input to slice 1's model choice, not just quality/cost.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Long-lived inference service pod (AUTOMATIC1111/ComfyUI API), driver calls it per prompt | Matches the runpod-jobs skill's explicitly discouraged shape (RunPod as a request-path dependency); pod stays up and bills between calls; more moving parts (HTTP server, proxy quirks) for no benefit at this batch size |
| Depend on the runpod-jobs skill's `scripts/rp` directly | Not present outside a Claude Code session with this skill installed; makes the pipeline non-standalone |
| Force literal 256×256 diffusion output | Below most models' effective training resolution; produces visibly broken images for no cost savings once `primitive`'s existing resize is accounted for |
| Lock in one model now (e.g. SDXL-Turbo) without comparing | Faster to plan, but the user explicitly asked to compare; picking blind risks committing to a worse cost/quality point when a three-way comparison costs barely more than a one-way test |

## Consequences

- Slice 1's build plan is larger than "call one model" — it needs a
  `--model` switch and a small side-by-side comparison step before the default is
  locked (tracked in SLICES.md V1).
- The vendored launcher duplicates logic that already exists in the runpod-jobs
  skill; it must be kept in sync by hand if that skill's safety recipe changes.
  Accepted: standalone-usability outweighs that maintenance cost here.
- Diffusion generation resolution is decoupled from `primitive`'s input
  resolution by design — if `primitive`'s default resize value ever changes, the
  pipeline's cost assumptions should be revisited, not just the flag value.
- **Container disk sizing (2026-09-27 addendum):** `--model compare`
  downloads all three models' weights onto one pod. `launch.sh`'s own
  built-in default (`RP_CONTAINER_DISK_GB`, 20GB) is sized for a single small
  job, not this — Flux.1-schnell alone (bf16 transformer + T5-XXL text
  encoder) is in the tens-of-GB range. `config.DEFAULT_CONTAINER_DISK_GB` is
  80GB, threaded through as its own `--container-disk-gb` flag rather than
  left to `launch.sh`'s default. Caught by comparing against another
  project's RunPod `.env.example` that happened to size its own disk
  explicitly — worth double-checking against real download sizes before
  the first live run, since this is still an estimate, not a measurement.
- **Spot pods discontinued; GPU type list needs fallbacks (2026-09-27
  addendum, from the first real launch attempt):** `launch.sh` defaulted to
  `interruptible: true` (spot); RunPod's pod-create endpoint now 500s that
  with `"Spot pods are no longer offered"` — confirmed by posting the exact
  create body directly. Default flipped to on-demand (`false`) in
  `launch.sh`. Separately, `runpodctl gpu list`'s `gpuId` values aren't all
  valid for pod creation — a MIG variant that `gpu list` shows 400s at
  create time; the endpoint's actual enum only surfaces in a 400's error
  body. GPU availability also shifts hour to hour, so `RP_GPU_TYPE` should
  always carry 2-3 comma-separated fallback ids, not one. `.env.example`'s
  default cost cap moved 0.60 → 0.80 to clear real on-demand pricing for a
  GPU with enough VRAM for Flux.1-schnell (RTX PRO 4500 Blackwell, 32GB,
  quoted $0.72/hr secure on this date). Verified end-to-end: a real pod was
  created (id, cost, and dead-man's-switch-free dockerStartCmd all
  confirmed via the REST response), then terminated and confirmed gone via
  `runpodctl pod list` — total spend for the check was a few seconds at
  $0.72/hr.
- **Flux.1-schnell needs HF authentication; generate.py isolates per-model
  failures (2026-09-27 addendum, from the real 3-model comparison run):**
  SD1.5 and SDXL-Turbo both succeeded end-to-end on the first real
  production run. Flux.1-schnell failed with `huggingface_hub.errors.
  GatedRepoError: 401` — it's a gated HF repo requiring (1) accepting its
  license at huggingface.co and (2) an access token. Neither is wired up
  yet: `launch.sh`'s pod-create body has no `env` field, so there's
  currently no way to hand a token into the container. Separately, this run
  exposed a real robustness bug: `generate.py`'s tar-and-send step only ran
  after its full model loop, so Flux's crash lost SD1.5 and SDXL-Turbo's
  already-generated images too — nothing was ever shipped back. Fixed by
  isolating both model-load and per-prompt generation in try/except inside
  the loop; a failed model now gets `file: None` + its error recorded per
  prompt, and every other model's results still ship. `orchestrate.py`'s
  `process_run` updated to handle a `None` file (no seed image, so no
  `primitive` invocation — the pod's error carries straight through).
  Verified: intentionally broke the new `None`-handling branch and
  confirmed the test fails with a clear `TypeError` before re-fixing it.
- **HF auth fixed, then Flux OOM'd on a 24GB card that outranked the A40 by
  luck of the draw (2026-09-27 addendum, from a follow-up diagnostic pod):**
  `HF_TOKEN` now reaches the pod via `RP_POD_ENV_JSON` (`build_plan`'s
  `hf_token` param), and a diagnostic run confirmed the `GatedRepoError` is
  gone — Flux authenticates and loads its weights. It then failed with
  `CUDA out of memory. Tried to allocate 90.00 MiB. GPU 0 has a total
  capacity of 22.03 GiB...`. The GPU was the RTX PRO 4000 Blackwell (24GB),
  which was second in `RP_GPU_TYPE`'s fallback list ahead of the A40 (48GB).
  RunPod tries the list in listed order and stops at the first one it can
  rent, not the biggest one, so a smaller card earlier in the list wins even
  when a bigger one is also listed. `.env`/`.env.example`'s `RP_GPU_TYPE`
  dropped the 24GB tier entirely: `NVIDIA RTX PRO 4500 Blackwell,NVIDIA A40`
  (32GB and 48GB only), so any run that includes Flux always lands on a card
  with real headroom rather than one that OOMs 90MiB short. Not yet
  re-verified with a full comparison run including a successful Flux image.
- **32GB wasn't enough either; settled on 48GB+ after two more real OOMs
  (2026-09-27 addendum, from two follow-up verification runs):** re-ran with
  the 32GB-tier fallback above and it OOM'd again, this time mid-inference
  rather than at load: `CUDA out of memory. Tried to allocate 18.00 MiB. GPU
  0 has a total capacity of 31.37 GiB of which 1.75 MiB is free... this
  process has 31.36 GiB memory in use.` Flux's bf16 weights plus inference
  activations sit right at the edge of 32GB — that tier is a near miss, not
  headroom. Tried the A40 (48GB) next and RunPod's create endpoint 500'd with
  `"There are no instances currently available"`; `runpodctl gpu list`
  confirmed the A40 has zero stock in every region right now (it's simply not
  in the catalog as available). Fell back further to an A100 80GB (real
  quoted $1.59/hr secure), which required raising
  `config.DEFAULT_MAX_HOURLY_USD` from 0.80 to 1.75 to clear the cost cap.
  `RP_GPU_TYPE` is now `NVIDIA A40,NVIDIA A100-SXM4-80GB,NVIDIA A100 80GB
  PCIe` (A40 first in case its stock returns — it's cheaper — with two real
  80GB fallbacks). On the actual run, A40 stock had come back by the time the
  job launched, landed there at $0.49/hr, and Flux.1-schnell generated
  successfully in 2.05s with real headroom to spare. Verified end-to-end: a
  real 288KB image was produced, `primitive` processed it into triangles, and
  both were visually inspected. Total for this round of diagnosis: three real
  pods created and terminated (confirmed via `runpodctl pod list` returning
  `[]` after each), all under a few minutes each.
- **Model selection resolved: Flux.1-schnell (2026-09-27 addendum, from the
  real 3-model comparison run, `runs/run-20260927-113021`):** same 3 prompts
  across SD1.5, SDXL-Turbo, and Flux.1-schnell on one pod ($0.49/hr A40, 9/9
  images ok, all under 2.5s each — negligible cost). All three produced usable
  `primitive` source material; none was a clear quality loser. Flux.1-schnell
  won on two things that don't wash out at this scale: the most consistently
  sharp/well-composed output across all 3 prompts, and its license (Apache-2.0)
  — the only one of the three with no commercial-use restriction. SDXL-Turbo's
  license (Stability AI Non-Commercial Research Community License) rules it
  out for this project's `bot/` roadmap; SD1.5's OpenRAIL-M is more permissive
  but still carries behavioral-use restrictions Apache-2.0 doesn't. Recorded as
  `config.DEFAULT_MODEL` and the `orchestrate.py --model` CLI default; still
  selectable via `--model sd15|sdxl-turbo|compare`. Closes the open fork this
  ADR deferred above ("the choice of default is made from the real
  cost/quality/speed tradeoff... not from paper specs").
- **A pod sat silently `awaiting_container` for 35+ minutes with zero log
  output during SLICES-phase3.md V5's real validation run (2026-09-27,
  `ppics-video-run-20260927-184957`, pod `quid61fzvawmpg`):** the first sign
  anything was wrong was a human manually polling `runpodctl pod
  list`/`pod logs` — the driver itself (`orchestrate.execute`/
  `execute_video`) had no visibility into "did the container even start,"
  only the same ~40-minute `RP_MAX_LIFETIME_SECS` deadline used for the
  whole job. That deadline has to be long enough for a real job to finish,
  which makes it useless as an early warning for a boot that's stuck before
  the job ever runs. Fixed by adding `orchestrate._wait_for_boot()`: after
  `up` succeeds, poll `launch.sh logs` (short interval, few minutes) and
  fail fast with a clear error — pod still torn down via the existing
  `finally` block — if the pod produces *no* log output in that window,
  rather than waiting out the full job deadline to learn the same thing.
  Wired into both `execute()` and `execute_video()`. This directly
  implements the runpod-jobs skill's own "do an early sanity check within
  the first 1-3 minutes" guidance, which this project's driver hadn't
  actually built in until this incident forced the question. Root cause of
  the stuck pod itself (cold image pull on this particular host, most
  likely, given the same image had booted quickly in earlier real runs)
  was not conclusively diagnosed — the boot-timeout fix bounds the cost of
  not knowing, rather than needing to know for certain. The stuck pod was
  terminated manually (`runpodctl pod delete`, confirmed gone via `pod
  list`) rather than left to run out its own deadline, once the decision
  was made to fix the gap before retrying.
- **`_wait_for_boot()` was itself silent and undiagnostic — fixed same day,
  before the retry:** the first version polled with no output at all until
  either success or a bare "no output" failure, which is exactly the kind
  of blind wait this ADR's own dead-man's-switch reasoning argues against
  elsewhere. Two additions: (1) a heartbeat line printed on every poll, so
  the wait is visible in real time instead of looking hung; (2) on timeout,
  the pod's `system`-source logs (`runpodctl pod logs --source system` —
  RunPod's own platform narration: image pull progress, container
  create/start, distinct from the job's own `container`-source output,
  discovered via `runpodctl pod logs --help` while investigating the
  incident above) are fetched and printed as part of the failure, so a
  stuck pull is visible in the error itself rather than requiring a human
  to separately think to check it.
- **`rp_api()`'s `curl` call had NO timeout at all — a real hang, found on
  the very next retry (2026-09-27, pod `qazo1pqzmcliz6`, an A100 rented
  after switching GPU tiers to rule out an A40-specific issue):** the pod
  was created, then RunPod itself reported it `runtime_unavailable` and
  reclaimed it within ~20 seconds — a different, platform-side failure from
  the stuck-boot incidents above. That part was handled fine. What wasn't:
  the *local driver process* then hung for 30+ minutes with zero CPU
  growth (confirmed via `ps`, not just a long-but-legitimate wait) and had
  to be killed by hand (`kill -9`) — `runpodctl pod logs <id>` against the
  now-deleted pod returned instantly with a clean 404 when tested directly
  (`timeout 20 runpodctl pod logs …` → `pod not found` in under a second),
  so the hang wasn't there. It traced to `find_pod_id()`'s `rp_api GET
  /pods` call: `rp_api()`'s `curl` invocation had never had a
  `--connect-timeout`/`--max-time` set, at all, for any of its call sites
  (pod create, delete, status, list) — a single network stall between the
  driver and RunPod's API hangs the whole process indefinitely, with
  nothing to interrupt it. This is a more serious gap than either boot-check
  fix above: it can hang `cmd_down` itself, the one call that's supposed to
  unconditionally guarantee teardown. Fixed by adding `--connect-timeout 10
  --max-time 30` to `rp_api()`'s single shared `curl` call, which covers
  every call site at once. No cost impact from this specific incident — the
  pod was already gone (RunPod's own reclaim) before the local hang started,
  so nothing kept billing while the driver was stuck — but the *next* time
  this class of bug fires, a still-running pod easily could be. A Python-side
  `subprocess.run(..., timeout=…)` on `_run_launcher`'s shorter subcommands
  (`up`/`down`/`logs`, not `relay-get`, which is long-running by design) is
  a reasonable further defense-in-depth layer, logged as a follow-up rather
  than built immediately — the root cause above is what actually let this
  happen, and is now fixed.
- **A job that dies on the pod takes its logs with it, and `relay-get` kept
  polling the dead pod (2026-10-15, A100 attempt 6):** the pod booted,
  started the job, then vanished — the wrapper's `wait "$JOB"; terminate "job
  exited"` deletes the pod the instant the job exits, crash or not, and a
  deleted pod's logs are gone. `relay-get` overwrote its log capture on every
  poll (so the last real lines were replaced by a `pod not found` reply) and
  never noticed the pod was gone, so the driver sat in the loop until the
  full 40-minute deadline, with nothing recorded about why the job died. The
  driver was interrupted by hand. Fixed on both ends: (1) `relay-get` now
  saves each real log capture to `<run_dir>/pod.log` (error replies, which
  start `{"error"`, no longer overwrite it), recognises the `not_found` reply,
  and fails immediately with the last 40 log lines in the error; the poll
  interval dropped from 10s to 5s (`RP_RELAY_POLL_SECS`). (2) The pod wrapper
  now lingers `RP_EXIT_GRACE_SECS` (default 30s, about a cent and a half on an
  A100) after the job exits before self-terminating, so a crash's final
  traceback is still readable; the max-lifetime guard keeps running during
  it, so the cost ceiling is unchanged. A job's success vs failure can't be
  told apart at that point (the job runs in a `( … | tee )` subshell whose
  status is `tee`'s), which is why the grace applies to both. Two offline
  tests drive `relay-get` against a fake `runpodctl` on PATH (no network),
  and were confirmed to fail when the log-saving line is disabled.
- **A cost cap refusing a pod is the safety net working, not a failure
  (2026-10-16):** an A100 that quoted $1.79/hr against the $1.75
  `DEFAULT_MAX_HOURLY_USD` was terminated by `launch.sh` within seconds,
  before the job ran. A100 80GB pricing moved from the $1.59 quoted on
  2026-09-27. The cap was raised for that run with `--max-hourly-usd 2.0`
  rather than by editing the default, with the worst case (40 minutes at
  $2.00/hr, about $1.33) checked against the approved validation budget.
- **A pod-create failure printed only "HTTP 500" (2026-10-16):** `set -e`
  discarded the error body along with the failed call, so a transient
  "no capacity" 500 was indistinguishable from a rejected field. `cmd_up` now
  prints RunPod's reason (length-capped, with every value from the pod env
  JSON redacted first, since the API echoes `env` back and the HF token lives
  there). A test with a fake `curl` pins both the message and the redaction.
