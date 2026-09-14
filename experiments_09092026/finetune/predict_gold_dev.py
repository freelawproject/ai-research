"""Run the trained extraction + linking checkpoints over the gold_dev records
and dump predictions, so the encoder can be scored with the triage scorer
(experiments_09022026/triage/lib/citation_seed.py) apples-to-apples with eyecite
and the LLM seeding runs.

    uv run python predict_gold_dev.py            # -> ../data/output/pred_gold_dev.json
    uv run python predict_gold_dev.py --split gold_test   # only when Rachel asks

Pipeline (end to end, no gold mentions used): extraction windows -> BIO argmax
-> char spans (decode_spans + trim_span, exactly as train_extraction scores)
-> mention features computed on the PREDICTED spans (mention_features logic)
-> AntecedentLinker.predict_clusters over the predicted mentions.
Output: {cid: {"opinions": [{"opinion_idx", "opinion_type", "text_len",
"mentions": [{"start","end","text","cluster"}]}]}}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as C  # noqa: E402
from linking_model import AntecedentLinker  # noqa: E402
from mention_features import classify, name_tokens, reporter_key  # noqa: E402
from train_linking import Collator, LinkDataset  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# silver_runs.tar.gz was extracted at the experiment root (runs/ sits next to finetune/)
RUNS = os.environ.get("SILVER_RUNS", os.path.join(HERE, "..", "runs"))
# round-2 runs are named extract_large-caselaw_warm / _cold: RUN_SUFFIX=_warm
SUFFIX = os.environ.get("RUN_SUFFIX", "")
EXTRACT = os.path.join(RUNS, f"extract_large-caselaw{SUFFIX}", "best")
LINK = os.path.join(RUNS, f"link_large-caselaw{SUFFIX}", "best", "model.pt")


def device():
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def extract(records, tok, model, dev, max_length=4096, stride=256, batch=2):
    """Predicted char spans per record (set of (s, e)), same decoding as training."""
    model.eval()
    preds = {}
    for r in records:
        if not r["text"]:
            preds[(r["citing_cluster_id"], r["opinion_idx"])] = set()
            continue
        enc = tok(r["text"], max_length=max_length, truncation=True, stride=stride,
                  return_overflowing_tokens=True, return_offsets_mapping=True)
        spans = set()
        n = len(enc["input_ids"])
        for b0 in range(0, n, batch):
            ids = [enc["input_ids"][i] for i in range(b0, min(n, b0 + batch))]
            L = max(len(x) for x in ids)
            input_ids = torch.full((len(ids), L), tok.pad_token_id, dtype=torch.long)
            attn = torch.zeros((len(ids), L), dtype=torch.long)
            for k, x in enumerate(ids):
                input_ids[k, :len(x)] = torch.tensor(x)
                attn[k, :len(x)] = 1
            with torch.no_grad():
                logits = model(input_ids=input_ids.to(dev), attention_mask=attn.to(dev)).logits
            lab = logits.argmax(-1).cpu().numpy()
            for k in range(len(ids)):
                offs = enc["offset_mapping"][b0 + k]
                for s, e in C.decode_spans(lab[k][:len(offs)], offs):
                    sp = C.trim_span(r["text"], s, e)
                    if sp:
                        spans.add(sp)
        preds[(r["citing_cluster_id"], r["opinion_idx"])] = spans
    return preds


def pred_records(records, preds):
    """Copies of the records whose `mentions` are the PREDICTED spans (cluster
    placeholder '0', source 'pred'), plus a features dict in mention_features' shape."""
    out, feats = [], {}
    for r in records:
        spans = sorted(preds[(r["citing_cluster_id"], r["opinion_idx"])])
        ms = []
        for s, e in spans:
            t = r["text"][s:e]
            kind = classify(t)
            names = name_tokens(t)
            if kind in ("reporter", "id"):
                names |= name_tokens(" ".join(r["text"][:s].split()[-8:]))
            ms.append({"start": s, "end": e, "text": t, "cluster": "0", "source": "pred"})
            feats[(r["citing_cluster_id"], r["opinion_idx"], s)] = {
                "kind": kind, "reporter_key": reporter_key(t), "name_tokens": sorted(names),
                "is_id": kind == "id", "is_supra": kind == "supra"}
        out.append({**r, "mentions": ms})
    return out, feats


def link(prec, feats, tok, linker, dev, ctx, lwin, K):
    ds = LinkDataset(prec, feats, tok, ctx, lwin, K)
    coll = Collator(K)
    linker.eval()
    clusters = {}
    for case in ds.cases:
        batch = {k: v.to(dev) for k, v in coll([case]).items()}
        pred = linker.predict_clusters(batch)
        for m, c in zip(case["ms"], pred):
            clusters[(case["cid"], m["opinion_idx"], m["start"])] = c
    return clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="gold_dev")
    ap.add_argument("--records", help="alternate records jsonl (e.g. data/output/gold_dev_fresh.jsonl rebuilt from current revised_html)")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "output", f"pred_gold_dev{SUFFIX}.json"))
    ap.add_argument("--ctx", type=int, default=128)
    ap.add_argument("--lwin", type=int, default=64)
    ap.add_argument("--cand", type=int, default=256)
    ap.add_argument("--max-length", type=int, default=4096)
    a = ap.parse_args()
    if a.split == "gold_test":
        print("WARNING: gold_test is the held-out set — run this only on Rachel's explicit request.", file=sys.stderr)
    dev = device()
    records = C.select(C.load_records(a.records) if a.records else C.load_records(), a.split)
    print(f"{len(records)} {a.split} records ({len({r['citing_cluster_id'] for r in records})} clusters) on {dev}")

    tok = AutoTokenizer.from_pretrained(EXTRACT)
    ext = AutoModelForTokenClassification.from_pretrained(EXTRACT, attn_implementation="sdpa").to(dev)
    preds = extract(records, tok, ext, dev, max_length=a.max_length)
    n_pred = sum(len(v) for v in preds.values())
    print(f"extraction: {n_pred} predicted spans")
    del ext

    prec, feats = pred_records(records, preds)
    # the linker's encoder weights are inside model.pt; build the module on the
    # extraction checkpoint (same architecture, local) to avoid a HF download,
    # then overwrite everything with the saved linker state.
    linker = AntecedentLinker(EXTRACT, C.N_FEATS, attn_impl="sdpa")
    state = torch.load(LINK, map_location="cpu")
    missing, unexpected = linker.load_state_dict(state, strict=False)
    if missing or unexpected:
        print(f"linker state: missing {len(missing)} unexpected {len(unexpected)} keys", file=sys.stderr)
    linker.to(dev)
    clusters = link(prec, feats, tok, linker, dev, a.ctx, a.lwin, a.cand)
    print(f"linking: {len(clusters)} mentions clustered")

    out = {}
    for r in prec:
        cid = r["citing_cluster_id"]
        out.setdefault(str(cid), {"opinions": []})["opinions"].append({
            "opinion_idx": r["opinion_idx"], "opinion_type": r["opinion_type"], "text_len": len(r["text"]),
            "mentions": [{"start": m["start"], "end": m["end"], "text": m["text"],
                          "before": r["text"][max(0, m["start"] - 40):m["start"]],
                          "cluster": clusters.get((cid, r["opinion_idx"], m["start"]), -1)} for m in r["mentions"]]})
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
