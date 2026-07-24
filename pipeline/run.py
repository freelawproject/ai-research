"""CLI: run a dataset through a route, writing per-page artifacts.

    uv run python -m pipeline.run --data-root data --dataset sample30 \
        --route mistral            # or --route all

Implemented stages: 1 render, 2 layout, 3 main OCR (dots), 4 supplemental
OCR. Normalize / reconstruct / compare / assemble land in later milestones;
the artifact's "stages" object grows as they do (docs/pipeline.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.core import layout, render
from pipeline.core.config import RENDER_H, RENDER_W, Dataset, dataset
from pipeline.core.pages import Page, discover_pages
from pipeline.core.readers import redaction_rects
from pipeline.engines import dots, gemini, mistral, surya
from pipeline.routes import ROUTES, Route

SCHEMA_VERSION = 1


def _stage_render(ds: Dataset, page: Page) -> dict:
    png = render.page_png(ds, page)
    rel = png.relative_to(ds.root.parent.parent)  # relative to data/
    return {
        "png": str(rel),
        "size": [RENDER_W, RENDER_H],
        "n_redaction_rects": len(redaction_rects(ds, page)),
    }


def _stage_layout(ds: Dataset, page_id: str) -> dict:
    c = layout.containers_for(ds, page_id)
    return {
        "engine": "container_yolo",
        "raw": c["raw"],
        "post": c["post"],
        "columns": c["columns"],
        "stats": c["stats"],
    }


def _stage_main_ocr(ds: Dataset, page_id: str) -> dict | None:
    raw = dots.load(ds, page_id)
    if raw is None:
        return None
    return {
        "engine": "dots",
        "blocks": dots.blocks(raw),
        "page_text": raw.get("text", ""),
    }


def _stage_supplemental(ds: Dataset, page_id: str, route: Route) -> dict:
    if route.supplemental == "mistral":
        raw_m = mistral.load(ds, page_id)
        return {
            "engine": "mistral",
            "unit": route.supplemental_unit,
            "missing": raw_m is None,
            "blocks": mistral.blocks(raw_m or []),
        }
    if route.supplemental == "gemini":
        raw_g = gemini.load(ds, page_id)
        decoded = gemini.decode(raw_g) if raw_g else {
            "blocks": [],
            "parse_error": None,
        }
        return {
            "engine": "gemini",
            "unit": route.supplemental_unit,
            "refusal": gemini.is_refusal(raw_g),
            "raw_xml": raw_g or "",
            "blocks": decoded["blocks"],
            "parse_error": decoded["parse_error"],
        }
    if route.supplemental == "surya":
        raw_s = surya.load(ds, page_id, variant="line")
        return {
            "engine": "surya",
            "unit": route.supplemental_unit,
            "missing": raw_s is None,
            "lines": surya.lines(raw_s or {}),
        }
    raise ValueError(f"unknown supplemental engine: {route.supplemental}")


def build_page_artifact(ds: Dataset, page: Page, route: Route) -> dict | None:
    main_ocr = _stage_main_ocr(ds, page.page_id)
    if main_ocr is None:
        return None  # no dots output -> page not runnable yet
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset": ds.name,
        "page": page.page_id,
        "route": route.name,
        "stages": {
            "render": _stage_render(ds, page),
            "layout": _stage_layout(ds, page.page_id),
            "main_ocr": main_ocr,
            "supplemental": _stage_supplemental(ds, page.page_id, route),
        },
    }


def run_route(
    data_root: Path, dataset_name: str, route: Route
) -> tuple[int, int]:
    """Write artifacts for every runnable page; returns (written, skipped)."""
    ds = dataset(data_root, dataset_name)
    out_dir = data_root / "artifacts" / ds.name / route.name
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for page in discover_pages(ds):
        artifact = build_page_artifact(ds, page, route)
        if artifact is None:
            skipped += 1
            continue
        out = out_dir / f"{page.page_id}.json"
        out.write_text(
            json.dumps(artifact, ensure_ascii=False), encoding="utf-8"
        )
        written += 1
    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data", type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--route", required=True, choices=[*sorted(ROUTES), "all"]
    )
    args = parser.parse_args()
    names = sorted(ROUTES) if args.route == "all" else [args.route]
    for name in names:
        written, skipped = run_route(
            args.data_root, args.dataset, ROUTES[name]
        )
        print(f"{args.dataset}/{name}: {written} written, {skipped} skipped")


if __name__ == "__main__":
    main()
