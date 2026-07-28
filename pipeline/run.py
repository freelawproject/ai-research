"""CLI: run a dataset through a route, writing per-page artifacts.

    uv run python -m pipeline.run --dataset sample30 \
        --route dots+gemini+mistral       # any combination (order-free)
    uv run python -m pipeline.run --dataset sample30 --route all
        # all 7 unique combinations

Implemented stages: render, layout, main OCR (the route's bbox-capable
main engine), supplemental OCR, reconstruct (reading order; raw text),
normalize (canonical tokens; the rule registry grows rule by rule),
and compare + resolve (disputes on the unit-key streams; LightOn
cached-crop tiebreak or direct three-way vote). Assembly lands next;
the artifact's "stages" object grows as it does (docs/pipeline.md).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pipeline import routes
from pipeline.core import compare, layout, normalize, reconstruct, render
from pipeline.core.artifacts import SCHEMA_VERSION, artifact_path
from pipeline.core.config import RENDER_H, RENDER_W, Dataset, dataset
from pipeline.core.pages import Page, discover_pages
from pipeline.core.readers import redaction_rects
from pipeline.engines import dots, gemini, lighton, mistral, surya
from pipeline.routes import PRESETS, Route, resolve


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


def _load_blocks(
    ds: Dataset, page_id: str, engine: str, unit: str
) -> list[dict] | None:
    """The decoded block list of one bbox engine's cached output, or
    None when the page has no cached output for it."""
    if engine == "dots":
        raw = dots.load(ds, page_id)
        return None if raw is None else dots.blocks(raw)
    if engine == "mistral":
        raw_m = mistral.load(ds, page_id)
        return None if raw_m is None else mistral.blocks(raw_m)
    if engine == "surya":
        raw_s = surya.load(ds, page_id, variant=unit)
        return None if raw_s is None else surya.regions(raw_s)
    raise ValueError(f"not a block engine: {engine}")


def _stage_main_ocr(
    ds: Dataset, page_id: str, engine: str, unit: str
) -> dict | None:
    """The main engine's decoded regions. Any bbox-capable engine can be
    the main; a page with no main output is not runnable."""
    blocks = _load_blocks(ds, page_id, engine, unit)
    if blocks is None:
        return None
    return {"engine": engine, "unit": unit, "blocks": blocks}


def _one_supplemental(
    ds: Dataset, page_id: str, engine: str, unit: str
) -> dict:
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
    blocks = _load_blocks(ds, page_id, engine, unit)
    return {
        "engine": engine,
        "unit": unit,
        "missing": blocks is None,
        "blocks": blocks or [],
    }


def _reconstruct_one(containers: dict, main_ocr: dict, supp: dict) -> dict:
    """One supplemental against the MAIN engine's regions. Every block
    engine goes through the same path (in-image check vs the main
    engine's image blocks); gemini orders by its own coordinates."""
    if supp["engine"] == "gemini":
        s = reconstruct.reconstruct_gemini(supp["blocks"])
    else:
        s = reconstruct.reconstruct_blocks(
            containers, supp["blocks"], main_blocks=main_ocr["blocks"]
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


def _stamp_black_frac(ds: Dataset, page: Page, blocks: list[dict]) -> None:
    """Stamp the near-black fraction on every bbox-carrying block so
    reconstruction can drop REDACTION blocks — placeholder images AND
    text hallucinated over redacted regions (mistral invents whole
    passages there; reconstruct.REDACTION_BLACK_T)."""
    for b in blocks:
        if b.get("bbox"):
            b["black_frac"] = round(
                render.black_fraction(ds, page, b["bbox"]), 3
            )


def _stage_normalize(recon: dict) -> dict:
    return {
        "registry": {
            "hash": normalize.registry_hash(),
            "rules": normalize.registry_summary(),
        },
        "main": {
            "engine": recon["main"]["engine"],
            **normalize.normalize_items(
                recon["main"]["items"], recon["main"]["engine"]
            ),
        },
        "supplementals": [
            {
                "engine": s["engine"],
                "unit": s["unit"],
                **normalize.normalize_items(s["items"], s["engine"]),
            }
            for s in recon["supplementals"]
        ],
    }


def _stage_compare(
    ds: Dataset, page_id: str, route: Route, stages: dict
) -> dict:
    """Compare + resolve on the normalized streams. On tiebreak
    routes the crop
    reader is the LightOn cache lookup for this page."""
    read_crop: compare.CropReader | None = None
    if route.tiebreak is not None:

        def _read(bbox: Sequence[int]) -> str | None:
            return lighton.read(ds, page_id, bbox)

        read_crop = _read
    return compare.compare_streams(
        stages["normalize"],
        stages["reconstruct"],
        stages["main_ocr"]["blocks"],
        route.tiebreak,
        read_crop,
    )


def build_page_artifact(ds: Dataset, page: Page, route: Route) -> dict | None:
    main_ocr = _stage_main_ocr(ds, page.page_id, *route.main)
    if main_ocr is None:
        return None  # no main-engine output -> page not runnable yet
    containers = layout.containers_for(ds, page.page_id)
    supps = [
        _one_supplemental(ds, page.page_id, engine, unit)
        for engine, unit in route.supplementals
    ]
    # redaction test applies to EVERY bbox-carrying block (the
    # registry's redaction-blocks rule) — placeholder images and text
    # hallucinated over redacted regions alike (gemini has no bboxes,
    # so its blocks are a no-op)
    _stamp_black_frac(ds, page, main_ocr["blocks"])
    for supp in supps:
        _stamp_black_frac(ds, page, supp["blocks"])
    recon = _stage_reconstruct(containers, main_ocr, supps)
    stages = {
        "render": _stage_render(ds, page),
        "layout": _stage_layout(containers),
        "main_ocr": main_ocr,
        "supplementals": supps,
        "reconstruct": recon,
        "normalize": _stage_normalize(recon),
    }
    stages["compare"] = _stage_compare(ds, page.page_id, route, stages)
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset": ds.name,
        "page": page.page_id,
        "route": route.name,
        "tiebreak": route.tiebreak,
        "stages": stages,
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
    parser.add_argument(
        "--route",
        required=True,
        help=(
            "a combination like dots+gemini+mistral (order doesn't "
            "matter), 'all' (= all 7 unique combinations — feeds the "
            f"home-page stats table), or a legacy alias "
            f"({', '.join(PRESETS)})"
        ),
    )
    args = parser.parse_args()
    if args.route == "all":
        route_list = routes.all_combos()
    else:
        route_list = [resolve(args.route)]
    for route in route_list:
        written, skipped = run_route(args.data_root, args.dataset, route)
        print(
            f"{args.dataset}/{route.name}: {written} written, "
            f"{skipped} skipped"
        )


if __name__ == "__main__":
    main()
