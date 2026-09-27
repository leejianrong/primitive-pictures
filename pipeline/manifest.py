"""Run/item data model (ADR-0004): filesystem-addressed, JSON manifest, no
database. `schema_version` is included from day one so a later phase (e.g.
video frames) can extend the item shape without breaking readers of old
manifests.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1


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
