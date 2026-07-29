"""Ingest a LightOn reads tarball (reads/<key>.txt per crop, the
lighton pod kit's output) into a dataset's crop cache
(engines/lighton_crops/), so the tiebreak combinations can vote.

Artifacts do not rebuild on a cache change by themselves — re-run the
lighton combinations afterwards (the command prints the exact line).
"""

import tarfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dataset", required=True)
        parser.add_argument(
            "tarball", type=Path, help="lighton_reads_<set>.tar.gz"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        name = options["dataset"]
        tar_path: Path = options["tarball"]
        if not tar_path.is_file():
            raise CommandError(f"no tarball at {tar_path}")
        cache = settings.DATASETS_ROOT / name / "engines" / "lighton_crops"
        if not cache.is_dir():
            raise CommandError(f"no dataset at {cache.parent.parent}")
        ingested = replaced = orphaned = 0
        with tarfile.open(tar_path) as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith(".txt"):
                    continue
                # member paths come from the pod: keep the basename only
                target = cache / Path(member.name).name
                f = tar.extractfile(member)
                if f is None:
                    continue
                if target.exists():
                    replaced += 1
                if not (cache / f"{target.stem}.png").exists():
                    orphaned += 1  # a read no exported crop asked for
                target.write_bytes(f.read())
                ingested += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"{ingested} read(s) into {cache} "
                f"({replaced} replaced existing)"
            )
        )
        if orphaned:
            self.stdout.write(
                self.style.WARNING(
                    f"{orphaned} read(s) have no matching exported crop "
                    "image — keys may be from another dataset or run"
                )
            )
        self.stdout.write(
            "next: rebuild the lighton combinations so the votes land — "
            f"e.g. uv run python -m pipeline.run --dataset {name} "
            "--route dots+surya_block+lighton (repeat per combination, "
            "or --route all)"
        )
