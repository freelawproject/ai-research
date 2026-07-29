"""Export the crop bundle the LightOn tiebreaker is missing, packaged as
the lighton pod kit's input contract — crops/<key>.png +
manifest.jsonl ({key, expect, area} per crop; expect = the main
engine's reading of the region, used by the kit as a completeness check
only).

Two kinds of crop are missing a read, and both go in the bundle:
  - crops never read at all;
  - crops whose first read was discarded by the compare stage's
    degeneration guards and which are waiting on their ONE retry. These
    carry the RETRY_SUFFIX in their key and a "decode" object holding
    the adjusted settings the retry must run under; the kit writes them
    to reads/<key><RETRY_SUFFIX>.txt, which is where the compare stage
    looks for a retry read.

Artifacts for the requested combinations are built as needed to
enumerate the disputes. Each crop PNG is also written into the
dataset's engines/lighton_crops/ (the cache-image convention, so the
dispute cards can show what was read). After the pod run, feed the
reads tarball to `manage.py ingest_reads` and re-run the combinations.
"""

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from pipeline import routes
from pipeline.engines import lighton
from viewer import data


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dataset", required=True)
        parser.add_argument(
            "--route",
            action="append",
            default=None,
            help=(
                "a lighton combination to export for (repeatable); "
                "default: every lighton combination"
            ),
        )
        parser.add_argument(
            "--out",
            type=Path,
            default=None,
            help="bundle directory (default data/exports/lighton_<dataset>)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        name = options["dataset"]
        wanted = options["route"] or [
            r.name for r in routes.combos_for(name) if r.tiebreak
        ]
        if not wanted:
            raise CommandError(
                f"{name} does not present the tiebreak combinations, so it "
                "reads no crops (pipeline.core.config.TIEBREAK_DATASETS)"
            )
        combos = []
        for r in wanted:
            route = routes.resolve(r)
            if route.tiebreak is None:
                raise CommandError(
                    f"{route.name} is a vote combination — only lighton "
                    "tiebreaks read crops"
                )
            combos.append(route)
        pages = data.dataset_pages(name)
        if not pages:
            raise CommandError(f"no pages in dataset {name}")

        # key -> (page, bbox, expect, retry); the same block disputed on
        # several combinations exports once
        crops: dict[str, tuple[str, list[int], str, bool]] = {}
        for route in combos:
            n_first = n_retry = 0
            for page in pages:
                try:
                    artifact = data.ensure_artifact(name, route, page)
                except Http404:
                    continue  # page not runnable on this combination
                stages = artifact["stages"]
                text_of = {
                    b["id"]: b.get("text", "")
                    for b in stages["main_ocr"]["blocks"]
                }
                bbox_of = {
                    b["id"]: b["bbox"]
                    for b in stages["main_ocr"]["blocks"]
                    if b.get("bbox")
                }
                for d in stages["compare"]["disputes"]:
                    tb = d.get("tiebreak")
                    if not tb:
                        continue
                    retry = bool(tb.get("retry_pending"))
                    if tb["cached"] and not retry:
                        continue
                    for block in d["blocks"]:
                        bbox = bbox_of.get(block)
                        if not bbox:
                            continue
                        stem = f"{page}_" + "_".join(str(int(c)) for c in bbox)
                        key = stem + (lighton.RETRY_SUFFIX if retry else "")
                        if key not in crops:
                            crops[key] = (
                                page,
                                bbox,
                                text_of.get(block, ""),
                                retry,
                            )
                            if retry:
                                n_retry += 1
                            else:
                                n_first += 1
            self.stdout.write(
                f"{route.name}: {n_first} new crop(s), {n_retry} retry"
            )

        out: Path = (
            options["out"]
            or settings.DATA_ROOT / "exports" / f"lighton_{name}"
        )
        crops_dir = out / "crops"
        crops_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = settings.DATASETS_ROOT / name / "engines" / "lighton_crops"
        cache_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for key, (page, bbox, expect, retry) in sorted(crops.items()):
            bbox_key = "_".join(str(int(c)) for c in bbox)
            png = data.page_crop_png(name, page, bbox_key)
            (crops_dir / f"{key}.png").write_bytes(png)
            (cache_dir / f"{key}.png").write_bytes(png)
            x0, y0, x1, y1 = bbox
            entry = {
                "key": key,
                "expect": expect,
                "area": (x1 - x0) * (y1 - y0),
            }
            if retry:
                # the retry reads the SAME crop under tighter decoding
                entry["decode"] = lighton.retry_decode(expect)
            lines.append(json.dumps(entry, ensure_ascii=False))
        (out / "manifest.jsonl").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(crops)} crop(s) -> {out} (manifest.jsonl + crops/)"
            )
        )
        self.stdout.write(
            "next: stage this bundle into the lighton kit "
            "(data/sets/<set>/ then pack.sh lighton <set>), run the pod, "
            f"then: uv run python manage.py ingest_reads --dataset {name} "
            "<lighton_reads tarball>"
        )
