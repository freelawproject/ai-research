"""Run the trained extraction + linking checkpoints over the gold_dev records
and dump predictions, so the encoder can be scored with the triage scorer
(experiments_09022026/triage/lib/citation_seed.py) apples-to-apples with eyecite
and the LLM seeding runs.

    uv run python inference.py            # -> ../data/output/pred_gold_dev.json
    uv run python inference.py --split gold_test   # held out — only on request
    # round 5 linker (eyecite channel): join eyecite's groups onto the PREDICTED spans
    RUN_SUFFIX=_warm LINK_SUFFIX=_r5 uv run python inference.py --eyecite-feats
    # any split of any records file, e.g. the encoder over the round-2 training set
    RUN_SUFFIX=_warm LINK_SUFFIX=_r4 uv run python inference.py --split train,validation \
        --records ../data/output/citations_r2.jsonl --out ../data/output/pred_train_validation_r4_rule.json

Pipeline (end to end, no gold mentions used): extraction windows -> BIO argmax
-> char spans (decode_spans + trim_span, exactly as train_extraction scores)
-> mention features computed on the PREDICTED spans (mention_features logic)
-> AntecedentLinker.predict_clusters over the predicted mentions.
Output: {cid: {"opinions": [{"opinion_idx", "opinion_type", "text_len",
"mentions": [{"start","end","text","group"}]}]}}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# mention_features.py lives in ../corpus/ in the repo; the pod bundles stage it
# flat at ../, so both locations are on the path.
sys.path.insert(0, EXP)
sys.path.insert(0, os.path.join(EXP, "corpus"))
import common as C  # noqa: E402
from linking_model import AntecedentLinker  # noqa: E402
from mention_features import EyeciteJoin, classify, name_tokens, reporter_key  # noqa: E402
from linking_data import Collator, LinkDataset  # noqa: E402

# silver_runs.tar.gz was extracted at the experiment root (runs/ sits next to model/)
RUNS = os.environ.get("SILVER_RUNS", os.path.join(HERE, "..", "runs"))
# round-2 runs are named extract_large-caselaw_warm / _cold: RUN_SUFFIX=_warm
SUFFIX = os.environ.get("RUN_SUFFIX", "")
# linking-only rounds (r3, r4) reuse the warm extraction checkpoint:
# RUN_SUFFIX=_warm LINK_SUFFIX=_r4
LINK_SUFFIX = os.environ.get("LINK_SUFFIX", SUFFIX)
EXTRACT = os.path.join(RUNS, f"extract_large-caselaw{SUFFIX}", "best")
LINK = os.path.join(RUNS, f"link_large-caselaw{LINK_SUFFIX}", "best", "model.pt")
# raw html_with_citations payloads the eyecite channel is read from (round 5)
DEFAULT_PAYLOADS = [os.path.join(EXP, "data", "annotator_r2", "data", "opinion_html"),
                    os.path.join(EXP, "..", "experiments_09022026", "triage", "data", "annotator", "data", "opinion_html")]


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
    for n, r in enumerate(records, 1):
        if n % 500 == 0:
            print(f"  extraction {n}/{len(records)} records", file=sys.stderr, flush=True)
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


def pred_records(records, preds, eye_join=None):
    """Copies of the records whose `mentions` are the PREDICTED spans (cluster
    placeholder '0', source 'pred'), plus a features dict in mention_features' shape.
    With ``eye_join`` (EyeciteJoin) each predicted span also gets eyecite's group
    id when eyecite tagged an overlapping span — the round-5 input channel."""
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
            ms.append({"start": s, "end": e, "text": t, "group": "0", "source": "pred"})
            feats[(r["citing_cluster_id"], r["opinion_idx"], s)] = {
                "kind": kind, "reporter_key": reporter_key(t), "name_tokens": sorted(names),
                "is_id": kind == "id", "is_supra": kind == "supra"}
        rec = {**r, "mentions": ms}
        if eye_join is not None:
            for s, g in eye_join.gids(rec).items():
                feats[(r["citing_cluster_id"], r["opinion_idx"], s)]["eyecite_gid"] = g
        out.append(rec)
    return out, feats


def link(prec, feats, tok, linker, dev, ctx, lwin, K, dummies=False, id_rule=True, eyecite=False,
         max_mentions=700):
    ds = LinkDataset(prec, feats, tok, ctx, lwin, K, inject_dummies=dummies, max_mentions=max_mentions)
    coll = Collator(K, eyecite)
    linker.eval()
    clusters = {}
    for n, case in enumerate(ds.cases, 1):
        if n % 500 == 0:
            print(f"  linking {n}/{len(ds.cases)} cases", file=sys.stderr, flush=True)
        batch = {k: v.to(dev) for k, v in coll([case]).items()}
        pred = linker.predict_clusters(batch, id_rule=id_rule)
        for m, c in zip(case["ms"], pred):
            clusters[(case["cid"], m["opinion_idx"], m["start"])] = c
    return clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="gold_dev")
    ap.add_argument("--records", help="alternate records jsonl (e.g. data/output/gold_dev_fresh.jsonl rebuilt from current revised_html)")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "output", f"pred_gold_dev{LINK_SUFFIX}.json"))
    # must match what the linker was trained with (run_runpod.sh MODE=r4link);
    # pass --lwin 64 to score the round-2 / round-3 linkers
    ap.add_argument("--ctx", type=int, default=128)
    ap.add_argument("--lwin", type=int, default=128)
    ap.add_argument("--cand", type=int, default=256)
    ap.add_argument("--max-length", type=int, default=4096)
    ap.add_argument("--batch", type=int, default=2, help="extraction windows per forward pass (16 on an 80GB card)")
    ap.add_argument("--max-mentions", type=int, default=700,
                    help="skip cases with more mentions than this at linking (the training cap); 0 = no cap "
                         "(inference is chunked and runs under no_grad, so use 0 on a pod)")
    ap.add_argument("--limit", type=int, help="only the first N records (smoke)")
    ap.add_argument("--no-id-rule", action="store_true",
                    help="disable the Id.->nearest decode rule (to score the raw model)")
    ap.add_argument("--dummies", action="store_true",
                    help="inject no-antecedent Id./supra phantoms at inference — only "
                         "for scoring a linker that was TRAINED with --dummies (r3)")
    ap.add_argument("--eyecite-feats", action="store_true",
                    help="the linker was trained with the eyecite channel (r5): join eyecite's groups "
                         "onto the predicted spans from --payload-dir and feed the 3 extra pair features")
    ap.add_argument("--no-eyecite-input", action="store_true",
                    help="with --eyecite-feats: build the r5 model but feed an EMPTY eyecite channel "
                         "(what it does when eyecite is not run in front of it)")
    ap.add_argument("--payload-dir", action="append", default=None,
                    help="opinion_html payload dir(s) for the eyecite channel (default: round-2 + triage roots)")
    a = ap.parse_args()
    if a.split == "gold_test":
        print("WARNING: gold_test is the held-out set — scoring it spends it.", file=sys.stderr)
    dev = device()
    records = C.select(C.load_records(a.records) if a.records else C.load_records(), a.split)
    if a.limit:
        records = records[:a.limit]
    print(f"{len(records)} {a.split} records ({len({r['citing_cluster_id'] for r in records})} clusters) on {dev}")

    tok = AutoTokenizer.from_pretrained(EXTRACT)
    ext = AutoModelForTokenClassification.from_pretrained(EXTRACT, attn_implementation="sdpa").to(dev)
    preds = extract(records, tok, ext, dev, max_length=a.max_length, batch=a.batch)
    n_pred = sum(len(v) for v in preds.values())
    print(f"extraction: {n_pred} predicted spans")
    del ext

    eye_join = None
    if a.eyecite_feats and not a.no_eyecite_input:
        eye_join = EyeciteJoin(a.payload_dir or DEFAULT_PAYLOADS)
    prec, feats = pred_records(records, preds, eye_join)
    if eye_join is not None:
        n_eye = sum("eyecite_gid" in f for f in feats.values())
        print(f"eyecite channel: {n_eye}/{len(feats)} predicted spans carry an eyecite group ({dict(eye_join.stats)})")
    # the linker's encoder weights are inside model.pt; build the module on the
    # extraction checkpoint (same architecture, local) to avoid a HF download,
    # then overwrite everything with the saved linker state.
    linker = AntecedentLinker(EXTRACT, C.n_feats(a.eyecite_feats), attn_impl="sdpa")
    state = torch.load(LINK, map_location="cpu")
    missing, unexpected = linker.load_state_dict(state, strict=False)
    if missing or unexpected:
        print(f"linker state: missing {len(missing)} unexpected {len(unexpected)} keys", file=sys.stderr)
    linker.to(dev)
    clusters = link(prec, feats, tok, linker, dev, a.ctx, a.lwin, a.cand, a.dummies,
                    id_rule=not a.no_id_rule, eyecite=a.eyecite_feats, max_mentions=a.max_mentions)
    print(f"linking: {len(clusters)} mentions clustered")

    out = {}
    for r in prec:
        cid = r["citing_cluster_id"]
        out.setdefault(str(cid), {"opinions": []})["opinions"].append({
            "opinion_idx": r["opinion_idx"], "opinion_type": r["opinion_type"], "text_len": len(r["text"]),
            "mentions": [{"start": m["start"], "end": m["end"], "text": m["text"],
                          "before": r["text"][max(0, m["start"] - 40):m["start"]],
                          "group": clusters.get((cid, r["opinion_idx"], m["start"]), -1)} for m in r["mentions"]]})
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
