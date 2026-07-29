"""LightOnOCR — the tiebreak engine on lighton routes. The compare
stage resolves disputes through the crop-read CACHE: a read exists for
an exact main-block bbox or it doesn't (a miss is an honest
no-LightOn-vote — live inference is the RunPod kit's job, not the
pipeline's).

Each crop gets at most TWO reads. The first is the plain read; when it
fails the compare stage's degeneration guards, that stage asks for one
RETRY read of the same crop, decoded under the settings retry_decode()
describes. A crop is never read a third time.

Cache layout: engines/lighton_crops/<page>_<x0>_<y0>_<x1>_<y1>.txt (the
read), with an optional .png (the crop) and .conf.json beside it. The
retry read is the same stem plus RETRY_SUFFIX.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pipeline.core.config import Dataset

ENGINE = "lighton"
CACHE_DIR = "lighton_crops"
# The retry read sits beside the first one under this suffix.
RETRY_SUFFIX = "__retry"


def _crop_stem(page_id: str, bbox: Sequence[int]) -> str:
    return f"{page_id}_" + "_".join(str(int(c)) for c in bbox)


def retry_decode(expect: str) -> dict:
    """Decode settings for the one retry of a crop whose first read was
    discarded. The first read failed on length or on its ending, so the
    retry gets LESS room to ramble, not more: a ceiling near the length
    of the text the main engine found in this crop, and a stronger
    repetition brake."""
    return {
        "max_new_tokens": len(expect) // 3 + 32,
        "repetition_penalty": 1.25,
        "no_repeat_ngram_size": 8,
    }


def read(
    ds: Dataset,
    page_id: str,
    bbox: Sequence[int],
    retry: bool = False,
) -> str | None:
    """The cached read for an exact crop bbox — the tiebreak lookup.
    `retry` asks for the second, re-decoded read of the same crop."""
    stem = _crop_stem(page_id, bbox) + (RETRY_SUFFIX if retry else "")
    f = ds.engine_dir(CACHE_DIR) / f"{stem}.txt"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8", errors="replace")


def crop_png(ds: Dataset, page_id: str, bbox: Sequence[int]) -> Path | None:
    """The crop image beside a cached read, when one was saved."""
    f = ds.engine_dir(CACHE_DIR) / f"{_crop_stem(page_id, bbox)}.png"
    return f if f.exists() else None
