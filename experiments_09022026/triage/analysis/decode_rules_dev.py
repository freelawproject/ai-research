"""Decode rules on top of the encoder's end-to-end clusters, scored on dev
with the seeding scorer (mention + pairwise coref P/R/F vs annotator gold).

The learned linker is level with eyecite on held-out coref because eyecite's
precision comes from mechanical keying. This measures, without any training,
how much of that keying can be bolted onto the encoder's output as
merge-only rules (like the `Id.` rule already is):

  reporter   two full reporter cites with the same volume / reporter / first
             page -> one cluster (exact keying, no eyecite involved)
  shortform  `805 F.2d at 1124` -> the unique full cite in the case with that
             volume + reporter (skipped when ambiguous)
  eyecite    an encoder mention that overlaps an eyecite span inherits eyecite's
             group id (href cluster / reporter path / data-id); mentions with the
             same eyecite group -> one cluster. `eyecite_full` restricts this to
             eyecite spans that are full reporter cites (eyecite's *resolution*,
             which also joins parallel cites), leaving Id./supra/short forms to
             the model; `eyecite_all` also imports eyecite's short-form and Id.
             resolution — the part the gold corrected most.

Rules only merge, never split, so mention scores are unchanged and coref
precision can only fall while recall rises; the table shows which rules pay.
"harmful merges" counts rule unions that joined two encoder clusters whose
gold-matched mentions sit in different gold groups.

    python3 analysis/decode_rules_dev.py \\
        [--pred ../../experiments_09092026/data/output/pred_gold_dev_r4_rule.json] [--ids data/citation_seed/dev_ids.txt]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, HERE)
from citation_seed import OUT, State, _match, prf, score_states  # noqa: E402
from score_encoder import EncoderState  # noqa: E402

EXP0909 = os.path.join(os.path.dirname(os.path.dirname(ROOT)), "experiments_09092026")
DEFAULT_PRED = os.path.join(EXP0909, "data", "output", "pred_gold_dev_r4_rule.json")
DEFAULT_IDS = os.path.join(OUT, "dev_ids.txt")

RE_ID = re.compile(r"^\s*(id\.|id\b|ibid)", re.I)
# "805 F.2d 1117", "805 F. 2d 1117, 1124", "259 U.S. 214" -> (vol, reporter, page)
RE_FULL = re.compile(r"^\s*(\d+)\s+([A-Z][A-Za-z0-9.'’ ]*?)\s+(\d+)(?![\dA-Za-z])")
# "805 F.2d at 1124" -> (vol, reporter)
RE_SHORT = re.compile(r"^\s*(\d+)\s+([A-Z][A-Za-z0-9.'’ ]*?)\s+at\s+\d+", re.I)


def norm_rep(r):
    return re.sub(r"[.\s’']", "", r).lower()


def full_key(text):
    m = RE_FULL.match(text)
    if not m or RE_ID.match(text) or "supra" in text.lower():
        return None
    if m.group(2).lower().strip() == "at":
        return None
    return (m.group(1), norm_rep(m.group(2)), m.group(3))


def short_key(text):
    m = RE_SHORT.match(text)
    return (m.group(1), norm_rep(m.group(2))) if m else None


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[ra] = rb
        return True


class Ruled:
    """State look-alike over the encoder's mentions with union-find clusters."""

    def __init__(self, enc_mentions):
        self.ms = list(enc_mentions)
        self.uf = UF(len(self.ms))
        base = defaultdict(list)
        for i, m in enumerate(self.ms):
            base[m[3]].append(i)
        for idx in base.values():
            for j in idx[1:]:
                self.uf.union(idx[0], j)

    def mentions(self):
        return [(m[0], m[1], m[2], f"u{self.uf.find(i)}", m[4], m[5]) for i, m in enumerate(self.ms)]


def eyecite_gid_of(enc_ms, seed, full_only):
    """encoder mention index -> eyecite seed group id (overlap within a writing)."""
    occs = [o for o in seed.mentions()]                          # (op, s, e, gid, kind, key)
    if full_only:
        occs = [o for o in occs if full_key(seed.texts[o[0]][o[1]:o[2]]) is not None]
    pairs = _match(enc_ms, occs)
    return {ie: occs[io][3] for ie, io in pairs}


