# Primitive Pictures — Phase 3: Slices

Vertical increments, numbered on from phase 1's V1-V4. Each ends in something
you can demonstrate. V5 confronts the riskiest unknown: a new RunPod job type
generating real video, at a real (if small) cost, on a model chosen from
research rather than a real bake-off.

## V5: RunPod video-gen job mechanism + Wan2.2 validation

**Delivers:** R1 (fully), R0 (partial — proves the hardest piece), R5

**Build plan**

1. ✅ Added `wan2.2-ti2v-5b` as a new `VideoBackend` entry in
   `pipeline/config.py`'s own `VIDEO_BACKENDS` registry, separate from the
   image `Backend`/`BACKENDS` registry (ADR-0008's revised consequences: the
   two shapes differ enough — a conditioning image and a frame sequence vs.
   a single prompt-to-image call — that forcing one dataclass onto both
   wasn't worth it).
2. ✅ Wrote the pod-side video generation step (`runpod/generate_video.py`):
   generates a still via the existing image backend (reusing
   `generate.py`'s `build_pipeline`/`generate_one`, not duplicating them),
   then animates it with `WanImageToVideoPipeline` (I2V mode).
3. ✅ Frames are saved directly from the pipeline's own output
   (`output.frames[0]`) as `out/<item>/frames/<index>.png` — no ffmpeg
   extraction step needed (correction from this slice's original plan). A
   viewable `.mp4` is exported separately via diffusers' `export_to_video`
   (needs `imageio[ffmpeg]`, added to a new, separate
   `pod-requirements-video.txt` — kept apart from the image job's
   `pod-requirements.txt` since that file's `diffusers==0.31.0` pin predates
   Wan2.2 support).
4. ✅ Extended the driver side (`orchestrate.py`'s `build_video_job_cmd`,
   `build_video_plan`, `execute_video`) to embed the new pod-side files, launch
   the job via the same `launch.sh`/relay mechanism as V1, and unpack the
   still/frames/clip into `runs/<run_id>/out/` — a new `--video` CLI mode,
   `--video-model` selecting the backend (only `wan2.2-ti2v-5b` registered so
   far). Does not invoke `primitive` — that's V6/V7.
5. ✅ Manual validation run, 2026-10-15 (`runs/run-20261015-171744`): one
   prompt, still → 41 frames + clip, on a real RTX PRO 6000 Blackwell pod,
   with explicit go-ahead. It took eleven launch attempts, each failure
   fixed and recorded (ADR-0001 and ADR-0008 addenda). Measured on the
   successful run, not estimated: container up in 2 s, dependency install
   7 s, FLUX.1-schnell download+load 3 m 48 s, Wan2.2 download+load about
   4 m 07 s, still + 41-frame video generation 33.8 s, pod lifetime about
   9 m 20 s, about $0.39 at $2.49/hr. Output: a 512×512 still, 41 frames at
   832×480, a 106 KB mp4, 16 MB total.
6. ✅ `runpodctl pod list` returned `[]` after the run, and the driver logged
   `verified: no … pod remains`.

**Demo:** `pipeline/orchestrate.py --video --prompts one-prompt.txt` boots one
pod, produces a still, a directory of per-frame PNGs, and a viewable `.mp4`
(for inspection only) under `runs/<run_id>/`, and terminates. `runpodctl pod
list` shows nothing running afterward. Done on 2026-10-15; the frames show a
coherent animation of the still (boat, reflection and sky consistent through
frame 40).

**Rests on assumptions:** Q12 (Wan2.2 ungated) — if wrong, reuses ADR-0001's
existing `HF_TOKEN` wiring. Q16 (short clip/frame-count default) — if wrong,
a parameter change, not a redesign: 41 frames took 33.8 s to generate, so
there is headroom for longer clips. Q12 (ungated) is still unverified: the
`HF_TOKEN` was present, so a gated repo would not have shown. The pinned
dependency set (`diffusers==0.35.2`, `transformers==4.45.2`, torch 2.8.0
image) is now proven on a real GPU.

### Test plan

#### End-to-end

- Given one prompt, a `--video` run produces a still, per-frame PNGs, and a
  viewable clip under `runs/<run_id>/`, and `runpodctl pod list` shows the
  pod terminated within 60 seconds of the script exiting. ✅ Met on
  2026-10-15 (the driver verified teardown itself, and `pod list` was empty).

#### Integration

- ✅ The video-gen job command embeds all 5 files (generate.py,
  generate_video.py, config.py, pod-requirements-video.txt, prompts.json)
  recoverably and round-trips the image/video model selection exactly
  (`test_build_video_job_cmd_embeds_files_recoverably`).
- ✅ `execute_video` tears down the pod even when the relay pull fails, and
  correctly counts ok/failed rows from the pod's own generation manifest
  (`test_execute_video_always_tears_down_even_if_relay_get_fails`,
  `test_execute_video_counts_ok_and_failed_rows`).

#### Unit

- ✅ The video backend registry rejects an unknown video model with a clear
  error, and every registered video backend's `num_frames` satisfies Wan's
  `4k+1` temporal-VAE constraint (`test_config.py`).
- ✅ `build_video_plan` rejects an unknown image or video model before ever
  reaching RunPod (`test_build_video_plan_rejects_unknown_*`).

## V6: `primitive/` frame-sequence mode with static carry-forward warm start

**Status: ✅ done 2026-10-16 (PR #15).** Built as planned, plus a `-drop-rate`
(random fraction of the previous frame's shapes discarded before the new ones
are searched, so the count settles near `new-shapes-per-frame / drop-rate`
instead of growing without bound). `-resume` skips the leading finished frames
and restarts the next one cold (shapes aren't serialized, ADR-0007). One
background for the whole sequence, so carried alpha shapes composite
identically. Output: `-out DIR` gets per-frame SVG+PNG (written atomically) and
`frames.json`. Reviewed on real frames: dynamic settings (drop about 30%) look
better than steady ones (drop 10%); more shapes (500) look cleaner and less
jittery, fewer (200) more abstract. Default the CLI to dynamic.

**Delivers:** R2 (fully), R3 (partial — per-frame item schema)

**Build plan**

1. Add a new exported entrypoint in `primitive/` that, given an ordered list
   of target frame images and a per-frame new-shape budget: processes frame 0
   with today's existing cold-start `Model`, then for each later frame,
   constructs a new `Model` seeded from the previous frame's finished
   `Shapes`/`Colors`/`Scores` (rendered onto the new frame's target for
   scoring), and calls the existing `Model.Step` the capped number of times
   for that frame's new-shape budget (ADR-0007, ADR-0009).
2. Add a new `main.go` CLI mode that resolves an ordered frame list from a
   directory and drives the new entrypoint, writing each frame's PNG/SVG
   incrementally as it completes, and supports `--resume` to skip
   already-completed frames on a rerun.
3. New CLI flags: `--frames <dir>`, `--new-shapes-per-frame <n>` (the
   continuity/flicker dial), following the existing `-n`/`-m` flag
   conventions.
4. Define the per-frame item shape (index, status, timing) that V7's
   pipeline-side manifest will carry.

**Demo:** `primitive --frames examples/test-frames/ --out out/frames/ -n 150
--new-shapes-per-frame 25` processes a small fixed sequence of frames;
largely-unchanged image regions visibly keep similar shapes across frames
(spot-checkable by eye) instead of an independently-randomized layout each
frame.

**Rests on assumptions:** ADR-0007's in-process design — if a future need for
per-frame process isolation emerges, the seeding logic (step 1) carries over
unchanged; only the driving loop (step 2) would move.

### Test plan

#### End-to-end

- Running the new mode against a fixed 3-frame test sequence produces 3
  output files with no error, completing incrementally — killing the process
  after frame 2 leaves frames 0-1 on disk.

#### Integration

- `--resume` against a partially-completed output directory skips
  already-done frames and only processes the remainder.

#### Unit

- Given two adjacent fixed frames and a fixed RNG seed, seeding frame 2's
  `Model` from frame 1's shape list reproduces frame 1's shapes exactly
  before any new shape is added (carry-forward is lossless).
- The new-shape budget is respected exactly — no more, no fewer shapes added
  per frame than requested.

## V7: Pipeline wiring for the video-run manifest

**Delivers:** R0 (fully), R3 (fully)

**Build plan**

1. Extend `pipeline/manifest.py` with a frame-item schema nested under a
   video-run parent item (prompt, model, per-frame status, continuity stats
   placeholder for V8), `schema_version` bumped per ADR-0004's
   design-for-extension.
2. Extend `pipeline/orchestrate.py` with a `--video` mode so, after V5's
   frames are relayed back, it invokes V6's new `primitive` frame-sequence
   mode **once per run** (not once per frame) — a single subprocess call over
   the whole frame directory, same argv-list-no-shell discipline as V2.
3. `--video --process-only RUN_DIR`, mirroring V2's `--process-only`: re-run
   the frame-sequence step against an already-populated run directory,
   skipping RunPod entirely.

**Demo:** manually populate a `runs/<id>/out/frames/` directory (a stand-in
for what V5 would have relayed back) plus a stub
`generation-manifest.json`, then `orchestrate.py --video --process-only
runs/<id>` runs the whole frame sequence through V6's mode in one call and
writes a complete video-run manifest.

**Rests on assumptions:** none new — structurally rests on V5 and V6, though
each is independently testable via its own fixture/`--process-only` path.

### Test plan

#### End-to-end

- `--video --process-only` against a directory with 5 valid frames produces
  a manifest with 5 `ok` frame items and viewable outputs, spending nothing.

#### Integration

- The single-subprocess-per-run invocation of V6's mode is built correctly
  (right binary, right flags, right frame ordering), and its exit/partial-
  completion state is captured into the manifest.

#### Unit

- The video-run manifest round-trips (write then read) with the extended
  schema and preserves `schema_version`.

## V8: Shape-continuity metric

**Status: ✅ done 2026-10-16 (PR #16).** `primitive.MeasureContinuity` compares
two frames' shapes by identity (committed shapes are immutable and carried
shapes are shared, not copied). Surfaced as `carried_fraction`/`added_fraction`
per frame in `frames.json`, in the `-v` line, and as an always-printed
run-level summary line. Demo: add 10/drop 10% reports 90% carried, add 40/drop
40% reports 60%. The run-level summary is a stderr line for now; V7 folds it
into the manifest.

**Delivers:** R4

Independent of V5-V7 — pure Go function over two shape lists, buildable in
any order relative to the others, same independence pattern as phase 1's V4.

**Build plan**

1. Implement a pure function in `primitive/` comparing two consecutive
   frames' shape lists: the fraction carried unchanged vs. newly added
   (ADR-0010).
2. Surface it per-frame in V6's output, and roll it into V7's run-level
   manifest summary.
3. Report it in a way visible without reading code (a printed summary line,
   or a manifest field), so the `--new-shapes-per-frame` dial's effect is
   observable and tunable.

**Demo:** running V6's mode at two different `--new-shapes-per-frame` values
on the same test sequence shows the metric moving in the expected direction
(higher budget → lower carried-shape fraction).

**Rests on assumptions:** none — direct implementation of the decided ADR-0010
answer.

### Test plan

#### End-to-end

- Given a fixed two-frame carry-forward sequence, the reported carried/added
  fractions match a hand-computed expectation.

#### Integration

- N/A — no external dependency for this slice.

#### Unit

- Given two shape lists differing by a known number of added shapes, the
  metric returns the exact expected fractions.
- An identical pair of shape lists reports 100% carried, 0% added.
