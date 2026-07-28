"""Dispute report — the corpus view of compare + resolve.

For every route with artifacts under a dataset: the dispute counts, how
they resolved (majority / main-authoritative / fallback by reason), the
LightOn tiebreak funnel (attempted -> cached -> accepted -> voted), and
the low-confidence disputes themselves (the spans a human should look
at, ranked by page)."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dataset", required=True)
        parser.add_argument(
            "--low-conf",
            type=int,
            default=15,
            help="low-confidence examples to list per route (default 15)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        root: Path = settings.ARTIFACTS_ROOT / options["dataset"]
        if not root.is_dir():
            raise CommandError(
                f"no artifacts under {root} — run: uv run python -m "
                f"pipeline.run --dataset {options['dataset']} --route all"
            )
        found = False
        for route_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            pages = 0
            units = disputed = low_conf = high_risk = 0
            resolutions: Counter[str] = Counter()
            reasons: Counter[str] = Counter()
            funnel: Counter[str] = Counter()
            degraded: list[str] = []
            examples: list[tuple[str, dict]] = []
            for f in sorted(route_dir.glob("*.json")):
                doc = json.loads(f.read_text(encoding="utf-8"))
                cmp = doc.get("stages", {}).get("compare")
                if not cmp:
                    continue
                found = True
                pages += 1
                m = cmp["metrics"]
                units += m["main_units"]
                disputed += m["disputed_units"]
                low_conf += m["n_low_confidence"]
                high_risk += m.get("n_high_risk", 0)
                resolutions.update(m["by_resolution"])
                reasons.update(m["by_reason"])
                funnel.update(m.get("tiebreak", {}))
                if cmp["degraded"]:
                    degraded.append(
                        f"{doc['page']} ({', '.join(cmp['degraded'])})"
                    )
                examples += [
                    (doc["page"], d)
                    for d in cmp["disputes"]
                    if d["low_confidence"]
                ]
            if not pages:
                continue
            n_disputes = sum(resolutions.values())
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"\n{route_dir.name} — {pages} pages, {n_disputes} "
                    f"disputes over {units} main units "
                    f"({disputed} disputed)"
                )
            )
            self.stdout.write(
                "  resolved: "
                + " · ".join(f"{k} {v}" for k, v in resolutions.most_common())
            )
            if reasons:
                self.stdout.write(
                    "  fallback reasons: "
                    + " · ".join(f"{k} {v}" for k, v in reasons.most_common())
                )
            if funnel:
                self.stdout.write(
                    "  tiebreak funnel: "
                    f"{funnel['attempted']} attempted -> "
                    f"{funnel['cached']} cached -> "
                    f"{funnel['accepted']} accepted -> "
                    f"{funnel['voted']} voted"
                )
            if degraded:
                self.stdout.write("  degraded pages: " + ", ".join(degraded))
            self.stdout.write(
                f"  low-confidence: {low_conf}"
                + (f" ({high_risk} high risk)" if low_conf else "")
                + ("" if low_conf else " — every dispute resolved")
            )
            for page, d in examples[: options["low_conf"]]:
                reads = " ⇄ ".join(
                    f"{k}:{v or '∅'!r}" for k, v in d["reads"].items()
                )
                self.stdout.write(f"    {page}  [{d['reason']}]  {reads}")
            rest = len(examples) - options["low_conf"]
            if rest > 0:
                self.stdout.write(f"    … {rest} more")
        if not found:
            raise CommandError(
                "no compare record in any artifact — these artifacts "
                "predate compare + resolve; re-run the pipeline"
            )
