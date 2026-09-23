"""Dev-set comparison of every citation system against the annotator gold.

Four systems over the 49 triage dev opinions, all in the annotator frame and
all scored with the one scorer (`citation_seed.score_states`):

    eyecite   State(cid, seed_state=True)                    stored CourtListener markup
    eyecite_txt  eyecite run here on the plain text          same library, no stored markup
    gpt       seed + data/citation_seed/dev_gpt_v8g_high      prompt v8g + candidates, high effort
    opus      seed + data/citation_seed/dev_opus_v5           prompt v5
    enc       experiments_09092026 pred_gold_dev_r4_rule.json round-4 encoder + `Id.` decode rule

Per opinion every gold mention becomes a *locus*, plus one locus per run of
overlapping mentions no system matched to gold. At each locus each system gets
a verdict:

    match   the system tagged the gold mention        (group_ok says whether its
                                                       coreference matches gold)
    miss    gold tagged it, the system did not
    fp      the system tagged it, gold did not
    none    neither

`group_ok` compares partitions the way the pairwise scorer does: over the gold
mentions THIS system matched, the set grouped with the locus under the system
must equal the set grouped with it under gold.

    python3 compare.py                    # pooled table, reproduces model_comparison_dev.md
    python3 compare.py --cid 101625       # one opinion, locus by locus
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import io
import json
import os
import re
import sys

from eyecite import get_citations, resolve_citations
from eyecite.models import UnknownCitation

try:
    EYECITE_VERSION = importlib.metadata.version("eyecite")
except Exception:  # not installed as a distribution
    EYECITE_VERSION = "unknown"

HERE = os.path.dirname(os.path.abspath(__file__))
TRIAGE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(TRIAGE, "lib"))
sys.path.insert(0, os.path.join(TRIAGE, "analysis"))
from citation_seed import (  # noqa: E402
    OUT,
    State,
    _match,
    apply_edits,
    load_json,
    meta_of,
    parse_edits,
    prf,
    score_states,
)
from disagreement_dev import kind  # noqa: E402
from score_encoder import EncoderState  # noqa: E402

EXP0909 = os.path.join(os.path.dirname(os.path.dirname(TRIAGE)), "experiments_09092026")
DEFAULT_PRED = os.environ.get(
    "DEVCMP_PRED", os.path.join(EXP0909, "data", "output", "pred_gold_dev_r4_rule.json"))
IDS_PATH = os.environ.get("DEVCMP_IDS", os.path.join(OUT, "dev_ids.txt"))

# key -> (label, short, css class, source). Order is the render/column order.
SYSTEMS = {
    "eyecite": ("eyecite, stored CL markup", "eyecite/CL", "s-eye", ("seed", None)),
    "eyecite_txt": (f"eyecite {EYECITE_VERSION} re-run on plain text", "eyecite/txt", "s-eyetxt", ("fresh", None)),
    "gpt": ("GPT-5.6 v8g high", "GPT", "s-gpt", ("edits", "dev_gpt_v8g_high")),
    "opus": ("Opus 5 v5", "Opus", "s-opus", ("edits", "dev_opus_v5")),
    "enc": ("encoder r4 + Id. rule", "encoder", "s-enc", ("encoder", None)),
}
# opt-in extra rows (same shape, measured in model_comparison_dev.md)
EXTRA = {
    "sonnet": ("Sonnet 4.6 v5", "Sonnet", "s-sonnet", ("edits", "dev_sonnet46")),
    "kimi": ("Kimi K2.5 v5", "Kimi", "s-kimi", ("edits", "dev_kimi")),
    "gpt_v5": ("GPT-5.6 v5 medium", "GPT v5", "s-gptv5", ("edits", "dev_gpt")),
}
for _k in (os.environ.get("DEVCMP_EXTRA") or "").replace(" ", "").split(","):
    if _k in EXTRA:
        SYSTEMS[_k] = EXTRA[_k]

def dev_ids():
    return open(IDS_PATH, encoding="utf-8").read().split()


def _pred(path=None):
    return load_json(path or DEFAULT_PRED)


def build_states(cid, gold, pred):
    """{key: state} for every system that produced output for `cid`; plus {key: error}."""
    states, errors = {}, {}
    for key, (_label, _short, _css, (src, arg)) in SYSTEMS.items():
        try:
            if src == "seed":
                states[key] = State(cid, True)
            elif src == "fresh":
                states[key] = EyeciteFreshState(cid, gold)
            elif src == "edits":
                path = os.path.join(OUT, arg, f"{cid}.edits.json")
                if not os.path.exists(path):
                    errors[key] = "no output"
                    continue
                st = State(cid, True)
                mp = load_json(os.path.join(OUT, "inputs", f"{cid}.map.json"))
                apply_edits(st, parse_edits(path), mp)
                states[key] = st
            else:
                if str(cid) not in pred:
                    errors[key] = "no prediction"
                    continue
                states[key] = EncoderState(cid, pred[str(cid)], gold)
        except Exception as exc:  # bad json, unresolvable edit, …
            errors[key] = f"{type(exc).__name__}: {exc}"
    return states, errors


class EyeciteFreshState:
    """eyecite run here on the plain opinion text, ignoring the stored markup.

    The `eyecite` row scores CourtListener's stored `html_with_citations` spans,
    which were written at ingest by whatever version ran then and only where the
    annotator could resolve a span. This row asks the separate question: what
    does the library find when pointed at the same text today. Grouping comes
    from `resolve_citations`; a citation that resolves to nothing becomes its own
    group, so it still counts as a found mention.
    """

    def __init__(self, cid, gold):
        self.cid = str(cid)
        self._mentions = []
        self.names = {}
        self.unanchored = 0
        n = 0
        for oid, text in gold.texts.items():
            with contextlib.redirect_stdout(io.StringIO()):  # eyecite prints overlap diagnostics
                cites = get_citations(text)
                res = resolve_citations(cites)
            gid_of = {}
            for members in res.values():
                n += 1
                for c in members:
                    gid_of[id(c)] = f"e{n}"
            for c in cites:
                if isinstance(c, UnknownCitation):  # bare "§" tokens, never a case mention
                    continue
                s, e = c.span()
                g = gid_of.get(id(c))
                if g is None:
                    n += 1
                    g = f"e{n}"
                self._mentions.append((oid, s, e, g, "occ", f"{oid}:{s}"))

    def mentions(self):
        return self._mentions


def _partition(idxs, gid_of):
    """{idx: frozenset of idxs sharing its group}, over `idxs` only."""
    by = {}
    for i in idxs:
        by.setdefault(gid_of(i), []).append(i)
    return {i: frozenset(members) for members in by.values() for i in members}


def _snip(text, s, e, w=90):
    left = re.sub(r"\s+", " ", text[max(0, s - w):s])
    mid = re.sub(r"\s+", " ", text[s:e])
    right = re.sub(r"\s+", " ", text[e:e + w])
    return f"…{left}⟦{mid}⟧{right}…"


def compare_opinion(cid, pred=None, gold=None):
    """Full comparison payload for one opinion (also what the API returns)."""
    pred = pred if pred is not None else _pred()
    gold = gold or State(cid, seed_state=False)
    states, errors = build_states(cid, gold, pred)
    texts = gold.texts
    mg = gold.mentions()

    per = {}
    for key, st in states.items():
        ms = st.mentions()
        pairs = _match(ms, mg)
        g2s = {gi: si for si, gi in pairs}
        matched_sys = {si for si, _ in pairs}
        matched = sorted(g2s)
        sys_part = _partition(matched, lambda gi: ms[g2s[gi]][3])
        gold_part = _partition(matched, lambda gi: mg[gi][3])
        per[key] = {
            "ms": ms,
            "g2s": g2s,
            "matched": set(matched),
            "unmatched": [si for si in range(len(ms)) if si not in matched_sys],
            "sys_part": sys_part,
            "gold_part": gold_part,
            "score": score_states(st, gold),
            "names": dict(getattr(st, "names", {}) or {}),
            "unanchored": getattr(st, "unanchored", 0),
        }

    def sys_cell(key, gi):
        p = per[key]
        if gi not in p["matched"]:
            return {"v": "miss"}
        si = p["g2s"][gi]
        gid = p["ms"][si][3]
        ok = p["sys_part"][gi] == p["gold_part"][gi]
        return {"v": "match", "gid": gid, "group_ok": ok,
                "n_mates": len(p["sys_part"][gi]) - 1,
                "n_mates_gold": len(p["gold_part"][gi]) - 1}

    loci = []
    for gi, m in enumerate(mg):
        oid, s, e, gid = m[0], m[1], m[2], m[3]
        text = re.sub(r"\s+", " ", texts[oid][s:e])
        cells = {k: sys_cell(k, gi) for k in per}
        loci.append({"op": oid, "s": s, "e": e, "text": text, "kind": kind(text),
                     "ctx": _snip(texts[oid], s, e),
                     "gold": {"tagged": True, "gid": gid, "name": gold.names.get(gid, "")},
                     "sys": cells})

    # false positives: runs of overlapping unmatched system mentions share one locus
    fps = []
    for key, p in per.items():
        for si in p["unmatched"]:
            m = p["ms"][si]
            fps.append((m[0], m[1], m[2], key, m[3]))
    fps.sort(key=lambda x: (x[0], x[1], x[2]))
    runs = []
    for oid, s, e, key, gid in fps:
        if runs and runs[-1]["op"] == oid and s < runs[-1]["e"]:
            runs[-1]["e"] = max(runs[-1]["e"], e)
            runs[-1]["who"][key] = gid
        else:
            runs.append({"op": oid, "s": s, "e": e, "who": {key: gid}})
    for r in runs:
        text = re.sub(r"\s+", " ", texts[r["op"]][r["s"]:r["e"]])
        cells = {}
        for k in per:
            cells[k] = ({"v": "fp", "gid": r["who"][k]} if k in r["who"] else {"v": "none"})
        loci.append({"op": r["op"], "s": r["s"], "e": r["e"], "text": text, "kind": kind(text),
                     "ctx": _snip(texts[r["op"]], r["s"], r["e"]),
                     "gold": {"tagged": False}, "sys": cells})

    loci.sort(key=lambda x: (x["op"], x["s"]))
    for i, lo in enumerate(loci):
        lo["id"] = i
        flags = set()
        for k, c in lo["sys"].items():
            if c["v"] == "miss":
                flags.add("miss")
            elif c["v"] == "fp":
                flags.add("fp")
            elif c["v"] == "match" and not c["group_ok"]:
                flags.add("group")
        lo["flags"] = sorted(flags)
        lo["n_wrong"] = sum(1 for c in lo["sys"].values()
                            if c["v"] in ("miss", "fp") or (c["v"] == "match" and not c["group_ok"]))
        lo["clean"] = not lo["flags"]

    counts = {}
    for key in per:
        c = {"miss": 0, "fp": 0, "group": 0}
        for lo in loci:
            cell = lo["sys"][key]
            if cell["v"] == "miss":
                c["miss"] += 1
            elif cell["v"] == "fp":
                c["fp"] += 1
            elif cell["v"] == "match" and not cell["group_ok"]:
                c["group"] += 1
        c["wrong"] = c["miss"] + c["fp"] + c["group"]
        c["n_mentions"] = len(per[key]["ms"])
        c["unanchored"] = per[key]["unanchored"]
        c["mention_PRF"] = per[key]["score"]["mention_PRF"]
        c["coref_PRF"] = per[key]["score"]["coref_pairwise_PRF"]
        c["_raw"] = per[key]["score"]["_raw"]
        counts[key] = c

    return {
        "cid": str(cid),
        "meta": {k: (meta_of(cid).get(k) or "") for k in ("case_name", "court", "year")},
        "ops": [{"op_id": str(o["id"]), "type": gold.op_type[str(o["id"])], "text": texts[str(o["id"])]}
                for o in gold.opinions],
        "systems": {k: {"label": SYSTEMS[k][0], "short": SYSTEMS[k][1], "css": SYSTEMS[k][2]} for k in SYSTEMS},
        "order": [k for k in SYSTEMS if k in per],
        "errors": errors,
        "mentions": dict(
            {"gold": [{"op": m[0], "s": m[1], "e": m[2], "gid": m[3]} for m in mg]},
            **{k: [{"op": m[0], "s": m[1], "e": m[2], "gid": m[3]} for m in per[k]["ms"]] for k in per}),
        "names": dict({"gold": gold.names}, **{k: per[k]["names"] for k in per}),
        "n_gold": len(mg),
        "loci": loci,
        "counts": counts,
    }


def summarize(cids=None, pred=None, quiet=True, cache=None):
    """Per-opinion rows + pooled per-system scores over the dev set.

    `cache` (a dict) is filled with the full per-opinion payloads, so a caller
    that needs both (the viewer) compares each opinion exactly once.
    """
    cids = cids or dev_ids()
    pred = pred if pred is not None else _pred()
    rows, pooled = [], {k: {"m_tp": 0, "m_sys": 0, "m_gold": 0, "c_tp": 0, "c_fp": 0, "c_fn": 0,
                            "miss": 0, "fp": 0, "group": 0, "unanchored": 0} for k in SYSTEMS}
    tot_gold = 0
    for cid in cids:
        d = compare_opinion(cid, pred)
        if cache is not None:
            cache[d["cid"]] = d
        n_diff = sum(1 for lo in d["loci"] if not lo["clean"])
        rows.append({"cid": d["cid"], "meta": d["meta"], "n_gold": d["n_gold"],
                     "chars": sum(len(o["text"]) for o in d["ops"]),
                     "n_loci": len(d["loci"]), "n_diff": n_diff,
                     "counts": {k: {kk: vv for kk, vv in v.items() if kk != "_raw"}
                                for k, v in d["counts"].items()},
                     "errors": d["errors"]})
        tot_gold += d["n_gold"]
        for k, c in d["counts"].items():
            for kk in ("m_tp", "m_sys", "m_gold", "c_tp", "c_fp", "c_fn"):
                pooled[k][kk] += c["_raw"][kk]
            for kk in ("miss", "fp", "group", "unanchored"):
                pooled[k][kk] += c[kk]
        if not quiet:
            print(f"{d['cid']:>9}  gold {d['n_gold']:>4}  diff loci {n_diff:>4}  " +
                  "  ".join(f"{SYSTEMS[k][1]} {d['counts'][k]['wrong']:>3}" for k in d["order"]))
    table = []
    for k, r in pooled.items():
        if not r["m_gold"]:
            continue
        table.append({"key": k, "label": SYSTEMS[k][0], "short": SYSTEMS[k][1], "css": SYSTEMS[k][2],
                      "mention_PRF": prf(r["m_tp"], r["m_sys"], r["m_gold"]),
                      "coref_PRF": prf(r["c_tp"], r["c_tp"] + r["c_fp"], r["c_tp"] + r["c_fn"]),
                      "n_mentions": r["m_sys"], "miss": r["miss"], "fp": r["fp"], "group": r["group"],
                      "unanchored": r["unanchored"], "wrong": r["miss"] + r["fp"] + r["group"]})
    return rows, table, {"n_opinions": len(rows), "n_gold": tot_gold,
                         "n_loci": sum(r["n_loci"] for r in rows),
                         "n_diff": sum(r["n_diff"] for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", help="one opinion, locus by locus")
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--pred", default=DEFAULT_PRED)
    ap.add_argument("--json", action="store_true", help="dump the payload instead of a table")
    a = ap.parse_args()
    pred = _pred(a.pred)
    if a.cid:
        d = compare_opinion(a.cid, pred)
        if a.json:
            print(json.dumps(d, indent=1, ensure_ascii=False, default=str))
            return
        print(f"== {d['cid']} {d['meta']['case_name']} — {d['n_gold']} gold mentions, {len(d['loci'])} loci")
        for lo in d["loci"]:
            if lo["clean"]:
                continue
            cells = " ".join(
                f"{d['systems'][k]['short']}:" +
                (lo["sys"][k]["v"] if lo["sys"][k]["v"] != "match"
                 else ("ok" if lo["sys"][k]["group_ok"] else "GROUP"))
                for k in d["order"])
            print(f"  [{lo['kind']:>8}] {lo['text'][:56]!r:60} {cells}")
        for k in d["order"]:
            c = d["counts"][k]
            print(f"  {d['systems'][k]['label']:26} mention {c['mention_PRF']} coref {c['coref_PRF']} "
                  f"miss {c['miss']} fp {c['fp']} group {c['group']}")
        return
    rows, table, tot = summarize(a.ids, pred, quiet=False)
    print(f"\n== POOLED over {tot['n_opinions']} dev opinions ({tot['n_gold']:,} gold mentions)")
    print(f"  {'system':28} {'mention P/R/F1':22} {'coref P/R/F1':22} {'miss':>6} {'fp':>6} {'group':>6}")
    for r in table:
        print(f"  {r['label']:28} {str(r['mention_PRF']):22} {str(r['coref_PRF']):22} "
              f"{r['miss']:>6} {r['fp']:>6} {r['group']:>6}"
              + (f"  ({r['unanchored']} unanchored)" if r["unanchored"] else ""))


if __name__ == "__main__":
    main()
