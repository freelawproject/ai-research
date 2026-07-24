"""Artifact index — the viewer's only data layer.

There is no database: this module scans the data folder and reads the
pipeline's JSON artifacts (docs/pipeline.md). At review scale (tens of
thousands of pages) direct filesystem reads are fast enough; restart the
server to pick up newly written artifacts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.http import Http404

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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
    out = []
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


def load_artifact(dataset: str, route: str, page: str) -> dict:
    f = (
        settings.ARTIFACTS_ROOT
        / _safe(dataset)
        / _safe(route)
        / f"{_safe(page)}.json"
    )
    if not f.exists():
        raise Http404(f"no artifact for {dataset}/{route}/{page}")
    return json.loads(f.read_text(encoding="utf-8"))


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
