"""Dataset paths and canonical dimensions.

Every engine saw the identical 1700x2200 black-redacted render of each page;
all bboxes across the pipeline live in that pixel space.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RENDER_W = 1700
RENDER_H = 2200

# Datasets that WITHHOLD the LightOn tiebreak combinations: they present
# the direct three-way votes only. A dataset is listed here when its
# crop cache is not complete enough to compare fairly.
#
# This is a reporting decision, not a capability one: a tiebreak
# combination reads cached crops, and an incomplete cache turns every
# uncovered dispute into an honest no-vote. Side by side with a vote
# combination that has all its inputs, that reads as a WORSE combination
# rather than an unfinished one — so the comparison would mislead.
#
# Withholding is opt-IN: a dataset nobody has ruled on presents
# everything, so a new set is never quietly reported as vote-only.
VOTE_ONLY_DATASETS = frozenset({"volumes"})


def presents_tiebreak(name: str) -> bool:
    """Whether this dataset presents the LightOn tiebreak combinations."""
    return name not in VOTE_ONLY_DATASETS


@dataclass(frozen=True)
class Dataset:
    """One dataset under data/datasets/<name> (layout: docs/pipeline.md)."""

    name: str
    root: Path

    @property
    def redacted(self) -> Path:
        return self.root / "redacted"

    @property
    def source(self) -> Path:
        """Whole-volume PDFs behind a prerendered set — the only layout
        with no per-page PDF to point at (pipeline.core.pages)."""
        return self.root / "source"

    @property
    def page_png(self) -> Path:
        return self.root / "page_png"

    def engine_dir(self, engine: str) -> Path:
        return self.root / "engines" / engine


def dataset(data_root: Path, name: str) -> Dataset:
    return Dataset(name=name, root=data_root / "datasets" / name)
