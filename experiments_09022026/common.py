"""Shared loaders + metric helpers for the 0902 citator quality review."""
import collections
import csv
import math
import os
import re

BENCH = "/Users/rachel/Desktop/flp/citator-benchmark/data"
TEXTS = "/Users/rachel/Desktop/flp/ai-research/experiments_05182026/data/opinion_texts"

DIRECT_HISTORY = {
    "Reversed by", "Reversed and remanded by", "Vacated by",
    "Vacated and remanded by", "Reversed in part; Vacated in part by",
    "Affirmed in part; Reversed in part by",
    "Affirmed in part; Vacated in part by", "Modified by", "Remanded by",
    "Cert. granted by", "Dismissed by", "Cert. denied by", "Affirmed by",
}
SEVERITY = {
    "Reversed by": "Stop", "Reversed and remanded by": "Stop",
    "Vacated by": "Stop", "Vacated and remanded by": "Stop",
    "Overruled by": "Stop", "Abrogated by": "Stop", "Questioned by": "Stop",
    "Reversed in part; Vacated in part by": "Warning",
    "Affirmed in part; Reversed in part by": "Warning",
    "Affirmed in part; Vacated in part by": "Warning",
    "Disapproved by": "Warning", "Limited by": "Warning",
    "Modified by": "Caution", "Remanded by": "Caution",
    "Cert. granted by": "Caution", "Criticized by": "Caution",
    "Distinguished by": "Caution", "Declined to follow by": "Caution",
    "Dismissed by": "Neutral", "Cited by": "Neutral",
    "Cert. denied by": "Neutral", "Affirmed by": "Neutral",
}


def base(t):
    """Fold 'X as recognized by' onto 'X by'."""
    return t.replace(" as recognized by", " by")


def severity(t):
    return SEVERITY.get(base(t), "?")


def category(t):
    if t == "Cited by":
        return "Cited by"
    if t.endswith(" as recognized by"):
        return "Related Reference"
    return "Direct History" if t in DIRECT_HISTORY else "Citing Reference"


def load_finals():
    """Flat most-negative export: one row per (citing, cited) pair with a
    final treatment. Keyed by cluster-id pair."""
    rows = csv.DictReader(open(f"{BENCH}/treatments_most_negative.csv"))
    return {
        (r["citing_case_cluster_id"], r["cited_case_cluster_id"]): r
        for r in rows if r["final_treatment"]
    }


def load_predictions():
    rows = csv.DictReader(open(f"{BENCH}/predictions.csv"))
    return {(r["citing_cluster_id"], r["cited_cluster_id"]): r["treatment"]
            for r in rows}


def expert_votes(r):
    return [r[f"expert_{i}_treatment"] for i in (1, 2, 3)
            if r[f"expert_{i}_treatment"]]


def leave_one_out(finals, keys=None):
    """Yield (key, held_out_vote, others_majority) for every expert vote on
    a pair with >= 2 votes, skipping ties among the other votes."""
    for k, r in finals.items():
        if keys is not None and k not in keys:
            continue
        v = expert_votes(r)
        if len(v) < 2:
            continue
        for i, h in enumerate(v):
            c = collections.Counter(v[:i] + v[i + 1:]).most_common()
            if len(c) > 1 and c[0][1] == c[1][1]:
                continue
            yield k, h, c[0][0]


def prf(pairs, key=lambda x: x):
    """Per-class (f1, precision, recall, support) for (pred, gold) pairs."""
    tp, fp, fn = collections.Counter(), collections.Counter(), collections.Counter()
    for p, g in pairs:
        p, g = key(p), key(g)
        if p == g:
            tp[g] += 1
        else:
            fp[p] += 1
            fn[g] += 1
    out = {}
    for c in set(tp) | set(fp) | set(fn):
        pr = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0
        rc = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else 0
        out[c] = (2 * pr * rc / (pr + rc) if pr + rc else 0, pr, rc, tp[c] + fn[c])
    return out


def macro_f1(f, min_support=20):
    cs = [c for c, v in f.items() if v[3] >= min_support]
    return sum(f[c][0] for c in cs) / len(cs), len(cs)


def norm_ws(s):
    s = (s.replace("’", "'").replace("“", '"').replace("”", '"')
         .replace("—", "-").replace("–", "-"))
    return re.sub(r"\s+", " ", s)


def spearman(a, b):
    def rank(x):
        s = sorted(range(len(x)), key=lambda i: x[i])
        r = [0] * len(x)
        i = 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[s[j + 1]] == x[s[i]]:
                j += 1
            for k in range(i, j + 1):
                r[s[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den
