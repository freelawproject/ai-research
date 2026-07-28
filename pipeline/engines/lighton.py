"""LightOnOCR — the tiebreak engine on lighton routes. The compare
stage resolves disputes through the crop-read CACHE: a read exists for
an exact main-block bbox or it doesn't (a miss is an honest
no-LightOn-vote — live inference is the RunPod kit's job, not the
pipeline's).

Cache layout: engines/lighton_crops/<page>_<x0>_<y0>_<x1>_<y1>.txt (the
read), with an optional .png (the crop) and .conf.json beside it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pipeline.core.config import Dataset

ENGINE = "lighton"
CACHE_DIR = "lighton_crops"


def _crop_stem(page_id: str, bbox: Sequence[int]) -> str:
    return f"{page_id}_" + "_".join(str(int(c)) for c in bbox)


def read(ds: Dataset, page_id: str, bbox: Sequence[int]) -> str | None:
    """The cached read for an exact crop bbox — the tiebreak lookup."""
    f = ds.engine_dir(CACHE_DIR) / f"{_crop_stem(page_id, bbox)}.txt"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8", errors="replace")


def crop_png(ds: Dataset, page_id: str, bbox: Sequence[int]) -> Path | None:
    """The crop image beside a cached read, when one was saved."""
    f = ds.engine_dir(CACHE_DIR) / f"{_crop_stem(page_id, bbox)}.png"
    return f if f.exists() else None
