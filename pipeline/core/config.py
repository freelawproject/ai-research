"""Dataset paths and canonical dimensions.

Every engine saw the identical 1700x2200 black-redacted render of each page;
all bboxes across the pipeline live in that pixel space.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RENDER_W = 1700
RENDER_H = 2200


@dataclass(frozen=True)
class Dataset:
    """One dataset under data/datasets/<name> (layout: docs/pipeline.md)."""

    name: str
    root: Path

    @property
    def redacted(self) -> Path:
        return self.root / "redacted"

    @property
    def page_png(self) -> Path:
        return self.root / "page_png"

    def engine_dir(self, engine: str) -> Path:
        return self.root / "engines" / engine


def dataset(data_root: Path, name: str) -> Dataset:
    return Dataset(name=name, root=data_root / "datasets" / name)
