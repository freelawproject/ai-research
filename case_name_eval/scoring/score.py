"""Score case-name predictions against the gold set.

    python3 score.py --production                      # names on the clusters today
    python3 score.py --predictions preds.jsonl --name my-model

A predictions file has one JSON object per line: `cluster_id`,
`case_name_full`, `case_name`. `--production` reads the names stored on the
clusters when the sample was pulled (../data/annotations.db).

Writes the summary to ../results/<name>.json and one row per record to
../data/scores/<name>_records.jsonl, and prints the summary. Standard library
only, so it runs without the annotator's environment.

Metrics, per field:

- match        the prediction equals the gold name, ignoring capitalization,
               spacing and punctuation. An empty gold name (no case name in
               the document) is matched by an empty prediction.
- strict       the prediction equals the gold name character for character,
               after collapsing spaces.
- word_overlap F1 between the words of the prediction and of the gold name
               (1.0 = same words, 0.0 = none shared). Partial credit for a
               name that is nearly right.
- returned     the prediction is not empty.

Segments:

- answerable   the whole caption is inside the 2,000 characters the production
               model reads (no `caption_beyond_input` / `caption_cut_off` flag)
- beyond_2k    the gold name needs text past 2,000 characters
- no_case_name the document has no case name; the right answer is empty
"""

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RESULTS = HERE.parent / "results"
FIELDS = {"full": "case_name_full", "short": "case_name"}
BEYOND = {"caption_beyond_input", "caption_cut_off"}


def comparable(name: str) -> str:
    """A name reduced to its letters and digits. Same rule as annotator/store.py."""
    return "".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def tokens(name: str) -> Counter:
    return Counter(re.findall(r"[a-z0-9]+", (name or "").lower()))


def word_overlap(pred: str, gold: str) -> float:
    p, g = tokens(pred), tokens(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    shared = sum((p & g).values())
    if not shared:
        return 0.0
    precision, recall = shared / sum(p.values()), shared / sum(g.values())
    return 2 * precision * recall / (precision + recall)


def segment(gold: dict) -> str:
    if "no_caption" in gold["flags"] or not gold["case_name_full"]:
        return "no_case_name"
    return "beyond_2k" if BEYOND & set(gold["flags"]) else "answerable"


def score_one(pred: str, gold: str) -> dict:
    pred, gold = pred or "", gold or ""
    return {
        "match": float(comparable(pred) == comparable(gold)),
        "strict": float(" ".join(pred.split()) == " ".join(gold.split())),
        "word_overlap": word_overlap(pred, gold),
        "returned": float(bool(pred.strip())),
    }


def load_gold() -> dict[str, list[dict]]:
    out = {}
    for split in ("validation", "test"):
        path = DATA / "gold" / f"{split}.jsonl"
        out[split] = [
            json.loads(line) for line in path.read_text().splitlines() if line
        ]
    return out


def load_production() -> dict[int, dict]:
    conn = sqlite3.connect(DATA / "annotations.db")
    return {
        cid: {"case_name_full": full or "", "case_name": short or ""}
        for cid, full, short in conn.execute(
            "SELECT cluster_id, prod_case_name_full, prod_case_name FROM records"
        )
    }


def load_predictions(path: str) -> dict[int, dict]:
    out = {}
    for line in Path(path).read_text().splitlines():
        if line:
            item = json.loads(line)
            out[int(item["cluster_id"])] = item
    return out


def summarize(rows: list[dict]) -> dict:
    """Mean of every metric over a list of per-record scores."""
    if not rows:
        return {"n": 0}
    out = {"n": len(rows)}
    for field in FIELDS:
        for metric in ("match", "strict", "word_overlap", "returned"):
            out[f"{field}_{metric}"] = round(
                sum(r[field][metric] for r in rows) / len(rows), 4
            )
    out["both_match"] = round(
        sum(r["full"]["match"] * r["short"]["match"] for r in rows) / len(rows), 4
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--production", action="store_true")
    source.add_argument("--predictions")
    parser.add_argument("--name", help="label for the results file")
    args = parser.parse_args()

    name = args.name or (
        "production" if args.production else Path(args.predictions).stem
    )
    preds = load_production() if args.production else load_predictions(args.predictions)
    gold = load_gold()

    report = {"name": name, "splits": {}}
    for split, records in gold.items():
        scored = []
        for g in records:
            p = preds.get(g["cluster_id"], {})
            scored.append(
                {
                    "cluster_id": g["cluster_id"],
                    "stratum": g["stratum"],
                    "doc_type": g["doc_type"],
                    "segment": segment(g),
                    "full": score_one(p.get("case_name_full"), g["case_name_full"]),
                    "short": score_one(p.get("case_name"), g["case_name"]),
                }
            )
        groups = {"all": summarize(scored)}
        for key in ("segment", "stratum", "doc_type"):
            buckets = defaultdict(list)
            for row in scored:
                buckets[row[key]].append(row)
            groups[key] = {k: summarize(v) for k, v in sorted(buckets.items())}
        report["splits"][split] = groups
        report["splits"][split]["missing_predictions"] = sum(
            1 for g in records if g["cluster_id"] not in preds
        )
        report["splits"][split]["records"] = scored

    # Per-record scores hold one row per gold record, so they stay with the gold.
    records_path = DATA / "scores" / f"{name}_records.jsonl"
    records_path.parent.mkdir(parents=True, exist_ok=True)
    with records_path.open("w") as out:
        for split, groups in report["splits"].items():
            for row in groups.pop("records"):
                out.write(json.dumps({"split": split, **row}) + "\n")

    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{name}.json"
    out_path.write_text(json.dumps(report, indent=1))

    for split, groups in report["splits"].items():
        a = groups["all"]
        print(f"\n{name} on {split} (n={a['n']})")
        print(
            f"  full caption: match {a['full_match']:.3f}  strict {a['full_strict']:.3f}  "
            f"word overlap {a['full_word_overlap']:.3f}  returned {a['full_returned']:.3f}"
        )
        print(
            f"  short name:   match {a['short_match']:.3f}  strict {a['short_strict']:.3f}  "
            f"word overlap {a['short_word_overlap']:.3f}  returned {a['short_returned']:.3f}"
        )
        print(f"  both names match: {a['both_match']:.3f}")
        for key in ("segment", "stratum"):
            for label, s in groups[key].items():
                print(
                    f"    {key}={label:<24} n={s['n']:<5} full match {s['full_match']:.3f}  "
                    f"overlap {s['full_word_overlap']:.3f} | short match {s['short_match']:.3f} "
                    f"returned {s['short_returned']:.3f}"
                )
    print(f"\nWrote {out_path} and {records_path}")


if __name__ == "__main__":
    main()
