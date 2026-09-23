"""Isolated coreference eval (gold mention spans given) of a trained linker on
any split, with the decode options chosen at the command line — the laptop-side
counterpart of the per-epoch `eval_coref` the trainer runs on the pod.

    uv run python eval_isolated.py --link _r4 --split validation
    # a linker trained with the eyecite channel, channel fed vs masked:
    CIT_FEATS=../data/output/mention_features_r2e.jsonl \\
        uv run python eval_isolated.py --link _r5 --eyecite-feats [--mask-eyecite]

Records default to the round-2 build (citations_r2.jsonl); the features file
must match the linker (the eyecite channel needs the _r2e file, which carries
eyecite_gid — pass CIT_FEATS). Writes
../data/output/isolated_<split><link>[_noeye][_norule].json and prints B³ +
attach by kind, in the same shape as the reports in ../results/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import torch
from transformers import AutoTokenizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from linking_model import AntecedentLinker  # noqa: E402
from linking_data import Collator, LinkDataset, eval_coref  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.environ.get("SILVER_RUNS", os.path.join(HERE, "..", "runs"))
EXTRACT = os.path.join(RUNS, "extract_large-caselaw_warm", "best")   # local copy of the backbone config/tokenizer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", default="_r4", help="linker run suffix: runs/link_large-caselaw<link>/best/model.pt")
    ap.add_argument("--split", default="validation")
    ap.add_argument("--records", default=os.path.join(HERE, "..", "data", "output", "citations_r2.jsonl"))
    ap.add_argument("--eyecite-feats", action="store_true", help="the linker was trained with the eyecite channel (r5)")
    ap.add_argument("--mask-eyecite", action="store_true", help="with --eyecite-feats: feed the channel as all-zeros")
    ap.add_argument("--no-id-rule", action="store_true")
    ap.add_argument("--ctx", type=int, default=128)
    ap.add_argument("--lwin", type=int, default=128, help="64 for the r2 warm/cold linkers")
    ap.add_argument("--cand", type=int, default=256)
    ap.add_argument("--max-mentions", type=int, default=700, help="the trainer's cap; keeps the case set comparable")
    ap.add_argument("--max-docs", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.split == "gold_test":
        sys.exit("gold_test is spent — not scoring it")
    dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    records = C.select(C.load_records(a.records), a.split, a.max_docs)
    feats = C.load_features()
    if a.eyecite_feats and not any("eyecite_gid" in f for f in list(feats.values())[:1000]):
        sys.exit("--eyecite-feats but the features file has no eyecite_gid column: set CIT_FEATS=../data/output/mention_features_r2e.jsonl")
    tok = AutoTokenizer.from_pretrained(EXTRACT)
    ds = LinkDataset(records, feats, tok, a.ctx, a.lwin, a.cand, max_mentions=a.max_mentions)
    print(f"{len(ds)} {a.split} cases on {dev}; link{a.link}; eyecite={a.eyecite_feats} masked={a.mask_eyecite} id_rule={not a.no_id_rule}")
    model = AntecedentLinker(EXTRACT, C.n_feats(a.eyecite_feats), attn_impl="sdpa")
    state = torch.load(os.path.join(RUNS, f"link_large-caselaw{a.link}", "best", "model.pt"), map_location="cpu")
    model.load_state_dict(state)
    model.to(dev).eval()
    coll = Collator(a.cand, a.eyecite_feats, 1.0 if a.mask_eyecite else 0.0)
    rep = eval_coref(model, ds, coll, id_rule=not a.no_id_rule)
    tag = f"{a.split}{a.link}" + ("_noeye" if a.mask_eyecite else "") + ("_norule" if a.no_id_rule else "")
    out = a.out or os.path.join(HERE, "..", "data", "output", f"isolated_{tag}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    att = rep["attach"]
    print(f"B3 P/R/F1 {rep['b3_precision']:.4f} / {rep['b3_recall']:.4f} / {rep['b3_f1']:.4f}  n={rep['n_mentions']}")
    print("  attach: " + "  ".join(f"{k}={v['correct']}/{v['total']} ({v['rate']:.3f})" for k, v in att.items()
                                  if k in ("all", "id", "name", "reporter", "supra", "manual", "eyecite")))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
