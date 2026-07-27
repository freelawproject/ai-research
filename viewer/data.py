"""Artifact index — the viewer's only data layer.

There is no database: this module scans the data folder and reads the
pipeline's JSON artifacts (docs/pipeline.md). Routes are composed by the
user (three models), so artifacts are materialized LAZILY: the first
visit to a page under a combination builds its artifact from the cached
engine outputs and writes it to disk (same contract, same files the CLI
writes); later visits just read it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.http import Http404

from pipeline.core.artifacts import SCHEMA_VERSION, artifact_path
from pipeline.core.config import dataset as load_dataset
from pipeline.core.pages import discover_pages
from pipeline.routes import Route
from pipeline.run import build_page_artifact

_NAME_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")


def _safe(name: str) -> str:
    if not _NAME_RE.match(name) or ".." in name:
        raise Http404(name)
    return name


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    n_pages: int
    engines: list[str]
    routes: dict[str, int]  # route name -> artifact count


def list_datasets() -> list[DatasetInfo]:
    root: Path = settings.DATASETS_ROOT
    out: list[DatasetInfo] = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        engines = sorted(
            p.name for p in (d / "engines").glob("*") if p.is_dir()
        )
        routes = {}
        art = settings.ARTIFACTS_ROOT / d.name
        if art.is_dir():
            for r in sorted(p for p in art.iterdir() if p.is_dir()):
                routes[r.name] = len(list(r.glob("*.json")))
        out.append(
            DatasetInfo(
                name=d.name,
                n_pages=len(list((d / "page_png").glob("*.png"))),
                engines=engines,
                routes=routes,
            )
        )
    return out


def route_pages(dataset: str, route: str) -> list[str]:
    """Sorted page ids that have an artifact for this dataset x route."""
    d = settings.ARTIFACTS_ROOT / _safe(dataset) / _safe(route)
    return sorted(f.stem for f in d.glob("*.json"))


def dataset_pages(dataset: str) -> list[str]:
    """Every renderable page of a dataset (route-independent)."""
    d = settings.DATASETS_ROOT / _safe(dataset) / "page_png"
    return sorted(f.stem for f in d.glob("*.png"))


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
    out.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    return artifact


def ensure_artifact(dataset: str, route: Route, page: str) -> dict:
    """The artifact for dataset x route x page, materializing it from
    the cached engine outputs when missing or written by an older
    pipeline (routes are user-composed, so combinations are built on
    first visit — the artifact contract on disk stays the source of
    truth)."""
    f = (
        settings.ARTIFACTS_ROOT
        / _safe(dataset)
        / _safe(route.name)
        / f"{_safe(page)}.json"
    )
    if f.exists():
        doc: dict = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("schema_version") == SCHEMA_VERSION:
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
