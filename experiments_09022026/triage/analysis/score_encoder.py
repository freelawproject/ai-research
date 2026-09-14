"""Score the experiments_09092026 encoder (extraction + linking) with the SAME
scorer used for eyecite and the LLM seeding runs (citation_seed.score_states:
overlap-matched mention P/R/F1 + pairwise coreference P/R/F1 against the
current annotator gold), so the rows are apples-to-apples.

Input: pred_gold_dev.json from experiments_09092026/finetune/predict_gold_dev.py
({cid: {"opinions": [{"opinion_type", "mentions": [{"text", "before", "cluster"}]}]}}).
Predicted spans live in the dataset's text frame (revised_html → text); they
are re-anchored into the annotator frame by text + preceding context with the
same whitespace-insensitive matcher the LLM `add` edits use (nth_of).

    python3 analysis/score_encoder.py --pred ../../experiments_09092026/data/output/pred_gold_dev.json \\
        --ids $(cat data/citation_seed/dev_ids.txt)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from citation_seed import State, nth_of, prf, score_states  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))


class EncoderState:
    """Minimal State look-alike: only .mentions() is needed by score_states."""

    def __init__(self, cid, pred, gold):
        self.cid = str(cid)
        self._mentions = []
        self.unanchored = 0
        self.total = 0
        # revised_citation_html emits sections in the annotator's (combined-dropped)
        # opinion order, which is also State.opinions' order -> map by index and
        # check the type; several opinions can share a type (multiple dissents).
        ordered = [str(o["id"]) for o in gold.opinions]
        for op in pred["opinions"]:
            oid = None
            i = op.get("opinion_idx")
            if i is not None and i < len(ordered) and gold.op_type[ordered[i]] == op["opinion_type"]:
                oid = ordered[i]
            elif len(gold.texts) == 1:
                oid = ordered[0]
            else:
                same = [o for o in ordered if gold.op_type[o] == op["opinion_type"]]
                oid = same[0] if len(same) == 1 else None
            if oid is None:
                self.unanchored += len(op["mentions"])
                self.total += len(op["mentions"])
                continue
            text = gold.texts[oid]
            for m in op["mentions"]:
                self.total += 1
                n, sp = nth_of(text, m["text"], m.get("before", ""))
                if sp is None:
                    self.unanchored += 1
                    continue
                self._mentions.append((oid, sp[0], sp[1], f"c{m['cluster']}", "occ", f"{oid}:{sp[0]}"))

    def mentions(self):
        return self._mentions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--label", default="encoder (silver, large-caselaw)")
    a = ap.parse_args()
    pred = json.load(open(a.pred, encoding="utf-8"))
    raw = {"m_tp": 0, "m_sys": 0, "m_gold": 0, "c_tp": 0, "c_fp": 0, "c_fn": 0}
    eye = dict(raw)
    n, unanch, tot = 0, 0, 0
    for cid in a.ids:
        if str(cid) not in pred:
            print(f"{cid}: no predictions (skipped)")
            continue
        gold = State(cid, seed_state=False)
        enc = EncoderState(cid, pred[str(cid)], gold)
        unanch += enc.unanchored
        tot += enc.total
        r = score_states(enc, gold)["_raw"]
        for k in raw:
            raw[k] += r[k]
        e = score_states(State(cid, seed_state=True), gold)["_raw"]
        for k in eye:
            eye[k] += e[k]
        n += 1
    print(f"== POOLED over {n} opinions (annotator gold as of now); encoder spans anchored {tot - unanch}/{tot}")
    for label, r in ((f"eyecite", eye), (a.label, raw)):
        mp = prf(r["m_tp"], r["m_sys"], r["m_gold"])
        cp = prf(r["c_tp"], r["c_tp"] + r["c_fp"], r["c_tp"] + r["c_fn"])
        print(f"  {label:32}: mention P/R/F {mp}  coref P/R/F {cp}  ({r['m_sys']} vs gold {r['m_gold']})")


if __name__ == "__main__":
    main()
