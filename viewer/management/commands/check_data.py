"""Validate the data folder layouts and report what's present. Two
roots, one per surface: ./data/ (routes; docs/pipeline.md) and
./align_data/ (alignment; docs/alignment.md). This is the first thing
to run after unzipping a bundle: it says exactly what is missing or
misplaced instead of letting the viewer fail mysteriously."""

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pipeline.align_run import ALIGN_DIR, ALIGN_SCHEMA_VERSION
from pipeline.core.artifacts import SCHEMA_VERSION
from pipeline.core.config import dataset
from pipeline.core.pages import discover_pages

REQUIRED_DIRS = ("redacted", "page_png", "engines")
# engines/<subpath> -> which combinations need it.
ENGINE_HINTS = {
    "container_yolo": "layout detection (every combination)",
    "dots": "main whenever picked; required for lighton tiebreaks",
    "gemini": "combinations that pick gemini (generated upstream)",
    "mistral": "combinations that pick mistral",
    "surya/block": "combinations that pick surya_block",
    "lighton_crops": "lighton tiebreak cache",
}
# An align set is prerendered: page_png/ + engines/ are the set. source/
# holds the whole-volume PDFs behind the renders — provenance only, so a
# bundle may omit it. Three OCR engines are aligned; the container model
# is drawn as an overlay; gemini and the lighton crop cache play no part.
ALIGN_REQUIRED_DIRS = ("page_png", "engines")
ALIGN_ENGINE_HINTS = {
    "container_yolo": "page-image overlay (places nothing)",
    "dots": "aligned + voted",
    "mistral": "aligned + voted",
    "surya/block": "aligned + voted",
}


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dataset", help="check one dataset only")

    def _count(self, d: Path, pattern: str = "*") -> int:
        return len([p for p in d.glob(pattern) if p.is_file()])

    def _schema_version(self, artifact_dir: Path) -> Any:
        """schema_version of the first artifact in a route dir (a spot
        check — all artifacts in a dir are written by one run)."""
        first = next(iter(sorted(artifact_dir.glob("*.json"))), None)
        if first is None:
            return None
        try:
            doc = json.loads(first.read_text(encoding="utf-8"))
        except ValueError:
            return None
        return doc.get("schema_version")

    def _check_route_set(self, name: str) -> int:
        ds = settings.DATASETS_ROOT / name
        problems = 0
        self.stdout.write(self.style.MIGRATE_HEADING(f"dataset {name}"))
        for sub in REQUIRED_DIRS:
            d = ds / sub
            if d.is_dir():
                self.stdout.write(f"  ✓ {sub}/")
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ {sub}/ MISSING"))
                problems += 1
        n_png = self._count(ds / "page_png", "*.png")
        self.stdout.write(f"  · {n_png} rendered pages")
        try:
            n_pages = len(discover_pages(dataset(settings.DATA_ROOT, name)))
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"  ✗ page discovery failed: {exc}")
            )
            problems += 1
            n_pages = 0
        if n_pages:
            self.stdout.write(f"  · {n_pages} discoverable pages")
        else:
            self.stdout.write(
                self.style.ERROR(
                    "  ✗ no discoverable pages — redacted/ holds "
                    "either volume trees (<reporter>/<volume>/"
                    "<first_page>/ with detections.json and the "
                    "source PDF) or one single-page redacted PDF "
                    "per page (flat layout; the stem is the page id)"
                )
            )
            problems += 1
        for engine, hint in ENGINE_HINTS.items():
            n = self._count(ds / "engines" / engine)
            mark = "✓" if n else "—"
            self.stdout.write(f"  {mark} engines/{engine}: {n} ({hint})")
        # routes are user-composed; artifacts materialize per
        # combination (on first viewer visit, or via pipeline.run)
        art = settings.ARTIFACTS_ROOT / name
        combos = (
            sorted(p.name for p in art.iterdir() if p.is_dir())
            if art.is_dir()
            else []
        )
        if not combos:
            self.stdout.write(
                "  — artifacts: none yet (built on first viewer "
                "visit, or run: uv run python -m pipeline.run "
                f"--dataset {name} --route all)"
            )
        for route in combos:
            d = art / route
            n = self._count(d, "*.json")
            version = self._schema_version(d)
            if version == SCHEMA_VERSION:
                self.stdout.write(f"  ✓ artifacts/{route}: {n}")
            else:
                self.stdout.write(
                    f"  — artifacts/{route}: {n} at schema "
                    f"v{version} (stale; rebuilt on next visit or "
                    f"via: uv run python -m pipeline.run --dataset "
                    f"{name} --route {route})"
                )
        return problems

    def _check_align_set(self, name: str) -> int:
        ds = settings.ALIGN_DATASETS_ROOT / name
        problems = 0
        self.stdout.write(self.style.MIGRATE_HEADING(f"align set {name}"))
        for sub in ALIGN_REQUIRED_DIRS:
            d = ds / sub
            if d.is_dir():
                self.stdout.write(f"  ✓ {sub}/")
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ {sub}/ MISSING"))
                problems += 1
        if (ds / "source").is_dir():
            self.stdout.write("  ✓ source/")
        else:
            self.stdout.write(
                "  — source/ absent (provenance only; a bundle may omit it)"
            )
        n_png = self._count(ds / "page_png", "*.png")
        self.stdout.write(f"  · {n_png} rendered pages")
        try:
            n_pages = len(
                discover_pages(dataset(settings.ALIGN_DATA_ROOT, name))
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"  ✗ page discovery failed: {exc}")
            )
            problems += 1
            n_pages = 0
        if n_pages:
            self.stdout.write(f"  · {n_pages} discoverable pages")
        else:
            self.stdout.write(
                self.style.ERROR(
                    "  ✗ no discoverable pages — an align set ships "
                    "its renders as page_png/<volume>__page_<n>.png"
                )
            )
            problems += 1
        for engine, hint in ALIGN_ENGINE_HINTS.items():
            n = self._count(ds / "engines" / engine)
            mark = "✓" if n else "—"
            self.stdout.write(f"  {mark} engines/{engine}: {n} ({hint})")
        art = settings.ALIGN_ARTIFACTS_ROOT / name / ALIGN_DIR
        rebuild = f"uv run python -m pipeline.align_run --dataset {name}"
        n = self._count(art, "*.json") if art.is_dir() else 0
        if not n:
            self.stdout.write(
                "  — artifacts/align: none yet (built on first viewer "
                f"visit, or run: {rebuild})"
            )
            return problems
        version = self._schema_version(art)
        if version == ALIGN_SCHEMA_VERSION:
            self.stdout.write(f"  ✓ artifacts/{ALIGN_DIR}: {n}")
        else:
            self.stdout.write(
                f"  — artifacts/{ALIGN_DIR}: {n} at schema v{version} "
                f"(stale; rebuilt on next visit or via: {rebuild})"
            )
        return problems

    def handle(self, *args: Any, **options: Any) -> None:
        only = options["dataset"]
        route_root: Path = settings.DATA_ROOT
        align_root: Path = settings.ALIGN_DATA_ROOT
        if not route_root.is_dir() and not align_root.is_dir():
            raise CommandError(
                f"no data folder at {route_root} or {align_root} — unzip "
                "a bundle at the repo root: route sets sit at ./data/, "
                "align sets at ./align_data/ (see README)"
            )

        problems = 0
        found = 0
        if route_root.is_dir():
            names = (
                [only]
                if only
                else sorted(
                    p.name
                    for p in settings.DATASETS_ROOT.glob("*")
                    if p.is_dir()
                )
            )
            for name in names:
                if not (settings.DATASETS_ROOT / name).is_dir():
                    continue
                found += 1
                problems += self._check_route_set(name)
            weights = settings.WEIGHTS_ROOT
            n_w = self._count(weights) if weights.is_dir() else 0
            self.stdout.write(f"weights: {n_w} file(s) under {weights}")
        if align_root.is_dir():
            names = (
                [only]
                if only
                else sorted(
                    p.name
                    for p in settings.ALIGN_DATASETS_ROOT.glob("*")
                    if p.is_dir()
                )
            )
            for name in names:
                if not (settings.ALIGN_DATASETS_ROOT / name).is_dir():
                    continue
                found += 1
                problems += self._check_align_set(name)
        if not found:
            where = (
                f"{settings.DATASETS_ROOT} or {settings.ALIGN_DATASETS_ROOT}"
            )
            raise CommandError(
                f"no dataset named {only} under {where}"
                if only
                else f"no datasets under {where}"
            )

        if problems:
            raise CommandError(f"{problems} problem(s) found")
        self.stdout.write(self.style.SUCCESS("data layout OK"))
