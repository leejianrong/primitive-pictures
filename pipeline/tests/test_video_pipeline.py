"""Video-run wiring (SLICES-phase3.md V7): primitive -frames once per clip, the
nested frame manifest, and --process-only. Everything is faked at the subprocess
boundary, so no primitive binary, ffmpeg or RunPod is needed."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import manifest
import orchestrate


def _clip_row(index: int, name: str, frames: int) -> dict:
    d = f"{index:03d}-{name}"
    return {
        "index": index,
        "prompt": name,
        "image_model": "flux-schnell",
        "video_model": "wan2.2-ti2v-5b",
        "seconds": 40.0,
        "still_file": f"{d}/still.png",
        "clip_file": f"{d}/clip.mp4",
        "frame_files": [f"{d}/frames/{i:04d}.png" for i in range(frames)],
        "error": None,
    }


def _make_run(tmp_path: Path, rows: list[dict]) -> Path:
    run_dir = tmp_path / "run-test"
    out = run_dir / "out"
    out.mkdir(parents=True)
    (out / "generation-manifest.json").write_text(json.dumps(rows))
    for row in rows:
        for f in row.get("frame_files") or []:
            (out / f).parent.mkdir(parents=True, exist_ok=True)
            (out / f).write_bytes(b"png")
    return run_dir


class FakeTools:
    """Stands in for subprocess.run: a fake `primitive -frames` that writes the same
    frames.json the real one does (optionally dying part-way), and a fake ffmpeg."""

    def __init__(self, fail_after: dict[str, int] | None = None, ffmpeg_rc: int = 0):
        self.calls: list[list[str]] = []
        self.fail_after = fail_after or {}
        self.ffmpeg_rc = ffmpeg_rc

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "ffmpeg":
            if self.ffmpeg_rc == 0:
                Path(cmd[-1]).write_bytes(b"mp4")
            return subprocess.CompletedProcess(cmd, self.ffmpeg_rc, "", "encode error")
        frames_dir, out_dir = Path(cmd[2]), Path(cmd[4])
        out_dir.mkdir(parents=True)
        names = sorted(p.name for p in frames_dir.iterdir())
        limit = self.fail_after.get(frames_dir.parent.name, len(names))
        items = []
        for i, name in enumerate(names[:limit]):
            warm = i > 0
            items.append(
                {
                    "index": i,
                    "source": str(frames_dir / name),
                    "status": "ok",
                    "output": str(out_dir / name),
                    "shapes": 100,
                    "carried": 70 if warm else 0,
                    "dropped": 30 if warm else 0,
                    "added": 30 if warm else 100,
                    "carried_fraction": 0.7 if warm else 0.0,
                    "added_fraction": 0.3 if warm else 1.0,
                    "seconds": 0.5,
                }
            )
        (out_dir / "frames.json").write_text(json.dumps(items))
        rc = 1 if limit < len(names) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "primitive: killed" if rc else "")


def _process(run_dir, fake, settings=None):
    with patch.object(subprocess, "run", side_effect=fake):
        return orchestrate.process_video_run(
            run_dir, Path("/fake/primitive"), settings, ffmpeg_bin="ffmpeg"
        )


def test_one_primitive_call_per_clip_with_dynamic_defaults(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 5), _clip_row(1, "a-fox", 3)])
    fake = FakeTools()
    m = _process(run_dir, fake)

    primitive_calls = [c for c in fake.calls if c[0] != "ffmpeg"]
    assert len(primitive_calls) == 2  # per clip, never per frame (ADR-0007)
    cmd = primitive_calls[0]
    assert cmd[0] == "/fake/primitive" and "shell" not in cmd
    assert cmd[cmd.index("-n") + 1] == "500"
    assert cmd[cmd.index("-drop-rate") + 1] == "0.3"
    assert cmd[cmd.index("-new-shapes-per-frame") + 1] == "150"  # 30% of 500: steady count
    assert "-m" not in cmd  # primitive's own default shape unless one is requested
    assert Path(cmd[cmd.index("-frames") + 1]) == run_dir / "out" / "000-a-boat" / "frames"
    assert m.ok_count == 2 and m.failed_count == 0


def test_manifest_nests_frames_with_relative_paths_and_continuity(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 4)])
    m = _process(run_dir, FakeTools())
    item = m.items[0]

    assert item.status == "ok" and len(item.frames) == 4
    assert item.frames[0].source == "out/000-a-boat/frames/0000.png"  # relative to the run dir
    assert item.frames[1].output == "out/000-a-boat/primitive/0001.png"
    assert item.still == "out/000-a-boat/still.png"
    assert item.primitive_clip == "out/000-a-boat/primitive.mp4"
    assert item.mean_carried_fraction == pytest.approx(0.7)  # warm frames only
    assert item.settings["new_shapes_per_frame"] == 150
    assert (run_dir / "out" / "000-a-boat" / "primitive.mp4").exists()

    # written to disk as a schema-2 video manifest, and it round-trips
    back = manifest.read_video(run_dir / "manifest.json")
    assert back.schema_version == manifest.VIDEO_SCHEMA_VERSION == 2
    assert back == m


def test_preview_uses_the_playback_fps_and_pads_odd_heights(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 2)])
    fake = FakeTools()
    _process(run_dir, fake, orchestrate.VideoSettings(playback_fps=6))
    ffmpeg = next(c for c in fake.calls if c[0] == "ffmpeg")
    assert ffmpeg[ffmpeg.index("-framerate") + 1] == "6"
    assert "pad=ceil(iw/2)*2:ceil(ih/2)*2" in ffmpeg[ffmpeg.index("-vf") + 1]


def test_shape_mode_and_explicit_budget_are_passed_through(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 2)])
    fake = FakeTools()
    s = orchestrate.VideoSettings(shape_count=200, shape_mode=4, drop_rate=0.5, new_shapes=7)
    _process(run_dir, fake, s)
    cmd = fake.calls[0]
    assert cmd[cmd.index("-m") + 1] == "4"
    assert cmd[cmd.index("-n") + 1] == "200"
    assert cmd[cmd.index("-new-shapes-per-frame") + 1] == "7"
    assert cmd[cmd.index("-drop-rate") + 1] == "0.5"


def test_partial_completion_is_captured_and_does_not_stop_other_clips(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 5), _clip_row(1, "a-fox", 3)])
    m = _process(run_dir, FakeTools(fail_after={"000-a-boat": 2}))

    boat, fox = m.items
    assert boat.status == "failed"
    assert len(boat.frames) == 2  # the frames finished before the crash are kept
    assert "killed" in boat.error
    assert boat.primitive_clip is None  # no preview of an incomplete clip
    assert fox.status == "ok"  # one clip's failure doesn't abort the rest
    assert m.ok_count == 1 and m.failed_count == 1


def test_pod_side_failure_is_carried_through_without_running_primitive(tmp_path):
    rows = [_clip_row(0, "a-boat", 2)]
    rows.append(
        {
            "index": 1,
            "prompt": "a-fox",
            "image_model": "flux-schnell",
            "video_model": "wan2.2-ti2v-5b",
            "still_file": None,
            "clip_file": None,
            "frame_files": [],
            "error": "video generation failed: OOM",
        }
    )
    run_dir = _make_run(tmp_path, rows)
    fake = FakeTools()
    m = _process(run_dir, fake)
    assert m.items[1].status == "failed"
    assert m.items[1].error == "video generation failed: OOM"
    assert len([c for c in fake.calls if c[0] != "ffmpeg"]) == 1


def test_a_failed_preview_is_a_warning_not_a_failed_clip(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 2)])
    m = _process(run_dir, FakeTools(ffmpeg_rc=1))
    item = m.items[0]
    assert item.status == "ok" and len(item.frames) == 2
    assert item.primitive_clip is None
    assert "preview mp4 failed" in item.warning


def test_missing_ffmpeg_is_a_warning(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 2)])
    fake = FakeTools()
    with (
        patch.object(subprocess, "run", side_effect=fake),
        patch.object(orchestrate.shutil, "which", return_value=None),
    ):
        m = orchestrate.process_video_run(run_dir, Path("/fake/primitive"))
    assert m.items[0].status == "ok" and "ffmpeg not found" in m.items[0].warning


def test_a_primitive_that_writes_too_few_frames_without_failing_is_flagged(tmp_path):
    run_dir = _make_run(tmp_path, [_clip_row(0, "a-boat", 4)])

    def short(cmd, **kwargs):
        res = FakeTools(fail_after={"000-a-boat": 2})(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, res.stdout, res.stderr)

    m = _process(run_dir, short)
    assert m.items[0].status == "failed"
    assert "2 of 4 frames" in m.items[0].error


def test_process_only_video_runs_the_whole_sequence_without_runpod(tmp_path, capsys):
    """`--video --process-only RUN_DIR` is the free path: no launcher, no prompts needed."""
    run_dir = _make_run(tmp_path, [_clip_row(i, f"scene-{i}", 5) for i in range(5)])
    fake = FakeTools()
    with (
        patch.object(orchestrate, "load_dotenv"),
        patch.object(orchestrate, "_run_launcher", side_effect=AssertionError("RunPod used")),
        patch.object(orchestrate, "resolve_primitive_bin", return_value=Path("/fake/primitive")),
        patch.object(orchestrate.shutil, "which", return_value="ffmpeg"),
        patch.object(subprocess, "run", side_effect=fake),
    ):
        rc = orchestrate.main(["--video", "--process-only", str(run_dir), "--shape-count", "300"])
    assert rc == 0
    assert "5 ok, 0 failed" in capsys.readouterr().out
    assert fake.calls[0][fake.calls[0].index("-n") + 1] == "300"
    assert manifest.read_video(run_dir / "manifest.json").ok_count == 5


def test_read_video_rejects_an_image_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    manifest.write(path, manifest.Manifest(run_id="run-x"))
    with pytest.raises(ValueError, match="not a video-run manifest"):
        manifest.read_video(path)
