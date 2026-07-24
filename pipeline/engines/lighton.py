"""LightOnOCR — the tiebreak engine in every route. M1 exposes the crop-read
cache; live inference (GPU/CPU) arrives with the compare stage.

Cache layout: engines/lighton_crops/<page>_<x0>_<y0>_<x1>_<y1>.txt (the
read), with an optional .png (the crop) and .conf.json beside it.
"""

from __future__ import annotations

from pipeline.core.config import Dataset

ENGINE = "lighton"
CACHE_DIR = "lighton_crops"


def crop_reads(ds: Dataset, page_id: str) -> list[dict]:
    """Cached crop reads for a page: [{bbox, text, has_png}]."""
    out = []
    cache = ds.engine_dir(CACHE_DIR)
    for f in sorted(cache.glob(f"{page_id}_*.txt")):
        coords = f.stem[len(page_id) + 1 :].split("_")
        if len(coords) != 4:
            continue
        try:
            bbox = [int(c) for c in coords]
        except ValueError:
            continue
        out.append(
            {
                "bbox": bbox,
                "text": f.read_text(encoding="utf-8", errors="replace"),
                "has_png": f.with_suffix(".png").exists(),
            }
        )
    return out
