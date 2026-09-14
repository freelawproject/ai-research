"""How often does an Id. follow a citation that sits inside a (citing …)/(quoting …)
parenthetical, and what does eyecite do with it? (eyecite issues #348 / #349)

Run from the citation-tagger env (it has eyecite):

    cd flp/citation-tagger && uv run python <triage>/id_paren_prevalence.py [--write-flags]

Broad pattern: the citation right before the Id. sits inside a parenthetical that opens
with a citation signal (citing, quoting, see, cf., accord, …) and closes before the Id.
Strict ("tight") subset: the signal is citing/quoting, the outer antecedent is a case cite,
and the Id. is within 800 chars of the parenthetical.

Writes per-opinion counts to data/id_paren_prevalence.csv. With --write-flags it also merges
`id_paren: {n, n_tight, hits[], run, eyecite, reviewed}` into each cluster's grouping
override so the annotator shows a badge, a Records filter and a "Suspect Id. resolutions"
panel (a `reviewed` flag already set by the annotator is preserved).
"""
import argparse
import collections
import csv
import datetime
import importlib.metadata
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tagged_text as tt  # noqa: E402
from eyecite import get_citations, resolve_citations  # noqa: E402
from eyecite.models import FullCaseCitation, IdCitation, ShortCaseCitation, SupraCitation  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ANNOT = os.path.join(ROOT, "data", "annotator")
OVERRIDES = os.path.join(ANNOT, "data", "grouping_overrides")
SIGNAL = re.compile(r"^\s*(citing|quoting|see(?:\s+also|,\s+e\.g\.)?|cf\.|compare|e\.g\.|accord|discussing|"
                    r"overruling|following|relying\s+on|adopting|applying|collecting|construing|as amended|"
                    r"en banc|per curiam)", re.I)
TIGHT = re.compile(r"^\s*(citing|quoting)\b", re.I)
CASE_TYPES = (FullCaseCitation, ShortCaseCitation, SupraCitation, IdCitation)
TIGHT_MAX_GAP = 800


