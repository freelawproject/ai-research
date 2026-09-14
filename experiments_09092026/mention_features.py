"""Per-mention surface features for the coref linker (port of
experiments_06142026/baselines_citation.py::emit_features): kind
(reporter | id | supra | name), name tokens (from the span and the 8 words
before a reporter/id span), reporter key, is_id / is_supra.

    uv run python mention_features.py            # data/output/citations.jsonl -> mention_features.jsonl
"""

import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
RE_ID = re.compile(r"^\s*(id\.|id\b|ibid)", re.I)
RE_REPORTER = re.compile(r"^\s*(\d+)\s+([A-Za-z.'’ ]+?)\s*\d")
RE_ANY_REPORTER = re.compile(r"\b\d+\s+[A-Z]")
RE_CAPWORD = re.compile(r"[A-Z][A-Za-z'’]{2,}")
STOP = {"the", "in", "see", "but", "and", "for", "this", "that", "court", "section", "rule", "act", "id",
        "ibid", "supra", "infra", "also", "cf", "compare", "accord", "contra", "ante", "post", "slip", "no",
        "op", "stat", "const", "amend", "art", "ed", "vol", "pt", "us", "fed", "supp", "cir", "ct", "app",
        "div", "civ", "rev", "justice", "judge", "chief", "syllabus", "opinion", "dissent", "concurring",
        "concurrence", "majority"}


def reporter_key(text):
    m = RE_REPORTER.match(text)
    return (m.group(1) + " " + m.group(2)).strip().lower().replace(" ", "") if m else None


def name_tokens(text):
    return {w.lower() for w in RE_CAPWORD.findall(text) if w.lower() not in STOP}


def classify(text):
    t = text.strip()
    if RE_ID.match(t):
        return "id"
    if "supra" in t.lower():
        return "supra"
    if RE_REPORTER.match(t) or RE_ANY_REPORTER.search(t):
        return "reporter"
    return "name"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=os.path.join(HERE, "data", "output", "citations.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "output", "mention_features.jsonl"))
    a = ap.parse_args()
    n = 0
    with open(a.jsonl, encoding="utf-8") as f, open(a.out, "w", encoding="utf-8") as out:
        for ln in f:
            r = json.loads(ln)
            text = r["text"]
            for m in r["mentions"]:
                kind = classify(m["text"])
                names = name_tokens(m["text"])
                if kind in ("reporter", "id"):
                    names |= name_tokens(" ".join(text[:m["start"]].split()[-8:]))
                out.write(json.dumps({"citing_cluster_id": r["citing_cluster_id"], "opinion_idx": r["opinion_idx"],
                                      "start": m["start"], "kind": kind, "reporter_key": reporter_key(m["text"]),
                                      "name_tokens": sorted(names), "is_id": kind == "id",
                                      "is_supra": kind == "supra", "gold_cluster": m["cluster"]}) + "\n")
                n += 1
    print(f"wrote {n} mention features -> {a.out}")


if __name__ == "__main__":
    main()
