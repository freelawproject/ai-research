"""Stage 3 — normalize raw predictions into viewable tagged docs.

Takes preprocess.py's canonical docs plus infer.py's raw spans and
produces the final tagged documents:

- structural confinement (repo-root ``spannorm.confine_spans``): spans
  split at block tags, edges trimmed of whitespace/markup — only plain
  text is ever labeled;
- the footnote rule: nothing inside a ``<footnotes>`` band is tagged
  (footnote bands carry no headmatter; spans overlapping a band drop);
- optionally (``--union-layout-headings``) seed a ``heading`` span for
  each layout-detected visual heading block the tagger left unlabeled,
  marked ``src: "layout"`` — the union used when seeding training
  labels; plain inference leaves it off.

Output: ``<out>/<range>.json`` = ``{range, text, pages, block_spans}``
— the shape the delivery viewer's "pipeline samples" view reads from
``data/samples/``.

    uv run python postprocess.py                 # → ../data/samples
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # repo root: spannorm.py

from spannorm import confine_spans  # noqa: E402


def drop_footnote_band_spans(text: str, spans: list[dict]) -> list[dict]:
    bands = [
        (m.start(),
         text.find("</footnotes>", m.end()) + len("</footnotes>"))
        for m in re.finditer(r"<footnotes>", text)
    ]
    return [
        s for s in spans
        if not any(s["start"] < b1 and s["end"] > b0 for b0, b1 in bands)
    ]


def union_layout_headings(text: str, spans: list[dict],
                          layout: list[dict]) -> list[dict]:
    out = list(spans)
    for ls in confine_spans(text, [{**s, "label": "heading"}
                                   for s in layout]):
        if any(s["start"] < ls["end"] and s["end"] > ls["start"]
               for s in out):
            continue  # the tagger labeled here — its reading wins
        out.append({**ls, "src": "layout"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--docs", type=Path, default=HERE / "work" / "docs")
    ap.add_argument("--raw", type=Path, default=HERE / "work" / "raw")
    ap.add_argument("--out", type=Path,
                    default=HERE.parent / "data" / "samples")
    ap.add_argument("--ranges", nargs="*", default=[])
    ap.add_argument("--union-layout-headings", action="store_true")
    args = ap.parse_args()

    raws = sorted(args.raw.glob("*.json"))
    if args.ranges:
        raws = [p for p in raws if p.stem in args.ranges]
    if not raws:
        print(f"no raw predictions under {args.raw} — run infer.py first")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    totals: collections.Counter = collections.Counter()
    for path in raws:
        doc_path = args.docs / path.name
        if not doc_path.exists():
            print(f"!! {path.stem}: no matching doc in {args.docs}")
            return 1
        doc = json.loads(doc_path.read_text())
        raw = json.loads(path.read_text())
        text = doc["text"]
        spans = confine_spans(text, raw["spans"])
        spans = drop_footnote_band_spans(text, spans)
        if args.union_layout_headings:
            spans = union_layout_headings(
                text, spans, doc.get("layout_heading_spans", [])
            )
        spans.sort(key=lambda s: (s["start"], s["end"]))
        (args.out / path.name).write_text(json.dumps(
            {"range": doc["range"], "text": text, "pages": doc["pages"],
             "block_spans": spans},
            ensure_ascii=False,
        ))
        by = collections.Counter(s["label"] for s in spans)
        totals.update(by)
        print(f"{path.stem}: {len(spans)} spans {dict(by.most_common())}")
    print(f"\nTOTAL {sum(totals.values())} spans: "
          f"{dict(totals.most_common())}")
    print(f"→ {args.out} (view: uv run uvicorn app:app --port 8180, "
          "dataset “pipeline samples”)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
