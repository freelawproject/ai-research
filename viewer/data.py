"""Artifact index — the viewer's only data layer.

There is no database: this module scans the data folder and reads the
pipeline's JSON artifacts (docs/pipeline.md). Routes are composed by the
user (three models), so artifacts are materialized LAZILY: the first
visit to a page under a combination builds its artifact from the cached
engine outputs and writes it to disk (same contract, same files the CLI
writes); later visits just read it.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from pathlib import Path

from django.conf import settings
from django.http import Http404
from PIL import Image

from pipeline import routes
from pipeline.core.artifacts import SCHEMA_VERSION, artifact_path
from pipeline.core.compare import HIGH_RISK_UNITS
from pipeline.core.config import dataset as load_dataset
from pipeline.core.normalize import registry_hash
from pipeline.core.pages import discover_pages
from pipeline.engines import lighton
from pipeline.routes import Route
from pipeline.run import build_page_artifact

_NAME_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")


def _safe(name: str) -> str:
    if not _NAME_RE.match(name) or ".." in name:
        raise Http404(name)
    return name


def _page_key(stem: str) -> tuple[str, int, str]:
    """Natural page order: volumes sort by name, pages by their NUMERIC
    index (p2 before p10 — plain lexicographic order gets this wrong)."""
    base, _, num = stem.rpartition("__p")
    if base and num.isdigit():
        return (base, int(num), stem)
    return (stem, -1, stem)


def dataset_names() -> list[str]:
    """The datasets on disk, by name."""
    root: Path = settings.DATASETS_ROOT
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def dataset_pages(dataset: str) -> list[str]:
    """Every renderable page of a dataset (route-independent)."""
    d = settings.DATASETS_ROOT / _safe(dataset) / "page_png"
    return sorted((f.stem for f in d.glob("*.png")), key=_page_key)


def _materialize(dataset: str, route: Route, page: str) -> dict:
    try:
        ds = load_dataset(settings.DATA_ROOT, _safe(dataset))
        pg = next((p for p in discover_pages(ds) if p.page_id == page), None)
    except Exception as exc:  # dataset tree incomplete or unreadable
        raise Http404(f"cannot rebuild {dataset}/{page}: {exc}") from exc
    if pg is None:
        raise Http404(f"no page {page} in {dataset}")
    artifact = build_page_artifact(ds, pg, route)
    if artifact is None:
        raise Http404(
            f"{page} has no cached {route.main[0]} output — the main "
            "engine's read is required to reconstruct this page"
        )
    out = artifact_path(settings.ARTIFACTS_ROOT, ds.name, route.name, page)
    out.parent.mkdir(parents=True, exist_ok=True)
    # atomic: a reader never sees a half-written artifact
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    return artifact


def _current(doc: dict) -> bool:
    """An artifact is current when both its schema version and its
    normalization-registry hash match the running pipeline — a registry
    change (new/edited rule) rebuilds on the next visit instead of
    rendering stale normalization."""
    if doc.get("schema_version") != SCHEMA_VERSION:
        return False
    stamped = (
        doc.get("stages", {})
        .get("normalize", {})
        .get("registry", {})
        .get("hash")
    )
    return bool(stamped == registry_hash())


def ensure_artifact(dataset: str, route: Route, page: str) -> dict:
    """The artifact for dataset x route x page, materializing it from
    the cached engine outputs when missing, unreadable, or written by an
    older pipeline/registry (routes are user-composed, so combinations
    are built on first visit — the artifact contract on disk stays the
    source of truth)."""
    f = (
        settings.ARTIFACTS_ROOT
        / _safe(dataset)
        / _safe(route.name)
        / f"{_safe(page)}.json"
    )
    if f.exists():
        try:
            doc: dict = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            doc = {}  # torn/corrupt file: rebuild it
        if _current(doc):
            return doc
    return _materialize(dataset, route, page)


def engine_raw(
    dataset: str, page: str, engine: str, variant: str | None = None
) -> str | None:
    """The engine's raw output file for a page. JSON is pretty-printed for
    reading (keys/values byte-identical); everything else is verbatim."""
    base = settings.DATASETS_ROOT / _safe(dataset) / "engines" / _safe(engine)
    if variant:
        base = base / _safe(variant)
    for ext in ("json", "xml"):
        f = base / f"{_safe(page)}.{ext}"
        if not f.exists():
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if ext == "json":
            try:
                return json.dumps(
                    json.loads(content), indent=2, ensure_ascii=False
                )
            except ValueError:
                pass
        return content
    return None


def page_png_path(dataset: str, page: str) -> Path:
    f = (
        settings.DATASETS_ROOT
        / _safe(dataset)
        / "page_png"
        / f"{_safe(page)}.png"
    )
    if not f.exists():
        raise Http404(f"no render for {dataset}/{page}")
    return f


# disputes where the resolver could not vote (the "rejection/refusal"
# bucket of the home-page stats): no cached crop read, read discarded by
# a degeneration guard (implausible length or a disagreeing ending),
# dispute not located in the read, or no third voter (a degraded stream
# — e.g. a gemini refusal).
_REJECT_REASONS = (
    "no-cached-read",
    "retry-pending",
    "read-implausible",
    "read-rejected",
    "not-located",
    "no-vote",
)


def _stats_order(route: Route) -> tuple[int, int, str]:
    """Display order of the stats table: lighton
    tiebreak routes before 3-way votes; within each group, the
    combinations WITH gemini before those without."""
    has_gemini = any(e == "gemini" for e, _ in route.supplementals)
    return (
        0 if route.tiebreak else 1,
        0 if has_gemini else 1,
        route.name,
    )


def stats_ordered_combos(dataset: str) -> list[Route]:
    """The combinations this dataset presents, in the stats-table
    display order (the order every combination list in the viewer
    uses)."""
    return sorted(routes.combos_for(_safe(dataset)), key=_stats_order)


def built_routes(dataset: str, page: str) -> list[str]:
    """Names of the combinations whose artifact for a page exists on
    disk, in display order (existence only — a stale artifact heals
    when it is loaded)."""
    root = settings.ARTIFACTS_ROOT / _safe(dataset)
    return [
        r.name
        for r in stats_ordered_combos(dataset)
        if (root / r.name / f"{_safe(page)}.json").exists()
    ]


def _scan_route(route: Route, files: list[Path]) -> dict:
    """One pass over a combination's artifacts (current schema +
    registry only, so numbers never mix rule versions): the summed
    compare metrics AND every flagged dispute (anything short of a
    clean majority — the review categories select from these)."""
    pages = disputes = majority = low_conf = high_risk = rejected = 0
    degraded = main_blocks = reorders = 0
    flagged: list[dict] = []
    for f in sorted(files, key=lambda p: _page_key(p.stem)):
        try:
            doc: dict = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        cmp = doc.get("stages", {}).get("compare")
        if not _current(doc) or not cmp:
            continue
        pages += 1
        main_blocks += len(doc["stages"]["main_ocr"]["blocks"])
        m = cmp["metrics"]
        disputes += m["n_disputes"]
        majority += m["by_resolution"].get("majority", 0)
        reorders += m["by_resolution"].get("reorder", 0)
        low_conf += m["n_low_confidence"]
        high_risk += m["n_high_risk"]
        rejected += sum(m["by_reason"].get(r, 0) for r in _REJECT_REASONS)
        if cmp["degraded"]:
            degraded += 1
        for disp in cmp["disputes"]:
            if disp["resolution"] == "majority" and not disp["low_confidence"]:
                continue
            flagged.append(
                {
                    "route": route.name,
                    "engines": list(routes.slugs(route)),
                    "tiebreak": route.tiebreak is not None,
                    "page": doc["page"],
                    "streams": cmp["streams"],
                    "dispute": disp,
                }
            )
    return {
        "stats": {
            "pages": pages,
            "main_blocks": main_blocks,
            "disputes": disputes,
            "majority": majority,
            "reorders": reorders,
            "low_conf": low_conf,
            "high_risk": high_risk,
            "rejected": rejected,
            "degraded": degraded,
        },
        "flagged": flagged,
    }


# Aggregating a large set re-reads every artifact, so per-route values
# are cached next to the artifacts, keyed on (file count, newest
# mtime) — a fresh process never re-parses an unchanged route dir.


def _per_route_cached(
    dataset: str,
    cache_name: str,
    compute: Callable[[Route, list[Path]], object],
) -> dict[str, object]:
    """One computed value per route dir, from the named disk cache;
    recomputed only for dirs whose signature changed."""
    ds_root = settings.ARTIFACTS_ROOT / _safe(dataset)
    cache_f = ds_root / cache_name
    try:
        cache: dict = json.loads(cache_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    changed = False
    out: dict[str, object] = {}
    for route in stats_ordered_combos(dataset):
        d = ds_root / route.name
        files = sorted(d.glob("*.json")) if d.is_dir() else []
        sig = [
            len(files),
            max((f.stat().st_mtime for f in files), default=0.0),
        ]
        entry = cache.get(route.name)
        if entry and entry.get("sig") == sig and "value" in entry:
            out[route.name] = entry["value"]
        else:
            value = compute(route, files)
            cache[route.name] = {"sig": sig, "value": value}
            out[route.name] = value
            changed = True
    if changed and ds_root.is_dir():
        tmp = cache_f.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(cache_f)
    return out


_ROUTE_CACHE = ".route_cache.json"


def route_stats(dataset: str) -> list[dict]:
    """Compare + resolve aggregates for every unique combination over the
    pages built on disk. Counts are per dispute (the
    table shows them as numerator/denominator): majority-resolved,
    low-confidence, high-risk (low-confidence with disputed text over
    the char threshold), and rejection/refusal (the resolver could not
    vote). A combination nobody has built yet reports pages=0."""
    scans = _per_route_cached(dataset, _ROUTE_CACHE, _scan_route)
    rows = []
    for route in stats_ordered_combos(dataset):
        m1, m2, m3 = routes.slugs(route)
        scan: dict = scans[route.name]  # type: ignore[assignment]
        rows.append(
            {
                "route": route.name,
                "engines": [m1, m2, m3],
                "tiebreak": route.tiebreak is not None,
                **scan["stats"],
            }
        )
    return rows


# The review pages behind the stats table's column headers: URL slug ->
# page title, description, and the predicate selecting its disputes.
REVIEW_CATEGORIES: dict[str, dict] = {
    "reorders": {
        "title": "Reorders",
        "description": (
            "Paired deletion + insertion of near-identical text: the "
            "engines agree on the content but not on where it belongs "
            "(e.g. physical vs semantic footnote order). The main's "
            "placement was kept — listed for review, not flagged."
        ),
        "predicate": lambda d: d.get("resolution") == "reorder",
    },
    "rejected": {
        "title": "Rejected or refused disputes",
        "description": (
            "The resolver could not vote: no cached crop read, read "
            "rejected by the ending guard, dispute not located in the "
            "read, or a refused/empty supplemental stream. The main "
            "reading was kept."
        ),
        "predicate": lambda d: d.get("reason") in _REJECT_REASONS,
    },
    "low-conf": {
        "title": "Low-confidence disputes",
        "description": (
            "No majority resolved these: the main reading was kept "
            "and flagged."
        ),
        "predicate": lambda d: d.get("low_confidence"),
    },
    "high-risk": {
        "title": "High-risk disputes",
        "description": (
            "Low-confidence disputes whose disputed span is longer "
            f"than {HIGH_RISK_UNITS} units on some side — the spans "
            "most worth a human look."
        ),
        "predicate": lambda d: d.get("high_risk"),
    },
}


def flagged_disputes(dataset: str) -> list[dict]:
    """Every flagged dispute of a dataset (reorders, fallbacks,
    authoritative keeps, low-confidence — the review categories filter
    these), in stats-table order. Served from the same one-pass route
    cache as the stats."""
    scans = _per_route_cached(dataset, _ROUTE_CACHE, _scan_route)
    out: list[dict] = []
    for route in stats_ordered_combos(dataset):
        scan: dict = scans[route.name]  # type: ignore[assignment]
        out += scan["flagged"]
    return out


def review_disputes(
    dataset: str, category: str, route: str | None = None
) -> list[dict]:
    """Every dispute of a review category (optionally one combination
    only), in the stats-table order — feeds the review pages behind
    the stats table's column headers."""
    meta = REVIEW_CATEGORIES.get(category)
    if meta is None:
        raise Http404(f"no review category {category}")
    predicate = meta["predicate"]
    return [
        e
        for e in flagged_disputes(dataset)
        if predicate(e["dispute"]) and (route is None or e["route"] == route)
    ]


