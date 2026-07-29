"""Align-artifact index — the data layer for the alignment viewer.

Same contract as the route artifacts: the pipeline writes JSON, the
viewer only renders it. Documents are materialized lazily, so a set
whose engine outputs have just been staged is viewable without a batch
run (a whole set is ~13s, but the first visit should not have to wait
for it).

Everything here reads the ALIGN_* roots (settings), never DATA_ROOT:
align sets live under their own tree, which is what keeps them out of
the route surface — and route sets out of this one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.conf import settings
from django.http import Http404

from pipeline.align_run import (
    ALIGN_DIR,
    ALIGN_SCHEMA_VERSION,
    build_page_align,
)
from pipeline.core.artifacts import artifact_path
from pipeline.core.config import dataset as load_dataset
from pipeline.core.pages import discover_pages

_NAME_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")
_STATS_CACHE = ".align_stats_cache.json"
# "<volume stem>__page_<n>" — pages group by volume in the nav.
_PAGE_RE = re.compile(r"^(?P<volume>.+)__page_(?P<number>\d+)$")


def _safe(name: str) -> str:
    if not _NAME_RE.match(name) or ".." in name:
        raise Http404(name)
    return name


def page_sort_key(page_id: str) -> tuple[str, int, str]:
    """Volume, then page NUMBER — lexicographic order alone puts page
    10 before page 2 whenever the numbering is not zero-padded."""
    m = _PAGE_RE.match(page_id)
    if m is None:
        return (page_id, -1, page_id)
    return (m.group("volume"), int(m.group("number")), page_id)


def volume_of(page_id: str) -> str:
    m = _PAGE_RE.match(page_id)
    return m.group("volume") if m else page_id


def dataset_names() -> list[str]:
    """The align sets on disk, by name."""
    root: Path = settings.ALIGN_DATASETS_ROOT
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def dataset_pages(dataset: str) -> list[str]:
    """Every page of a dataset, in reading order across volumes."""
    root = settings.ALIGN_DATASETS_ROOT / _safe(dataset) / "page_png"
    return sorted((f.stem for f in root.glob("*.png")), key=page_sort_key)


def page_png_path(dataset: str, page: str) -> Path:
    """The canonical render behind an align page — from the align root,
    never the route surface's (a different tree)."""
    f = (
        settings.ALIGN_DATASETS_ROOT
        / _safe(dataset)
        / "page_png"
        / f"{_safe(page)}.png"
    )
    if not f.exists():
        raise Http404(f"no render for {dataset}/{page}")
    return f


def volumes(pages: list[str]) -> list[dict]:
    """The volumes a page list spans, with each one's first page."""
    seen: dict[str, dict] = {}
    for p in pages:
        vol = volume_of(p)
        entry = seen.setdefault(vol, {"volume": vol, "first": p, "n": 0})
        entry["n"] += 1
    return list(seen.values())


def ensure_align(dataset: str, page: str) -> dict:
    """The align document for one page, rebuilt when missing or written
    by an older schema."""
    f = artifact_path(
        settings.ALIGN_ARTIFACTS_ROOT, _safe(dataset), ALIGN_DIR, _safe(page)
    )
    if f.exists():
        try:
            doc: dict = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            doc = {}  # torn or truncated: rebuild
        if doc.get("schema_version") == ALIGN_SCHEMA_VERSION:
            return doc
    return _materialize(dataset, page)


def _materialize(dataset: str, page: str) -> dict:
    try:
        ds = load_dataset(settings.ALIGN_DATA_ROOT, _safe(dataset))
        pg = next((p for p in discover_pages(ds) if p.page_id == page), None)
    except Exception as exc:  # tree incomplete or unreadable
        raise Http404(f"cannot build {dataset}/{page}: {exc}") from exc
    if pg is None:
        raise Http404(f"no page {page} in {dataset}")
    doc = build_page_align(ds, pg)
    if doc is None:
        raise Http404(
            f"{page} has no cached output from any engine — nothing to align"
        )
    out = artifact_path(
        settings.ALIGN_ARTIFACTS_ROOT, ds.name, ALIGN_DIR, page
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")  # atomic: no half-written reads
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    return doc


def corpus_stats(dataset: str) -> dict | None:
    """Set-wide totals over the built align artifacts, or None when the
    set has not been built yet. Reads only what is on disk — it never
    materializes 3,500 pages behind a page load.

    Aggregating re-reads every artifact, so the result is cached beside
    them and keyed on (file count, newest mtime): a set that has not
    been rebuilt is never re-parsed.
    """
    root = settings.ALIGN_ARTIFACTS_ROOT / _safe(dataset) / ALIGN_DIR
    if not root.is_dir():
        return None
    files = sorted(root.glob("*.json"))
    if not files:
        return None
    cache_f = root.parent / _STATS_CACHE
    sig = [len(files), max(f.stat().st_mtime for f in files)]
    try:
        cached = json.loads(cache_f.read_text(encoding="utf-8"))
        if cached.get("sig") == sig:
            value: dict = cached["value"]
            return value
    except (OSError, ValueError, KeyError):
        pass
    computed = _compute_stats(files)
    try:
        tmp = cache_f.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"sig": sig, "value": computed}), encoding="utf-8"
        )
        tmp.replace(cache_f)
    except OSError:
        pass  # a read-only data tree still renders, just slower
    return computed


def _compute_stats(files: list[Path]) -> dict:
    pattern: dict[str, int] = {}
    boxes: dict[str, int] = {}
    agreement: dict[str, int] = {}
    totals = {
        "pages": 0,
        "groups": 0,
        "all_engines": 0,
        "page_scale": 0,
        "weak": 0,
        "two_column": 0,
        "low_confidence": 0,
    }
    medians = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        m = doc.get("metrics", {})
        totals["pages"] += 1
        totals["groups"] += m.get("n_groups", 0)
        totals["all_engines"] += m.get("n_all_engines", 0)
        totals["page_scale"] += m.get("n_page_scale", 0)
        totals["weak"] += m.get("n_weak_alignment", 0)
        totals["two_column"] += 1 if doc.get("column_boundary") else 0
        for k, v in m.get("by_pattern", {}).items():
            pattern[k] = pattern.get(k, 0) + v
        for k, v in m.get("boxes_per_engine", {}).items():
            boxes[k] = boxes.get(k, 0) + v
        for k, v in m.get("by_agreement", {}).items():
            agreement[k] = agreement.get(k, 0) + v
        totals["low_confidence"] += m.get("n_low_confidence", 0)
        if m.get("median_alignment_iou") is not None:
            medians.append(m["median_alignment_iou"])
    groups = max(totals["groups"], 1)
    return {
        **totals,
        "groups_per_page": totals["groups"] / max(totals["pages"], 1),
        "pct_all_engines": 100 * totals["all_engines"] / groups,
        "pct_weak": 100 * totals["weak"] / groups,
        "pct_two_column": 100 * totals["two_column"] / max(totals["pages"], 1),
        "median_alignment_iou": (
            sorted(medians)[len(medians) // 2] if medians else None
        ),
        "by_pattern": sorted(
            (
                {"key": k, "n": v, "pct": 100 * v / groups}
                for k, v in pattern.items()
            ),
            key=lambda r: -r["n"],
        ),
        "boxes_per_engine": dict(sorted(boxes.items())),
        "by_agreement": sorted(
            (
                {"key": k, "n": v, "pct": 100 * v / groups}
                for k, v in agreement.items()
            ),
            key=lambda r: -r["n"],
        ),
    }
