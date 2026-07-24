"""Validate the data folder layout (docs/pipeline.md) and report what's
present. This is the first thing to run after unzipping the data bundle:
it says exactly what is missing or misplaced instead of letting the viewer
fail mysteriously."""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pipeline.routes import ROUTES

REQUIRED_DIRS = ("redacted", "page_png", "engines")
ENGINE_HINTS = {
    "container_yolo": "layout stage",
    "dots": "main OCR (every route)",
    "gemini": "gemini route (generated upstream)",
    "mistral": "mistral route",
    "surya": "surya route",
    "lighton_crops": "tiebreak cache",
}


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dataset", help="check one dataset only")

    def _count(self, d: Path, pattern: str = "*") -> int:
        return len([p for p in d.glob(pattern) if p.is_file()])

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
            else sorted(
                p.name for p in datasets_root.glob("*") if p.is_dir()
            )
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
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ {sub}/ MISSING")
                    )
                    problems += 1
            n_png = self._count(ds / "page_png", "*.png")
            self.stdout.write(f"  · {n_png} rendered pages")
            for engine, hint in ENGINE_HINTS.items():
                d = ds / "engines" / engine
                n = (
                    self._count(d)
                    if engine != "surya"
                    else self._count(d / "line")
                )
                mark = "✓" if n else "—"
                self.stdout.write(f"  {mark} engines/{engine}: {n} ({hint})")
            for route in sorted(ROUTES):
                d = settings.ARTIFACTS_ROOT / name / route
                n = self._count(d, "*.json")
                if n:
                    self.stdout.write(f"  ✓ artifacts/{route}: {n}")
                else:
                    self.stdout.write(
                        f"  — artifacts/{route}: none (run: uv run python "
                        f"-m pipeline.run --dataset {name} --route {route})"
                    )
        weights = settings.WEIGHTS_ROOT
        n_w = self._count(weights) if weights.is_dir() else 0
        self.stdout.write(f"weights: {n_w} file(s) under {weights}")

        if problems:
            raise CommandError(f"{problems} problem(s) found")
        self.stdout.write(self.style.SUCCESS("data layout OK"))
