"""Three-way study on dev: GPT-seeded labels vs the encoder vs gold.

Question (idea for round 3 of seeding): if only the places where the encoder
DISAGREES with the eyecite+GPT label are sent to Opus for adjudication, how
much of GPT's labeling error would that catch, and how much Opus work is it?

For every dev opinion three mention sets are built in the annotator frame:
  gpt  = eyecite seed state + the GPT v8g edits (what round 2 trained on)
  enc  = the production encoder's end-to-end prediction (pred_gold_dev_*.json,
         re-anchored by score_encoder.EncoderState)
  gold = the annotator's verified state
and matched pairwise with the scorer's greedy overlap matcher (_match).

Mention level — every GPT mention is agree/gpt-only, every gold mention the
GPT pass missed is flagged iff the encoder tagged it. Coref level — over the
mentions all three systems share, a pair is flagged iff GPT and the encoder
disagree on whether it is coreferent; GPT is wrong iff it disagrees with gold.
"Flag recall" = share of GPT's errors that a disagreement points at. "Yield" =
share of flagged items where GPT (not the encoder) is the wrong one, i.e. the
share of Opus adjudications that would change a label. The oracle row scores
GPT's labels with every flagged-and-wrong item corrected — the ceiling of
the idea with a perfect adjudicator.

Caveat printed with the result: the encoder never saw dev, but it was trained
ON the round-2 GPT labels, so on the training set itself disagreement will be
rarer than here (it partly memorised the labels). Dev is the optimistic bound;
a cross-fitted encoder (train on half, predict the other half) is the honest
version for train.

    python3 analysis/disagreement_dev.py \\
        --pred ../../experiments_09092026/data/output/pred_gold_dev_r4_rule.json \\
        [--gpt-dir data/citation_seed/dev_gpt_v8g_high] [--ctx 400] [--out eval/…json]
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
from citation_seed import OUT, State, _match, apply_edits, load_json, parse_edits, prf  # noqa: E402
from score_encoder import EncoderState  # noqa: E402

EXP0909 = os.path.join(os.path.dirname(os.path.dirname(ROOT)), "experiments_09092026")
DEFAULT_PRED = os.path.join(EXP0909, "data", "output", "pred_gold_dev_r4_rule.json")
DEFAULT_GPT = os.path.join(OUT, "dev_gpt_v8g_high")
DEFAULT_IDS = os.path.join(OUT, "dev_ids.txt")


RE_ID = re.compile(r"^\s*(id\.|id\b|ibid)", re.I)
RE_REP = re.compile(r"^\s*\d+\s+[A-Z]")


def kind(text):
    t = text.strip()
    if RE_ID.match(t):
        return "id"
    if "supra" in t.lower():
        return "supra"
    if RE_REP.match(t) or re.search(r"\b\d+\s+[A-Z]", t):
        return "reporter"
    return "name"


def gpt_state(cid, gpt_dir):
    st = State(cid, True)
    mp = load_json(os.path.join(OUT, "inputs", f"{cid}.map.json"))
    apply_edits(st, parse_edits(os.path.join(gpt_dir, f"{cid}.edits.json")), mp)
    return st


def merge_windows(items, ctx):
    """[(op_id, s, e)] -> merged (op_id, s, e) windows padded by ctx chars."""
    by_op = defaultdict(list)
    for oid, s, e in items:
        by_op[oid].append((max(0, s - ctx), e + ctx))
    out = []
    for oid, spans in by_op.items():
        spans.sort()
        cur = list(spans[0])
        for s, e in spans[1:]:
            if s <= cur[1]:
                cur[1] = max(cur[1], e)
            else:
                out.append((oid, cur[0], cur[1]))
                cur = [s, e]
        out.append((oid, cur[0], cur[1]))
    return out


def one(cid, pred, gpt_dir, ctx):
    gold = State(cid, False)
    gpt = gpt_state(cid, gpt_dir)
    enc = EncoderState(cid, pred[str(cid)], gold)
    mg, ms_g, ms_e = gold.mentions(), gpt.mentions(), enc.mentions()
    g2gold = dict(_match(ms_g, mg))            # gpt idx -> gold idx
    e2gold = dict(_match(ms_e, mg))            # enc idx -> gold idx
    g2e = dict(_match(ms_g, ms_e))             # gpt idx -> enc idx
    e2g = {v: k for k, v in g2e.items()}
    gold2g = {v: k for k, v in g2gold.items()}
    gold2e = {v: k for k, v in e2gold.items()}

    c = Counter()
    flagged_spans = []                          # (op_id, s, e) of every disagreement
    # --- mention level
    for ig, m in enumerate(ms_g):
        right = ig in g2gold
        agree = ig in g2e
        c["gpt_mentions"] += 1
        k = kind(gold.texts[m[0]][m[1]:m[2]])
        if agree:
            c["m_agree_right" if right else "m_agree_wrong"] += 1
            if not right:
                c[f"kind_agree_wrong:{k}"] += 1
        else:
            c["m_gptonly_right" if right else "m_gptonly_wrong"] += 1   # wrong = GPT FP caught
            c[f"kind_gptonly_{'right' if right else 'wrong'}:{k}"] += 1
            flagged_spans.append((m[0], m[1], m[2]))
    for ie, m in enumerate(ms_e):
        if ie in e2g:
            continue
        right = ie in e2gold
        c["m_enconly_right" if right else "m_enconly_wrong"] += 1       # right = GPT FN caught
        c[f"kind_enconly_{'right' if right else 'wrong'}:{kind(gold.texts[m[0]][m[1]:m[2]])}"] += 1
        flagged_spans.append((m[0], m[1], m[2]))
    for igold in range(len(mg)):
        if igold not in gold2g:
            c["gpt_fn"] += 1
            if igold in gold2e:
                c["gpt_fn_flagged"] += 1
    c["gpt_fp"] = c["m_gptonly_wrong"] + c["m_agree_wrong"]
    c["gpt_fp_flagged"] = c["m_gptonly_wrong"]
    c["m_tp"], c["m_sys"], c["m_gold"] = len(g2gold), len(ms_g), len(mg)

    # --- coref level over the mentions gpt, enc and gold all share
    shared = [ig for ig in range(len(ms_g)) if ig in g2gold and ig in g2e]
    coref_flag_mentions = set()
    for a in range(len(shared)):
        for b in range(a + 1, len(shared)):
            ia, ib = shared[a], shared[b]
            same_g = ms_g[ia][3] == ms_g[ib][3]
            same_e = ms_e[g2e[ia]][3] == ms_e[g2e[ib]][3]
            same_gold = mg[g2gold[ia]][3] == mg[g2gold[ib]][3]
            flagged = same_g != same_e
            wrong = same_g != same_gold
            c["pairs"] += 1
            c["p_flag"] += flagged
            c["p_wrong"] += wrong
            c["p_flag_wrong"] += flagged and wrong
            c["p_flag_right"] += flagged and not wrong
            # the scorer's pairwise counts, for GPT as-is and for the oracle fix
            c["c_tp"] += same_g and same_gold
            c["c_fp"] += same_g and not same_gold
            c["c_fn"] += same_gold and not same_g
            fixed = same_gold if flagged else same_g
            c["o_tp"] += fixed and same_gold
            c["o_fp"] += fixed and not same_gold
            c["o_fn"] += same_gold and not fixed
            if flagged:
                coref_flag_mentions.update((ia, ib))
    for ig in coref_flag_mentions:
        m = ms_g[ig]
        flagged_spans.append((m[0], m[1], m[2]))
    c["coref_flag_mentions"] = len(coref_flag_mentions)
    # pairs the shared set leaves out (a mention only one side has) are counted
    # in the mention-level numbers; GPT's own pairwise score over gpt<->gold
    # matched mentions is what the seeding scorer reports.
    c["c_tp_full"] = c["c_fp_full"] = c["c_fn_full"] = 0
    gm = [ig for ig in range(len(ms_g)) if ig in g2gold]
    for a in range(len(gm)):
        for b in range(a + 1, len(gm)):
            ia, ib = gm[a], gm[b]
            same_g = ms_g[ia][3] == ms_g[ib][3]
            same_gold = mg[g2gold[ia]][3] == mg[g2gold[ib]][3]
            c["c_tp_full"] += same_g and same_gold
            c["c_fp_full"] += same_g and not same_gold
            c["c_fn_full"] += same_gold and not same_g

    wins = merge_windows(flagged_spans, ctx)
    c["windows"] = len(wins)
    c["window_chars"] = sum(e - s for _, s, e in wins)
    c["opinion_chars"] = sum(len(t) for t in gold.texts.values())
    c["has_disagreement"] = int(bool(flagged_spans))
    c["n_items"] = len(flagged_spans)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=DEFAULT_PRED)
    ap.add_argument("--gpt-dir", default=DEFAULT_GPT)
    ap.add_argument("--ids", default=DEFAULT_IDS, help="file of cluster ids (one per line)")
    ap.add_argument("--ctx", type=int, default=400, help="chars of context either side of a flagged span")
    ap.add_argument("--out", default=os.path.join(OUT, "eval", "disagreement_dev.json"))
    a = ap.parse_args()
    pred = json.load(open(a.pred, encoding="utf-8"))
    ids = [l.strip() for l in open(a.ids) if l.strip()]
    tot, per = Counter(), {}
    for cid in ids:
        if cid not in pred or not os.path.exists(os.path.join(a.gpt_dir, f"{cid}.edits.json")):
            print(f"{cid}: missing pred or GPT edits, skipped")
            continue
        c = one(cid, pred, a.gpt_dir, a.ctx)
        per[cid] = dict(c)
        tot.update(c)
    n = len(per)

    def pct(x, y):
        return f"{x}/{y} = {x / y:.1%}" if y else "n/a"

    print(f"== {n} dev opinions; GPT = {os.path.basename(a.gpt_dir)}, encoder = {os.path.basename(a.pred)}\n")
    print("MENTIONS (GPT mention vs encoder mention, judged against gold)")
    print(f"  agree & right      {tot['m_agree_right']:6d}")
    print(f"  agree & wrong      {tot['m_agree_wrong']:6d}   <- GPT false positives NOT catchable by disagreement")
    print(f"  GPT-only, GPT right{tot['m_gptonly_right']:6d}   <- encoder misses; adjudication keeps GPT's label")
    print(f"  GPT-only, GPT wrong{tot['m_gptonly_wrong']:6d}   <- GPT false positives caught")
    print(f"  enc-only, enc right{tot['m_enconly_right']:6d}   <- GPT false negatives caught")
    print(f"  enc-only, enc wrong{tot['m_enconly_wrong']:6d}   <- encoder false positives; adjudication rejects")
    print(f"  flag recall  FP {pct(tot['gpt_fp_flagged'], tot['gpt_fp'])}   FN {pct(tot['gpt_fn_flagged'], tot['gpt_fn'])}")
    m_flag = tot["m_gptonly_right"] + tot["m_gptonly_wrong"] + tot["m_enconly_right"] + tot["m_enconly_wrong"]
    m_yield = tot["m_gptonly_wrong"] + tot["m_enconly_right"]
    print(f"  yield (flagged items where GPT is the wrong one) {pct(m_yield, m_flag)}")
    print("  by kind:")
    for cat in ("agree_wrong", "gptonly_right", "gptonly_wrong", "enconly_right", "enconly_wrong"):
        ks = {k.split(":")[1]: v for k, v in tot.items() if k.startswith(f"kind_{cat}:")}
        print(f"    {cat:14} {dict(sorted(ks.items()))}")
    print("\nCOREF PAIRS (mentions shared by GPT, encoder and gold)")
    print(f"  pairs {tot['pairs']}  GPT wrong {tot['p_wrong']}  flagged {tot['p_flag']}  "
          f"flagged&wrong {tot['p_flag_wrong']}  flagged&GPT-right {tot['p_flag_right']}")
    print(f"  flag recall {pct(tot['p_flag_wrong'], tot['p_wrong'])}   yield {pct(tot['p_flag_wrong'], tot['p_flag'])}   "
          f"mentions touched by a coref flag {tot['coref_flag_mentions']}")
    print("\nSCORES (seeding scorer's pairwise metric)")
    m_gpt = prf(tot["m_tp"], tot["m_sys"], tot["m_gold"])
    m_orc = prf(tot["m_tp"] + tot["gpt_fn_flagged"], tot["m_sys"] - tot["gpt_fp_flagged"] + tot["gpt_fn_flagged"], tot["m_gold"])
    c_gpt = prf(tot["c_tp_full"], tot["c_tp_full"] + tot["c_fp_full"], tot["c_tp_full"] + tot["c_fn_full"])
    c_sh = prf(tot["c_tp"], tot["c_tp"] + tot["c_fp"], tot["c_tp"] + tot["c_fn"])
    c_orc = prf(tot["o_tp"], tot["o_tp"] + tot["o_fp"], tot["o_tp"] + tot["o_fn"])
    print(f"  GPT as-is          mention P/R/F {m_gpt}   coref P/R/F {c_gpt} (all gpt<->gold pairs), {c_sh} (shared-mention pairs)")
    print(f"  oracle adjudication mention P/R/F {m_orc}   coref P/R/F {c_orc} (shared-mention pairs; pairs gained from FN fixes not counted)")
    print("\nOPUS WORKLOAD")
    print(f"  opinions with >=1 disagreement {tot['has_disagreement']}/{n};  items {tot['n_items']};  "
          f"windows (+-{a.ctx} chars, merged) {tot['windows']}")
    print(f"  window chars {tot['window_chars']:,} of {tot['opinion_chars']:,} opinion chars = "
          f"{tot['window_chars'] / max(1, tot['opinion_chars']):.1%} of the text")
    print(f"  per opinion: {tot['windows'] / max(1, n):.1f} windows, {tot['n_items'] / max(1, n):.1f} items")
    print("\nCAVEAT: the encoder was trained on round-2 GPT labels; on the training set itself it will disagree less "
          "than on dev (it partly memorised them). These are the optimistic numbers; cross-fit for train.")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({"pooled": dict(tot), "per_cluster": per, "pred": a.pred, "gpt_dir": a.gpt_dir, "ctx": a.ctx},
              open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
