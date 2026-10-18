# Status and handoff

Last updated 2026-10-19. Read CLAUDE.md first, then this file. Trust the code over
this file; if something here is stale, fix it.

## Where things stand

Phase 3 (video) is built end to end and merged: `orchestrate.py --video --prompts FILE`
generates clips on RunPod (FLUX still, then Wan2.2-TI2V-5B, 81 frames at 832x480), runs
`primitive -frames` once per clip, and writes a slowed preview mp4 and a schema-2 manifest.
`--video --process-only RUN_DIR` redoes the local part for free. Frame-sequence defaults are
dynamic: drop rate 0.3, new shapes per frame = drop rate x count, 500 shapes for video.

Merged since the video pipeline landed (#19):

| PR | Card | What |
|----|------|------|
| #20 | PRI-43 | Python-side timeouts on `launch.sh` up/down/logs (180/150/60 s). A timed-out `up` also attempts `down`. `relay-get` has no timeout. |
| #21 | PRI-40 | Prompt bank gains composition, lighting, lens and texture clauses on their own seeded stream. |
| #22 | PRI-45 | Video job frees FLUX before loading Wan2.2. Validated on a real A40, see below. |

## Decisions already made (do not re-litigate)

- Flux.1-schnell is the default image model, Wan2.2-TI2V-5B the video model.
- Static carry-forward with a random drop rate. Flicker is a tunable aesthetic, not a bug.
- Frame-sequence processing is one in-process Go invocation (ADR-0007).
- The final shape count and shape mode are not chosen. Do not change those defaults until
  Jian picks (see PRI-46).
- Active refinement (ADR-0012, approved 2026-10-19): build guided drops first as a baseline,
  then `Model.Refine` only if sweeps still show ghosting. Moving a refined shape to the top of
  the z-order is acceptable. Default k about 10% of the shape count. Off by default, and
  `Model.Step` stays untouched.

## Open work (Pandan board 34, ids shown as PRI-nn)

- **PRI-39** active refinement, in progress. ADR-0012 is on branch `pri-39-refine-design`
  (not merged yet). Next step is guided drops in `primitive/sequence.go` (replace
  `keepIndices` with a highest-error selection behind a flag), then sweeps to compare.
- **PRI-46** per-mode shape counts. The mode sweep in `runs/run-20261016-015648/sweeps/modes`
  used one fixed setting for every mode (n=500, add=150, drop=0.3), so weaker modes may need
  more or fewer shapes. Jian rated polygon, rotated rectangle and rotated ellipse best.
- **PRI-47** stipple: much finer points, and slightly deformed instead of perfect circles
  (bounded random factor). Today: grid 16 px on 832x480 (1560 cells), radius up to 8 px,
  500 shapes. `scripts/sweep.py` cannot pass `-grid`/`-jitter` per variant yet.

## RunPod facts that cost money to learn

- Any real launch needs Jian's explicit go-ahead. Run `pipeline/runpod/offline_video_smoke.py`
  first (setup is in its docstring). Read the addenda at the end of ADR-0001 and ADR-0008.
- Standard launch (80 GB cards):
  `cd pipeline && RP_GPU_TYPE="NVIDIA A100-SXM4-80GB,NVIDIA A100 80GB PCIe,NVIDIA RTX PRO 6000
  Blackwell Server Edition" uv run python orchestrate.py --video --max-hourly-usd 2.6
  --prompts prompts/video-scenes.txt`. There is no trailing `.` argument. An 8-clip run is
  about $0.50.
- **A40 (48 GB) works** (`run-20261018-174235`, 2026-10-19): `RP_GPU_TYPE="NVIDIA A40"` with
  `--max-hourly-usd 0.6`. Two clips, no errors, about $0.09. It is about 3x slower per clip
  (about 200 s against about 70 s), so an 8-clip run is roughly $0.31, not a fifth of the
  price. Stock was "Low". Peak VRAM was not measured.
- Check money with `runpodctl user` (look only at `clientBalance`, `currentSpendPerHr`,
  `spendLimit`) and `runpodctl billing pods`. Never print `.env` or its values. Pull a single
  value by name with `dotenv_values`, never `source .env` and never a bare `export`.
- `.env` lives only in the main checkout. A git worktree does not have it, so launch real
  runs from the main checkout.
- Launch detached and judge "my run" by `find runs -newer <marker file>`, not by the newest
  directory. Confirm `runpodctl pod list` is empty afterwards.

## Working conventions that bit us

- One branch and one git worktree per card (`../pp-worktrees/<branch>`), PR into `main`.
  Do not commit or push unless Jian says so; "commit and merge" means do both.
- No attribution lines in commit or PR messages. `make ci` must be green first.
- The pre-push hook runs the whole CI and takes a few minutes, so push in the background.
- When a PR falls behind `main` (the second of two parallel PRs always does), use
  `gh pr update-branch N`, wait for CI again, then merge. Do not use `--admin`.
- Use `pandan` with `--board 34` for this project, and the `PRI-nn` ids, not `KAN-nnnn`.
  The CLI can be slow on a cold start, so run long batches in the background.

## Where the artifacts are

`runs/` is git-ignored. `run-20261016-015648` holds the 8-scene clips plus `sweeps/` (shape
counts and per-mode comparisons) and `primitive/` (three variants per scene).
`scripts/sweep.py FRAMES OUT name:N:ADD:DROP[:MODE] ...` renders variants and stitches slowed
side-by-side mp4s.