_BBOX_RE = re.compile(r"^\d{1,5}_\d{1,5}_\d{1,5}_\d{1,5}$")
MIN_CROP_SIDE = 64  # px; smaller crops are scaled up, see page_crop_png


def page_crop_png(dataset: str, page: str, bbox: str) -> bytes:
    """A crop of the canonical page render, by bbox key ("x0_y0_x1_y1")
    — how the viewer fulfills the final HTML's <figure data-bbox>
    image placeholders and how the LightOn crop bundle is rendered. The
    bbox is clamped to the image bounds, and a crop smaller than
    MIN_CROP_SIDE on either side is scaled up (aspect preserved).

    A disputed region can be a single glyph — a bullet, a bracket — and
    at its native size that crop is both unreadable to a person and too
    small for a vision tower to accept: LightOn merges patches 2x2, so
    a crop that renders to fewer than two patches on a side crashes the
    image tower rather than returning a bad read."""
    if not _BBOX_RE.match(bbox):
        raise Http404(bbox)
    x0, y0, x1, y1 = (int(c) for c in bbox.split("_"))
    with Image.open(page_png_path(dataset, page)) as im:
        left = max(0, min(x0, im.width - 1))
        top = max(0, min(y0, im.height - 1))
        right = min(im.width, max(x1, left + 1))
        bottom = min(im.height, max(y1, top + 1))
        crop = im.crop((left, top, right, bottom))
        if min(crop.size) < MIN_CROP_SIDE:
            scale = MIN_CROP_SIDE / min(crop.size)
            crop = crop.resize(
                (round(crop.width * scale), round(crop.height * scale)),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
    return buf.getvalue()


def lighton_crop_png(dataset: str, page: str, bbox: str) -> Path | None:
    """The cached LightOn crop image for an exact bbox key
    ("x0_y0_x1_y1"), or None when the cache holds no image for it. The
    cache naming convention is owned by pipeline.engines.lighton."""
    if not _BBOX_RE.match(bbox):
        raise Http404(bbox)
    ds = load_dataset(settings.DATA_ROOT, _safe(dataset))
    return lighton.crop_png(ds, _safe(page), [int(c) for c in bbox.split("_")])
