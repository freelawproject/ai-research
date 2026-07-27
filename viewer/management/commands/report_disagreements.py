"""Cross-engine disagreement report — the phenomena-surfacing tool for
the rule-by-rule normalization work (docs/pipeline.md, stage 6).

For every artifact of a dataset, the normalized KEY stream of the main
engine is diffed against each supplemental engine's stream, and the
distinct (main, supplemental) disagreement pairs are ranked by frequency
per engine. Each frequent pair is a candidate normalization rule; what
remains after the registry has absorbed the format phenomena is a real
OCR dispute, which stage 7 owns. A page appears once per supplemental
engine even when routes overlap (three_way re-runs mistral and surya)."""

import difflib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pipeline.core.normalize import registry_hash


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dataset", required=True)
        parser.add_argument(
            "--top",
            type=int,
            default=25,
            help="disagreement pairs to show per engine (default 25)",
        )
        parser.add_argument(
            "--min-count",
            type=int,
            default=2,
            help="hide pairs seen fewer times than this (default 2)",
        )

    def _streams(self, doc: dict) -> list[tuple[str, list[str], list[str]]]:
        """(comparison label, main keys, supplemental keys) per supp —
        the label names BOTH sides (routes are user-composed, so the
        main engine varies)."""
        norm = doc.get("stages", {}).get("normalize")
        if not norm:
            return []
        main = [t["key"] for t in norm["main"]["tokens"]]
        return [
            (
                f"{norm['main']['engine']} vs {s['engine']}/{s['unit']}",
                main,
                [t["key"] for t in s["tokens"]],
            )
            for s in norm["supplementals"]
        ]

    def handle(self, *args: Any, **options: Any) -> None:
        root: Path = settings.ARTIFACTS_ROOT / options["dataset"]
        if not root.is_dir():
            raise CommandError(
                f"no artifacts under {root} — run: uv run python -m "
                f"pipeline.run --dataset {options['dataset']} --route all"
            )
        pairs: dict[str, Counter[tuple[str, str]]] = {}
        examples: dict[str, dict[tuple[str, str], list[str]]] = {}
        spans: Counter[str] = Counter()
        tokens: Counter[str] = Counter()
        seen: set[tuple[str, str]] = set()
        n_pages = 0
        for f in sorted(root.glob("*/*.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            page = doc["page"]
            n_pages += 1
            for label, main, supp in self._streams(doc):
                if (page, label) in seen:
                    continue  # same comparison via an overlapping route
                seen.add((page, label))
                tokens[label] += len(main)
                sm = difflib.SequenceMatcher(a=main, b=supp, autojunk=False)
                for tag, i1, i2, j1, j2 in sm.get_opcodes():
                    if tag == "equal":
                        continue
                    pair = (
                        " ".join(main[i1:i2]),
                        " ".join(supp[j1:j2]),
                    )
                    spans[label] += 1
                    pairs.setdefault(label, Counter())[pair] += 1
                    ex = examples.setdefault(label, {}).setdefault(pair, [])
                    if len(ex) < 3 and page not in ex:
                        ex.append(page)
        if not seen:
            raise CommandError(
                "no normalize stage in any artifact — re-run the pipeline "
                "(schema v2+)"
            )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"disagreements — dataset {options['dataset']}, "
                f"registry {registry_hash()}, {n_pages} artifacts"
            )
        )
        for label in sorted(pairs):
            counter = pairs[label]
            distinct = len(counter)
            shown = 0
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"\n{label} — {spans[label]} disagreement "
                    f"spans over {tokens[label]} main tokens, "
                    f"{distinct} distinct"
                )
            )
            for (a, b), n in counter.most_common():
                if n < options["min_count"] or shown >= options["top"]:
                    break
                shown += 1
                where = ", ".join(examples[label][(a, b)])
                self.stdout.write(
                    f"  {n:>4}×  {a or '∅'!r}  ⇄  {b or '∅'!r}   [{where}]"
                )
            rest = distinct - shown
            if rest > 0:
                self.stdout.write(
                    f"  … {rest} more distinct pair(s) below the "
                    "count/top cutoff"
                )
