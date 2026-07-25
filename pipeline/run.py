"""CLI: run a dataset through a route, writing per-page artifacts.

    uv run python -m pipeline.run --data-root data --dataset sample30 \
        --route mistral            # or --route all

Implemented stages: 1 render, 2 layout, 3 main OCR (dots), 4 supplemental
OCR, 5 reconstruct (reading order; raw text). Normalize (6) / compare (7) /
assemble (8) land in later milestones; the artifact's "stages" object grows
as they do (docs/pipeline.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.core import layout, reconstruct, render
from pipeline.core.artifacts import SCHEMA_VERSION, artifact_path
from pipeline.core.config import RENDER_H, RENDER_W, Dataset, dataset
from pipeline.core.pages import Page, discover_pages
from pipeline.core.readers import redaction_rects
from pipeline.engines import dots, gemini, mistral, surya
from pipeline.routes import ROUTES, Route


def _stage_render(ds: Dataset, page: Page) -> dict:
    png = render.page_png(ds, page)
    rel = png.relative_to(ds.root.parent.parent)  # relative to data/
    return {
        "png": str(rel),
        "size": [RENDER_W, RENDER_H],
        "n_redaction_rects": len(redaction_rects(ds, page)),
    }


def _stage_layout(containers: dict) -> dict:
    return {
        "engine": "container_yolo",
        "raw": containers["raw"],
        "post": containers["post"],
        "columns": containers["columns"],
        "stats": containers["stats"],
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


def _one_supplemental(
    ds: Dataset, page_id: str, engine: str, unit: str
) -> dict:
    if engine == "mistral":
        raw_m = mistral.load(ds, page_id)
        return {
            "engine": "mistral",
            "unit": unit,
            "missing": raw_m is None,
            "blocks": mistral.blocks(raw_m or []),
        }
    if engine == "gemini":
        raw_g = gemini.load(ds, page_id)
        decoded = (
            gemini.decode(raw_g)
            if raw_g
            else {
                "blocks": [],
                "parse_error": None,
            }
        )
        return {
            "engine": "gemini",
            "unit": unit,
            "refusal": gemini.is_refusal(raw_g),
            "raw_xml": raw_g or "",
            "blocks": decoded["blocks"],
            "parse_error": decoded["parse_error"],
        }
    if engine == "surya":
        raw_s = surya.load(ds, page_id, variant=unit)
        key = "lines" if unit == "line" else "blocks"
        return {
            "engine": "surya",
            "unit": unit,
            "missing": raw_s is None,
            key: surya.regions(raw_s or {}),
        }
    raise ValueError(f"unknown supplemental engine: {engine}")


def _reconstruct_one(containers: dict, main_ocr: dict, supp: dict) -> dict:
    if supp["engine"] == "mistral":
        s = reconstruct.reconstruct_blocks(containers, supp["blocks"])
    elif supp["engine"] == "gemini":
        s = reconstruct.reconstruct_gemini(supp["blocks"])
    elif supp["unit"] == "line":
        s = reconstruct.reconstruct_surya(
            containers, supp["lines"], main_ocr["blocks"]
        )
    else:
        s = reconstruct.reconstruct_surya_blocks(
            containers, supp["blocks"], main_ocr["blocks"]
        )
    return {"engine": supp["engine"], "unit": supp["unit"], **s}


def _stage_reconstruct(
    containers: dict, main_ocr: dict, supps: list[dict]
) -> dict:
    main = reconstruct.reconstruct_blocks(containers, main_ocr["blocks"])
    return {
        "main": {"engine": main_ocr["engine"], **main},
        "supplementals": [
            _reconstruct_one(containers, main_ocr, supp) for supp in supps
        ],
    }


def build_page_artifact(ds: Dataset, page: Page, route: Route) -> dict | None:
    main_ocr = _stage_main_ocr(ds, page.page_id)
    if main_ocr is None:
        return None  # no dots output -> page not runnable yet
    containers = layout.containers_for(ds, page.page_id)
    supps = [
        _one_supplemental(ds, page.page_id, engine, unit)
        for engine, unit in route.supplementals
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset": ds.name,
        "page": page.page_id,
        "route": route.name,
        "tiebreak": route.tiebreak,
        "stages": {
            "render": _stage_render(ds, page),
            "layout": _stage_layout(containers),
            "main_ocr": main_ocr,
            "supplementals": supps,
            "reconstruct": _stage_reconstruct(containers, main_ocr, supps),
        },
    }


def run_route(
    data_root: Path, dataset_name: str, route: Route
) -> tuple[int, int]:
    """Write artifacts for every runnable page; returns (written, skipped)."""
    ds = dataset(data_root, dataset_name)
    artifacts_root = data_root / "artifacts"
    written = skipped = 0
    for page in discover_pages(ds):
        artifact = build_page_artifact(ds, page, route)
        if artifact is None:
            skipped += 1
            continue
        out = artifact_path(artifacts_root, ds.name, route.name, page.page_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(artifact, ensure_ascii=False), encoding="utf-8"
        )
        written += 1
    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data", type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--route", required=True, choices=[*ROUTES, "all"])
    args = parser.parse_args()
    names = list(ROUTES) if args.route == "all" else [args.route]
    for name in names:
        written, skipped = run_route(
            args.data_root, args.dataset, ROUTES[name]
        )
        print(f"{args.dataset}/{name}: {written} written, {skipped} skipped")


if __name__ == "__main__":
    main()
