"""CLI: run a dataset through a route, writing per-page artifacts.

    uv run python -m pipeline.run --data-root data --dataset sample30 \
        --route mistral                    # preset
    uv run python -m pipeline.run --dataset sample30 \
        --route mistral+gemini+dots       # any composed combination
    uv run python -m pipeline.run --dataset sample30 --route all

Implemented stages: 1 render, 2 layout, 3 main OCR (the route's
bbox-capable main engine), 4 supplemental OCR, 5 reconstruct (reading
order; raw text), 6 normalize (canonical tokens; the rule registry grows
rule by rule). Compare (7) / assemble (8) land in later milestones; the
artifact's "stages" object grows as they do (docs/pipeline.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.core import layout, normalize, reconstruct, render
from pipeline.core.artifacts import SCHEMA_VERSION, artifact_path
from pipeline.core.config import RENDER_H, RENDER_W, Dataset, dataset
from pipeline.core.pages import Page, discover_pages
from pipeline.core.readers import redaction_rects
from pipeline.engines import dots, gemini, mistral, surya
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


def _stage_main_ocr(
    ds: Dataset, page_id: str, engine: str, unit: str
) -> dict | None:
    """The main engine's decoded regions. Any bbox-capable engine can be
    the main; a page with no main output is not runnable."""
    if engine == "dots":
        raw = dots.load(ds, page_id)
        if raw is None:
            return None
        return {
            "engine": "dots",
            "unit": unit,
            "blocks": dots.blocks(raw),
            "page_text": raw.get("text", ""),
        }
    if engine == "mistral":
        raw_m = mistral.load(ds, page_id)
        if raw_m is None:
            return None
        return {
            "engine": "mistral",
            "unit": unit,
            "blocks": mistral.blocks(raw_m),
        }
    if engine == "surya":
        raw_s = surya.load(ds, page_id, variant=unit)
        if raw_s is None:
            return None
        return {
            "engine": "surya",
            "unit": unit,
            "blocks": surya.regions(raw_s),
        }
    raise ValueError(f"engine cannot be main: {engine}")


def _one_supplemental(
    ds: Dataset, page_id: str, engine: str, unit: str
) -> dict:
    if engine == "dots":
        raw_d = dots.load(ds, page_id)
        return {
            "engine": "dots",
            "unit": unit,
            "missing": raw_d is None,
            "blocks": dots.blocks(raw_d or {}),
        }
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
    """One supplemental against the MAIN engine's regions. Every block
    engine goes through the same path (in-image check vs the main
    engine's image blocks); gemini orders by its own coordinates; surya
    lines are filtered/grouped against the main blocks."""
    if supp["engine"] == "gemini":
        s = reconstruct.reconstruct_gemini(supp["blocks"])
    elif supp["engine"] == "surya" and supp["unit"] == "line":
        s = reconstruct.reconstruct_surya(
            containers, supp["lines"], main_ocr["blocks"]
        )
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
    """Stamp the near-black fraction on image blocks (dots Picture /
    mistral image) so reconstruction can drop redaction placeholders
    (reconstruct.REDACTION_BLACK_T)."""
    for b in blocks:
        label = b.get("label") or b.get("type")
        if label in reconstruct.IMAGE_LABELS and b.get("bbox"):
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


def build_page_artifact(ds: Dataset, page: Page, route: Route) -> dict | None:
    main_ocr = _stage_main_ocr(ds, page.page_id, *route.main)
    if main_ocr is None:
        return None  # no main-engine output -> page not runnable yet
    containers = layout.containers_for(ds, page.page_id)
    supps = [
        _one_supplemental(ds, page.page_id, engine, unit)
        for engine, unit in route.supplementals
    ]
    # redaction test applies to the dots/mistral image blocks (the
    # registry's redaction-images rule), wherever those engines sit
    if main_ocr["engine"] in ("dots", "mistral"):
        _stamp_black_frac(ds, page, main_ocr["blocks"])
    for supp in supps:
        if supp["engine"] in ("dots", "mistral"):
            _stamp_black_frac(ds, page, supp["blocks"])
    recon = _stage_reconstruct(containers, main_ocr, supps)
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
            "reconstruct": recon,
            "normalize": _stage_normalize(recon),
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
    parser.add_argument(
        "--route",
        required=True,
        help=(
            f"a preset ({', '.join(PRESETS)}), a composed combination "
            "like mistral+gemini+dots, or 'all' (= every preset)"
        ),
    )
    args = parser.parse_args()
    names = list(PRESETS) if args.route == "all" else [args.route]
    for name in names:
        route = resolve(name)
        written, skipped = run_route(args.data_root, args.dataset, route)
        print(
            f"{args.dataset}/{route.name}: {written} written, "
            f"{skipped} skipped"
        )


if __name__ == "__main__":
    main()
