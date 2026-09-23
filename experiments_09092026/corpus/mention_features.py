"""Per-mention surface features for the coref linker (port of
experiments_06142026/baselines_citation.py::emit_features): kind
(reporter | id | supra | name), name tokens (from the span and the 8 words
before a reporter/id span), reporter key, is_id / is_supra — plus, when
payload dirs are given, `eyecite_gid`: the group eyecite itself put the span
in (href cluster / reporter path / data-id, as build_dataset.silver_records
derives it), or null when eyecite did not tag the span. That column is the
"edit-formulated" input channel of round 5: the linker starts from eyecite's
grouping instead of from scratch.

    uv run python corpus/mention_features.py     # data/output/citations.jsonl -> mention_features.jsonl
    uv run --no-project python corpus/mention_features.py --jsonl data/output/citations_r2.jsonl \\
        --payload-dir data/annotator_r2/data/opinion_html \\
        --payload-dir ../experiments_09022026/triage/data/annotator/data/opinion_html \\
        --out data/output/mention_features_r2e.jsonl          # round 5: + eyecite_gid

Join: the payload's opinions (combined dropped) line up with the record's
opinion_idx; when the normalized texts are byte-identical (all of train)
spans are joined by offset overlap, otherwise (revised HTML re-rendered from
the annotator, ~1/3 of train_high_quality spans) each eyecite span is located in the
record text as the n-th occurrence of its text. Silver splits (round-1
train/val, eyecite labels only) get eyecite_gid = the label cluster itself.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)          # the experiment root; data/ lives there
sys.path.insert(0, HERE)             # build_dataset sits next to this file
import build_dataset as B  # noqa: E402


def gid(m):
    """Coreference group of a mention. Accepts the legacy "cluster" key."""
    return m.get("group", m.get("cluster"))


RE_ID = re.compile(r"^\s*(id\.|id\b|ibid)", re.I)
RE_REPORTER = re.compile(r"^\s*(\d+)\s+([A-Za-z.'’ ]+?)\s*\d")
RE_ANY_REPORTER = re.compile(r"\b\d+\s+[A-Z]")
RE_CAPWORD = re.compile(r"[A-Z][A-Za-z'’]{2,}")
STOP = {"the", "in", "see", "but", "and", "for", "this", "that", "court", "section", "rule", "act", "id",
        "ibid", "supra", "infra", "also", "cf", "compare", "accord", "contra", "ante", "post", "slip", "no",
        "op", "stat", "const", "amend", "art", "ed", "vol", "pt", "us", "fed", "supp", "cir", "ct", "app",
        "div", "civ", "rev", "justice", "judge", "chief", "syllabus", "opinion", "dissent", "concurring",
        "concurrence", "majority"}
SILVER_SPLITS = {"train", "val"}


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


def _nth_occurrence(hay, needle, nth):
    pos = -1
    for _ in range(nth + 1):
        pos = hay.find(needle, pos + 1)
        if pos < 0:
            return -1
    return pos


class EyeciteJoin:
    """eyecite's own grouping for a record's mentions, from the raw
    html_with_citations payloads the labels were corrected from."""

    def __init__(self, payload_dirs):
        self.dirs = [d for d in payload_dirs if d and os.path.isdir(d)]
        self.cache = {}
        self.stats = Counter()

    def _silver(self, cid):
        if cid not in self.cache:
            self.cache[cid] = None
            for d in self.dirs:
                p = os.path.join(d, f"{cid}.json")
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        self.cache[cid] = B.silver_records(json.load(f), "x")
                    break
            if len(self.cache) > 512:                    # payloads are big; keep the working set small
                for k in list(self.cache)[:256]:
                    if k != cid:
                        del self.cache[k]
        return self.cache[cid]

    def spans(self, record):
        """[(start, end, gid)] of eyecite's spans in the RECORD's text frame, or None."""
        silver = self._silver(record["citing_cluster_id"])
        if silver is None or record["opinion_idx"] >= len(silver):
            self.stats["no_payload"] += 1
            return None
        s = silver[record["opinion_idx"]]
        if s["opinion_type"] and record["opinion_type"] and s["opinion_type"] != record["opinion_type"]:
            same = [x for x in silver if x["opinion_type"] == record["opinion_type"]]
            if len(same) != 1:
                self.stats["type_mismatch"] += 1
                return None
            s = same[0]
        text = record["text"]
        if s["text"] == text:
            self.stats["offset_join"] += 1
            return [(m["start"], m["end"], gid(m)) for m in s["mentions"]]
        self.stats["text_join"] += 1
        out = []
        for m in s["mentions"]:
            nth = s["text"].count(m["text"], 0, m["start"])
            pos = _nth_occurrence(text, m["text"], nth)
            if pos >= 0:
                out.append((pos, pos + len(m["text"]), gid(m)))
            else:
                self.stats["span_unanchored"] += 1
        return out

    def gids(self, record):
        """{mention start: eyecite gid} for the record's mentions (overlap join)."""
        spans = self.spans(record)
        if spans is None:
            return {}
        by_start = sorted(spans)
        out = {}
        for m in record["mentions"]:
            for s, e, g in by_start:
                if s >= m["end"]:
                    break
                if e > m["start"]:
                    out[m["start"]] = g
                    break
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=os.path.join(EXP, "data", "output", "citations.jsonl"))
    ap.add_argument("--out", default=os.path.join(EXP, "data", "output", "mention_features.jsonl"))
    ap.add_argument("--payload-dir", action="append", default=[],
                    help="opinion_html payload dir(s) holding the raw html_with_citations the records came from; "
                         "enables the eyecite_gid column (repeatable, first hit wins)")
    a = ap.parse_args()
    join = EyeciteJoin(a.payload_dir) if a.payload_dir else None
    n = 0
    cov = defaultdict(lambda: [0, 0])                    # split -> [with gid, mentions]
    with open(a.jsonl, encoding="utf-8") as f, open(a.out, "w", encoding="utf-8") as out:
        for ln in f:
            r = json.loads(ln)
            text = r["text"]
            gids = {}
            if join is not None:
                if r["split"] in SILVER_SPLITS and all(m["source"] == "eyecite" for m in r["mentions"]):
                    gids = {m["start"]: gid(m) for m in r["mentions"]}
                else:
                    gids = join.gids(r)
            for m in r["mentions"]:
                kind = classify(m["text"])
                names = name_tokens(m["text"])
                if kind in ("reporter", "id"):
                    names |= name_tokens(" ".join(text[:m["start"]].split()[-8:]))
                row = {"citing_cluster_id": r["citing_cluster_id"], "opinion_idx": r["opinion_idx"],
                       "start": m["start"], "kind": kind, "reporter_key": reporter_key(m["text"]),
                       "name_tokens": sorted(names), "is_id": kind == "id",
                       "is_supra": kind == "supra", "gold_group": gid(m)}
                if join is not None:
                    row["eyecite_gid"] = gids.get(m["start"])
                    cov[r["split"]][0] += row["eyecite_gid"] is not None
                    cov[r["split"]][1] += 1
                out.write(json.dumps(row) + "\n")
                n += 1
    print(f"wrote {n} mention features -> {a.out}")
    if join is not None:
        print(f"eyecite join: {dict(join.stats)}")
        for split, (w, t) in sorted(cov.items()):
            print(f"  {split:10} eyecite_gid set on {w}/{t} mentions ({w / t:.1%})" if t else f"  {split}: 0")


if __name__ == "__main__":
    main()