def enclosing_paren(text, pos):
    """Index of the nearest unmatched '(' before pos, else -1."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                return i
            depth -= 1
    return -1


def closing_paren(text, open_pos):
    depth = 0
    for j in range(open_pos, min(len(text), open_pos + 3000)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def in_signal_paren(text, cite):
    """(open, close, signal_text) of a signal parenthetical enclosing the citation, else None."""
    p = enclosing_paren(text, cite.span()[0])
    if p < 0:
        return None
    m = SIGNAL.match(text[p + 1:p + 60])
    if not m:
        return None
    q = closing_paren(text, p)
    return (p, q, m.group(1)) if q > 0 else None


def scan(text):
    """[(kind, tight, id_cite, prev_cite)] for every Id. whose preceding citation is inside a
    signal parenthetical that closes before the Id."""
    cites = sorted((c for c in get_citations(text) if isinstance(c, CASE_TYPES)), key=lambda c: c.span()[0])
    res_of = {}
    for r, members in resolve_citations(cites).items():
        for m in members:
            res_of[id(m)] = r
    hits, n_id, n_unresolved = [], 0, 0
    for k, c in enumerate(cites):
        if not isinstance(c, IdCitation):
            continue
        n_id += 1
        n_unresolved += id(c) not in res_of
        if k == 0:
            continue
        prev = cites[k - 1]
        par = in_signal_paren(text, prev)
        if not par or par[1] > c.span()[0]:
            continue
        exp = None  # the citation the parenthetical modifies
        for j in range(k - 2, -1, -1):
            cj = cites[j]
            if cj.span()[1] <= par[0]:
                pj = in_signal_paren(text, cj)
                if pj and pj[1] < par[0]:
                    continue
                exp = cj
                break
        r_id, r_prev = res_of.get(id(c)), res_of.get(id(prev))
        r_exp = res_of.get(id(exp)) if exp else None
        if r_id is None:
            kind = "dropped"
        elif r_exp is not None and r_id is r_exp:
            kind = "ok"
        elif r_prev is not None and r_id is r_prev:
            kind = "to_inner"
        else:
            kind = "other"
        tight = bool(TIGHT.match(par[2]) and isinstance(exp, (FullCaseCitation, ShortCaseCitation))
                     and c.span()[0] - par[1] <= TIGHT_MAX_GAP)
        hits.append((kind, tight, c, prev))
    return cites, hits, n_id, n_unresolved


def snippet(text, c, before=160, after=40):
    s, e = c.span()
    b = re.sub(r"\s+", " ", text[max(0, s - before):s])
    a = re.sub(r"\s+", " ", text[e:e + after])
    needle = re.sub(r"\s+", " ", text[max(0, s - 70):e]).strip()
    return b, text[s:e], a, needle


def write_flag(cid, payload):
    path = os.path.join(OVERRIDES, f"{cid}.json")
    ov = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    prev = ov.get("id_paren") or {}
    payload["reviewed"] = bool(prev.get("reviewed")) if payload["n"] else False
    if ov.get("id_paren") == payload:
        return False
    ov["id_paren"] = payload
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ov, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-flags", action="store_true", help="merge id_paren into each cluster's grouping override")
    ap.add_argument("--ids", nargs="*", help="only these clusters")
    a = ap.parse_args()
    splits = {r["cluster_id"]: r["split"] for r in csv.DictReader(open(os.path.join(ROOT, "inputs", "splits.csv")))}
    meta = list(csv.DictReader(open(os.path.join(ANNOT, "overrides", "citing_metadata.csv"), encoding="utf-8")))
    cids = [r["cluster_id"] for r in meta if splits.get(r["cluster_id"]) != "unused"]
    if a.ids:
        cids = [c for c in cids if c in set(a.ids)]
    ver = importlib.metadata.version("eyecite")
    today = datetime.date.today().isoformat()
    tot, rows, n_written = collections.Counter(), [], 0
    for cid in cids:
        path = os.path.join(ANNOT, "data", "opinion_html", f"{cid}.json")
        if not os.path.exists(path):
            tot["no_html"] += 1
            continue
        tagged, _ = tt.from_opinion_html(json.load(open(path, encoding="utf-8")))
        text, _ = tt.strip_tags(tagged)
        _, hits, n_id, n_unres = scan(text)
        kinds = collections.Counter(k for k, _, _, _ in hits)
        n_tight = sum(1 for _, t, _, _ in hits if t)
        tot["ops"] += 1
        tot["id"] += n_id
        tot["id_unresolved"] += n_unres
        tot["hit"] += len(hits)
        tot["hit_tight"] += n_tight
        for k, n in kinds.items():
            tot[f"hit_{k}"] += n
        tot["ops_with_hit"] += bool(hits)
        rows.append([cid, splits.get(cid, "train"), n_id, len(hits), n_tight, kinds["to_inner"], kinds["dropped"], kinds["ok"]])
        if a.write_flags:
            out = []
            for kind, tight, c, prev in hits:
                b, idt, af, needle = snippet(text, c)
                out.append({"kind": kind, "tight": tight, "before": b, "id_text": idt, "after": af,
                            "needle": needle, "prev_cite": prev.matched_text()})
            n_written += write_flag(cid, {"n": len(hits), "n_tight": n_tight, "hits": out, "run": today, "eyecite": ver})
    with open(os.path.join(ROOT, "data", "id_paren_prevalence.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_id", "split", "n_id", "n_hits", "n_tight", "n_to_inner", "n_dropped", "n_ok"])
        w.writerows(rows)
    print(json.dumps(tot, indent=1))
    by = collections.defaultdict(collections.Counter)
    for cid, sp, n_id, n_h, n_t, *_ in rows:
        b = by[sp]
        b["ops"] += 1
        b["id"] += n_id
        b["hits"] += n_h
        b["tight"] += n_t
        b["ops_hit"] += n_h > 0
    print({k: dict(v) for k, v in by.items()})
    if a.write_flags:
        print(f"flags written/updated for {n_written} clusters (eyecite {ver})")


if __name__ == "__main__":
    main()
