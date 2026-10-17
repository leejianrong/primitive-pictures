"""Run/item data model (ADR-0004): filesystem-addressed, JSON manifest, no
database. `schema_version` is included from day one so a later phase (e.g.
video frames) can extend the item shape without breaking readers of old
manifests.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1  # image runs (Manifest)
VIDEO_SCHEMA_VERSION = 2  # video runs (VideoManifest): frame items nested under a clip


@dataclass
class Item:
    """One (prompt, model) seed image and what happened when `primitive` ran
    on it. Paths are relative to the run directory, not absolute -- a run
    directory should be relocatable/inspectable on its own.
    """

    index: int
    model: str
    prompt: str
    seed_image: str
    status: str  # "ok" | "failed"
    primitive_png: str | None = None
    primitive_svg: str | None = None
    error: str | None = None
    generation_seconds: float | None = None


@dataclass
class Manifest:
    run_id: str
    items: list[Item] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.items if item.status == "ok")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")


def write(path: Path, manifest: Manifest) -> None:
    payload = asdict(manifest)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def read(path: Path) -> Manifest:
    payload = json.loads(path.read_text())
    items = [Item(**item) for item in payload["items"]]
    return Manifest(
        run_id=payload["run_id"],
        items=items,
        schema_version=payload["schema_version"],
    )


@dataclass
class FrameItem:
    """One processed frame of a clip: mirrors one entry of primitive's own
    `frames.json` (ADR-0007, ADR-0010). `status` is "ok" (processed this run)
    or "resumed" (already on disk, skipped). Paths are relative to the run
    directory.
    """

    index: int
    source: str
    status: str
    output: str
    shapes: int = 0
    carried: int = 0
    dropped: int = 0
    added: int = 0
    carried_fraction: float = 0.0  # shape-continuity metric (ADR-0010): observability only
    added_fraction: float = 0.0
    seconds: float = 0.0


@dataclass
class VideoItem:
    """One prompt's clip: the generated frames, what `primitive -frames` did
    with them, and the slowed-down preview. `status` is "ok" when every frame
    was processed and "failed" otherwise; frames finished before a failure are
    still listed, since primitive writes them incrementally (ADR-0007).
    """

    index: int
    prompt: str
    image_model: str
    video_model: str
    status: str  # "ok" | "failed"
    still: str | None = None
    source_clip: str | None = None  # the model's own mp4
    primitive_dir: str | None = None
    primitive_clip: str | None = None  # slowed mp4 of the primitive frames
    frames: list[FrameItem] = field(default_factory=list)
    mean_carried_fraction: float | None = None  # mean over warm frames; None if no warm frame
    settings: dict = field(default_factory=dict)  # shape_count, new_shapes, drop_rate, ...
    error: str | None = None
    warning: str | None = None  # e.g. the preview mp4 failed but frames are fine
    generation_seconds: float | None = None
    primitive_seconds: float | None = None


@dataclass
class VideoManifest:
    run_id: str
    items: list[VideoItem] = field(default_factory=list)
    schema_version: int = VIDEO_SCHEMA_VERSION
    kind: str = "video"

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.items if item.status == "ok")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")


def write_video(path: Path, manifest: VideoManifest) -> None:
    path.write_text(json.dumps(asdict(manifest), indent=2) + "\n")


def read_video(path: Path) -> VideoManifest:
    payload = json.loads(path.read_text())
    if payload.get("kind") != "video":
        raise ValueError(f"{path} is not a video-run manifest (kind={payload.get('kind')!r})")
    items = []
    for raw in payload["items"]:
        frames = [FrameItem(**f) for f in raw.pop("frames", [])]
        items.append(VideoItem(**raw, frames=frames))
    return VideoManifest(
        run_id=payload["run_id"],
        items=items,
        schema_version=payload["schema_version"],
    )
