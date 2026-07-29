"""CLI: align a dataset's engine outputs page by page and write artifacts.

    uv run python -m pipeline.align_run --dataset newvols
    uv run python -m pipeline.align_run --dataset newvols --engines dots,surya

Each page becomes one document under
``align_data/artifacts/<dataset>/align/<page>.json``: the engines' regions
grouped into the same pieces of the page, put in reading order from
their coordinates alone, each resolved to the read the engines agree
on, plus the container-YOLO detections carried along purely as an
image overlay.

A page builds as long as ONE engine has output for it, so an engine
that dropped a page costs that engine's column, not the page.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.core import align, consensus, layout, order
from pipeline.core.artifacts import artifact_path
from pipeline.core.config import RENDER_H, RENDER_W, Dataset, dataset
from pipeline.core.pages import Page, discover_pages
from pipeline.engines import dots, mistral, surya

ALIGN_SCHEMA_VERSION = 2
# Artifacts live beside the route artifacts, under a reserved name.
ALIGN_DIR = "align"
ENGINES = ("dots", "mistral", "surya")


def _blocks(ds: Dataset, page_id: str, engine: str) -> list[dict] | None:
    """One engine's decoded blocks, or None when it has no output for
    this page."""
    if engine == "dots":
        raw = dots.load(ds, page_id)
        return None if raw is None else dots.blocks(raw)
    if engine == "mistral":
        raw_m = mistral.load(ds, page_id)
        return None if raw_m is None else mistral.blocks(raw_m)
    if engine == "surya":
        raw_s = surya.load(ds, page_id, variant="block")
        return None if raw_s is None else surya.regions(raw_s)
    raise ValueError(f"unknown engine: {engine}")


def _metrics(groups: list[dict], engines: list[str]) -> dict:
    by_pattern: dict[str, int] = {}
    boxes: dict[str, int] = {e: 0 for e in engines}
    by_agreement: dict[str, int] = {}
    low_conf = 0
    for g in groups:
        key = "+".join(g["present"])
        by_pattern[key] = by_pattern.get(key, 0) + 1
        for e, merged in g["engines"].items():
            boxes[e] += merged["n_boxes"]
        final = g["consensus"]
        by_agreement[final["agreement"]] = (
            by_agreement.get(final["agreement"], 0) + 1
        )
        low_conf += final["n_low_confidence"]
    scored = [g["alignment_iou"] for g in groups if len(g["present"]) > 1]
    return {
        "by_agreement": by_agreement,
        "n_low_confidence": low_conf,
        "n_groups": len(groups),
        "n_all_engines": sum(
            1 for g in groups if len(g["present"]) == len(engines)
        ),
        "n_page_scale": sum(1 for g in groups if g["page_scale"]),
        "by_pattern": dict(sorted(by_pattern.items(), key=lambda kv: -kv[1])),
        "boxes_per_engine": boxes,
        "n_weak_alignment": sum(1 for v in scored if v < 0.3),
        "median_alignment_iou": (
            sorted(scored)[len(scored) // 2] if scored else None
        ),
    }


def build_page_align(
    ds: Dataset, page: Page, engines: list[str] | None = None
) -> dict | None:
    """The align artifact for one page, or None when no engine read it."""
    names = list(engines or ENGINES)
    per_engine = {}
    missing = []
    for engine in names:
        blocks = _blocks(ds, page.page_id, engine)
        if blocks is None:
            missing.append(engine)
        else:
            per_engine[engine] = blocks
    if not per_engine:
        return None

    groups = align.align_page(per_engine)
    for g in groups:
        g["consensus"] = consensus.resolve(g["engines"])
    placed = order.place(groups)
    boundary = order.column_boundary(groups)
    containers = layout.containers_for(ds, page.page_id)
    png = ds.page_png / f"{page.page_id}.png"
    return {
        "schema_version": ALIGN_SCHEMA_VERSION,
        "kind": "align",
        "dataset": ds.name,
        "page": page.page_id,
        "engines": sorted(per_engine),
        "missing_engines": missing,
        "params": {"overlap": align.OVERLAP, "max_area": align.MAX_AREA},
        "render": {
            "png": str(png.relative_to(ds.root.parent.parent)),
            "size": [RENDER_W, RENDER_H],
        },
        # Informational only: the container model never places anything
        # here, it is drawn over the page image so its output can be
        # judged by eye.
        "containers": {
            "engine": "container_yolo",
            "raw": containers["raw"],
            "post": containers["post"],
            "columns": containers["columns"],
            "stats": containers["stats"],
        },
        "column_boundary": boundary,
        "groups": [{"id": i, **g} for i, g in enumerate(placed)],
        "metrics": _metrics(placed, sorted(per_engine)),
    }


def run_dataset(
    data_root: Path, dataset_name: str, engines: list[str] | None = None
) -> tuple[int, int]:
    """Write an align artifact for every page; returns (written, skipped)."""
    ds = dataset(data_root, dataset_name)
    artifacts_root = data_root / "artifacts"
    written = skipped = 0
    for page in discover_pages(ds):
        doc = build_page_align(ds, page, engines)
        if doc is None:
            skipped += 1
            continue
        out = artifact_path(artifacts_root, ds.name, ALIGN_DIR, page.page_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)
        written += 1
    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Align sets live under their own root, never under data/ — the
    # separation is what keeps them out of the route surface.
    parser.add_argument("--data-root", default="align_data", type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--engines",
        default=",".join(ENGINES),
        help=f"comma-separated subset of {', '.join(ENGINES)}",
    )
    args = parser.parse_args()
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in ENGINES]
    if unknown:
        parser.error(f"unknown engine(s): {', '.join(unknown)}")
    written, skipped = run_dataset(args.data_root, args.dataset, engines)
    print(f"{args.dataset}/{ALIGN_DIR}: {written} written, {skipped} skipped")


if __name__ == "__main__":
    main()
