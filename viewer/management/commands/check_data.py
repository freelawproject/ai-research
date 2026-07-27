"""Validate the data folder layout (docs/pipeline.md) and report what's
present. This is the first thing to run after unzipping the data bundle:
it says exactly what is missing or misplaced instead of letting the viewer
fail mysteriously."""

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pipeline.core.artifacts import SCHEMA_VERSION
from pipeline.core.config import dataset
from pipeline.core.pages import discover_pages

REQUIRED_DIRS = ("redacted", "page_png", "engines")
# engines/<subpath> -> which routes need it (surya's two variants are
# separate inputs and are checked separately).
ENGINE_HINTS = {
    "container_yolo": "layout stage",
    "dots": "main OCR (every route)",
    "gemini": "gemini route (generated upstream)",
    "mistral": "mistral + three_way routes",
    "surya/line": "surya_line route",
    "surya/block": "surya_block + three_way routes",
    "lighton_crops": "tiebreak cache",
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

    def handle(self, *args: Any, **options: Any) -> None:
        root: Path = settings.DATA_ROOT
        if not root.is_dir():
            raise CommandError(
                f"no data folder at {root} — unzip the data bundle at the "
                "repo root so it sits at ./data/ (see README)"
            )
        datasets_root: Path = settings.DATASETS_ROOT
        names = (
            [options["dataset"]]
            if options["dataset"]
            else sorted(p.name for p in datasets_root.glob("*") if p.is_dir())
        )
        if not names:
            raise CommandError(f"no datasets under {datasets_root}")

        problems = 0
        for name in names:
            ds = datasets_root / name
            if not ds.is_dir():
                raise CommandError(f"no dataset at {ds}")
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
                n_pages = len(discover_pages(dataset(root, name)))
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
                        "  ✗ no discoverable pages — each volume needs "
                        "redacted/<reporter>/<volume>/<first_page>/ with "
                        "detections.json and the source PDF"
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
        weights = settings.WEIGHTS_ROOT
        n_w = self._count(weights) if weights.is_dir() else 0
        self.stdout.write(f"weights: {n_w} file(s) under {weights}")

        if problems:
            raise CommandError(f"{problems} problem(s) found")
        self.stdout.write(self.style.SUCCESS("data layout OK"))