def apply_rules(r, seed, rules, gold_gid):
    """Merge by rule; return counts of unions and harmful unions."""
    c = Counter()
    ms = r.ms

    root_gold = defaultdict(set)                 # uf root -> gold gids of its mentions
    for k, g in gold_gid.items():
        root_gold[r.uf.find(k)].add(g)

    def union(i, j, tag):
        ri, rj = r.uf.find(i), r.uf.find(j)
        if ri == rj:
            return
        gi, gj = root_gold.get(ri, set()), root_gold.get(rj, set())
        r.uf.union(i, j)
        root_gold[r.uf.find(i)] = gi | gj
        c[f"{tag}_unions"] += 1
        if gi and gj and not (gi & gj):
            c[f"{tag}_harmful"] += 1
            HARMFUL.append({"rule": tag, "cid": seed.cid, "a": seed.texts[ms[i][0]][ms[i][1]:ms[i][2]],
                            "b": seed.texts[ms[j][0]][ms[j][1]:ms[j][2]], "gold_a": sorted(gi), "gold_b": sorted(gj),
                            "ctx_a": seed.texts[ms[i][0]][max(0, ms[i][1] - 80):ms[i][2] + 40],
                            "ctx_b": seed.texts[ms[j][0]][max(0, ms[j][1] - 80):ms[j][2] + 40]})

    if "reporter" in rules or "shortform" in rules:
        by_key = defaultdict(list)
        for i, m in enumerate(ms):
            k = full_key(seed.texts[m[0]][m[1]:m[2]])
            if k:
                by_key[k].append(i)
        if "reporter" in rules:
            for idx in by_key.values():
                for j in idx[1:]:
                    union(idx[0], j, "reporter")
        if "shortform" in rules:
            by_vr = defaultdict(set)
            for (v, rep, _pg), idx in by_key.items():
                by_vr[(v, rep)].update(r.uf.find(i) for i in idx)
            for i, m in enumerate(ms):
                k = short_key(seed.texts[m[0]][m[1]:m[2]])
                if not k or k not in by_vr:
                    continue
                roots = {r.uf.find(x) for x in by_vr[k]}
                if len(roots) == 1:
                    union(i, next(iter(roots)), "shortform")
                else:
                    c["shortform_ambiguous"] += 1
    for tag, full_only in (("eyecite_full", True), ("eyecite_all", False)):
        if tag not in rules:
            continue
        gid = eyecite_gid_of(ms, seed, full_only)
        by_g = defaultdict(list)
        for i, g in gid.items():
            by_g[g].append(i)
        for idx in by_g.values():
            for j in idx[1:]:
                union(idx[0], j, tag)
    return c


HARMFUL = []

VARIANTS = [
    ("encoder r4 + Id. rule (as is)", ()),
    ("+ reporter key", ("reporter",)),
    ("+ reporter key + short form", ("reporter", "shortform")),
    ("+ eyecite groups (full cites only)", ("eyecite_full",)),
    ("+ reporter key + eyecite (full)", ("reporter", "eyecite_full")),
    ("+ reporter + short form + eyecite (full)", ("reporter", "shortform", "eyecite_full")),
    ("+ eyecite groups (all spans, incl. Id./supra)", ("eyecite_all",)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=DEFAULT_PRED)
    ap.add_argument("--ids", default=DEFAULT_IDS)
    ap.add_argument("--out", default=os.path.join(OUT, "eval", "decode_rules_dev.json"))
    a = ap.parse_args()
    pred = json.load(open(a.pred, encoding="utf-8"))
    ids = [l.strip() for l in open(a.ids) if l.strip()]
    rows = {name: Counter() for name, _ in VARIANTS}
    eye = Counter()
    n = 0
    for cid in ids:
        if cid not in pred:
            print(f"{cid}: no predictions, skipped")
            continue
        n += 1
        gold = State(cid, False)
        seed = State(cid, True)
        enc = EncoderState(cid, pred[cid], gold)
        for k, v in score_states(seed, gold)["_raw"].items():
            eye[k] += v
        enc_ms = enc.mentions()
        gold_ms = gold.mentions()
        gold_gid = {ie: gold_ms[ig][3] for ie, ig in _match(enc_ms, gold_ms)}
        for name, rules in VARIANTS:
            r = Ruled(enc_ms)
            c = apply_rules(r, seed, rules, gold_gid)
            rows[name].update(c)
            for k, v in score_states(r, gold)["_raw"].items():
                rows[name][k] += v

    def fmt(c):
        m = prf(c["m_tp"], c["m_sys"], c["m_gold"])
        co = prf(c["c_tp"], c["c_tp"] + c["c_fp"], c["c_tp"] + c["c_fn"])
        return m, co

    print(f"== {n} dev opinions, encoder = {os.path.basename(a.pred)}\n")
    print(f"{'variant':48} {'mention P/R/F':26} {'coref P/R/F':26} unions (harmful)")
    m, co = fmt(eye)
    print(f"{'eyecite (untouched seed)':48} {str(m):26} {str(co):26}")
    out = {"eyecite": {"mention": m, "coref": co}}
    for name, rules in VARIANTS:
        c = rows[name]
        m, co = fmt(c)
        u = "  ".join(f"{t}={c[t + '_unions']}({c[t + '_harmful']})" for t in
                      ("reporter", "shortform", "eyecite_full", "eyecite_all") if t in rules)
        if c["shortform_ambiguous"]:
            u += f"  shortform_ambiguous={c['shortform_ambiguous']}"
        print(f"{name:48} {str(m):26} {str(co):26} {u}")
        out[name] = {"mention": m, "coref": co, "rules": rules,
                     "counts": {k: v for k, v in c.items() if k.endswith(("_unions", "_harmful", "_ambiguous"))}}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    out["harmful_unions"] = HARMFUL
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
