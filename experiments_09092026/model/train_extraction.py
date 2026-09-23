"""Task A — citation mention extraction (BIO token classification).

Replaces eyecite's extraction: fed raw opinion text (citation markup
stripped), tag every case-citation mention span. Long opinions are chunked
into overlapping windows; char-offset gold mentions are aligned to token BIO
labels (common.align_bio). Removed negatives stay O — the model earns
precision by NOT tagging them; eval reports how often it does.

HF Trainer + AutoModelForTokenClassification.
  uv run python train_extraction.py --base large-caselaw --device cuda
The three splits are arguments (`--train-split` / `--val-split` /
`--test-split`). By default: train on `train`, select on `validation` by
`eval_span_f1` every --eval-steps, and report on the human-verified `gold_dev`
records → `eval_test.json` (span P/R/F1, recall on the curator-added mentions
eyecite missed, rate of tagging curator-removed spans). `gold_test` is never
read.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import torch
from torch.utils.data import Dataset
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    DataCollatorForTokenClassification,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)

import common as C
from metrics import extraction_report


class WindowDataset(Dataset):
    """Overlapping token windows over opinions, with BIO labels aligned from
    char-offset mentions. Each item: input_ids, attention_mask, labels, and
    bookkeeping (cid, opinion_idx, window char-origin) for span decoding."""

    def __init__(self, records, tokenizer, max_length, stride):
        self.items = []
        for r in records:
            enc = tokenizer(
                r["text"],
                max_length=max_length,
                truncation=True,
                stride=stride,
                return_overflowing_tokens=True,
                return_offsets_mapping=True,
            )
            for win_i in range(len(enc["input_ids"])):
                offsets = enc["offset_mapping"][win_i]
                w_start = min((cs for cs, ce in offsets if ce > cs), default=0)
                w_end = max((ce for cs, ce in offsets if ce > cs), default=0)
                rel = C.spans_in_window(r["mentions"], w_start, w_end)
                # offsets are global char coords; align_bio works in any
                # single frame, so pass global offsets + global spans.
                spans = [(m["start"], m["end"]) for _, _, m in rel]
                labels = C.align_bio(offsets, spans)
                self.items.append({
                    "input_ids": enc["input_ids"][win_i],
                    "attention_mask": enc["attention_mask"][win_i],
                    "labels": labels,
                    "offsets": offsets,
                    "cid": r["citing_cluster_id"],
                    "opinion_idx_text": r["text"],
                })

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        it = self.items[i]
        return {k: it[k] for k in ("input_ids", "attention_mask", "labels")}


def gold_lookup(records):
    """{cid: ([(s,e) mentions], [(s,e) manual], [(s,e) negatives])} per opinion
    flattened — used for span-level scoring at the opinion level."""
    g = {}
    for ri, r in enumerate(records):
        key = (r["citing_cluster_id"], r["opinion_id"])
        ment = [(m["start"], m["end"]) for m in r["mentions"]]
        man = [(m["start"], m["end"]) for m in r["mentions"]
               if m["source"] == "manual"]
        neg = [(n["start"], n["end"]) for n in r["negatives"]]
        g[key] = (ment, man, neg)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="base-dapt")
    ap.add_argument("--train-split", default="train")
    ap.add_argument("--val-split", default="validation")
    ap.add_argument("--test-split", default="gold_dev")
    ap.add_argument("--max-train-docs", type=int, default=None, help="cap on training clusters")
    ap.add_argument("--max-val-docs", type=int, default=400, help="cap on val clusters (eval speed)")
    ap.add_argument("--eval-steps", type=int, default=1000, help="evaluate/save every N optimizer steps (0 = per epoch)")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--max-length", type=int, default=4096)
    ap.add_argument("--stride", type=int, default=256)
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--grad-checkpoint", action="store_true",
                    help="gradient checkpointing (slower, big memory save — "
                         "use for --base large)")
    ap.add_argument("--max-steps", type=int, default=-1, help="smoke cap")
    ap.add_argument("--device", choices=["mps", "cpu", "cuda"], default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--init", default=None,
                    help="warm start: a trained extraction checkpoint dir (runs/<name>/best) whose weights "
                         "replace the --base init; tokenizer/architecture still come from --base")
    args = ap.parse_args()
    if args.device is None:
        args.device = ("cuda" if torch.cuda.is_available()
                       else "mps" if torch.backends.mps.is_available()
                       else "cpu")
    base_id = C.BASES.get(args.base, args.base)
    name = args.name or f"extract_{args.base.replace('/', '_')}"
    out = C.HERE / "runs" / name
    out.mkdir(parents=True, exist_ok=True)

    records = C.load_records()
    train_recs = C.select(records, args.train_split, args.max_train_docs)
    val_recs = C.select(records, args.val_split, args.max_val_docs)
    test_recs = C.select(records, args.test_split)

    tok = AutoTokenizer.from_pretrained(base_id)
    train_ds = WindowDataset(train_recs, tok, args.max_length, args.stride)
    val_ds = WindowDataset(val_recs, tok, args.max_length, args.stride)
    test_ds = WindowDataset(test_recs, tok, args.max_length, args.stride)
    ncl = lambda rs: len({r["citing_cluster_id"] for r in rs})  # noqa: E731
    print(f"{ncl(train_recs)} train / {ncl(val_recs)} val / {ncl(test_recs)} {args.test_split} clusters; "
          f"{len(train_ds)} / {len(val_ds)} / {len(test_ds)} windows")

    attn = "sdpa" if args.device in ("mps", "cpu") else None
    # warm start: --init is a finished extraction run (same architecture + label
    # set), so its weights load directly and the classifier head is kept.
    model = AutoModelForTokenClassification.from_pretrained(
        args.init or base_id, num_labels=len(C.ID2LABEL),
        id2label=C.ID2LABEL, label2id=C.LABEL2ID,
        **({"attn_implementation": attn} if attn else {}))
    if args.init:
        print(f"warm start from {args.init}")

    # span-level metrics: decode predicted spans per window, lift to global
    # char coords, compare to gold per opinion. Factory so the SAME scorer
    # runs over val (selection) and the held-out test (final report).
    def make_metrics(ds, recs):
        def compute_metrics(ep):
            logits = ep.predictions
            preds = np.argmax(logits, axis=-1)
            pred_spans, gold_spans, manual_spans, neg_spans = [], [], [], []
            # rebuild per-window predicted spans, group by source text identity
            per_text = {}
            for wi, it in enumerate(ds.items):
                offs = it["offsets"]
                txt = it["opinion_idx_text"]
                # decode_spans returns global char spans; trim each to non-space
                # so BPE leading-space offsets align with the (trimmed) gold.
                spans = {sp for s, e in C.decode_spans(preds[wi][:len(offs)], offs)
                         if (sp := C.trim_span(txt, s, e))}
                per_text.setdefault(id(txt), {"text": txt, "pred": set()})
                per_text[id(txt)]["pred"].update(spans)
            # gold per opinion (matched by text identity)
            text_gold = {}
            for r in recs:
                text_gold[id(r["text"])] = (
                    {(m["start"], m["end"]) for m in r["mentions"]},
                    {(m["start"], m["end"]) for m in r["mentions"]
                     if m["source"] == "manual"},
                    {(n["start"], n["end"]) for n in r["negatives"]})
            for tid, pv in per_text.items():
                g, man, neg = text_gold.get(tid, (set(), set(), set()))
                pred_spans.append(pv["pred"])
                gold_spans.append(g)
                manual_spans.append(man)
                neg_spans.append(neg)
            return extraction_report(
                pred_spans, gold_spans, manual_spans, neg_spans)
        return compute_metrics

    targs = TrainingArguments(
        output_dir=str(out),
        num_train_epochs=args.epochs, max_steps=args.max_steps,
        learning_rate=args.lr,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=args.grad_checkpoint,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        **C.warmup_kwargs(0.1), weight_decay=0.01, lr_scheduler_type="linear",
        eval_strategy=("no" if args.max_steps >= 0 else "steps" if args.eval_steps > 0 else "epoch"),
        eval_steps=args.eval_steps if args.eval_steps > 0 else None,
        save_strategy=("no" if args.max_steps >= 0 else "steps" if args.eval_steps > 0 else "epoch"),
        save_steps=args.eval_steps if args.eval_steps > 0 else None,
        save_total_limit=1,
        # only model weights in checkpoints — no optimizer/scheduler/rng state
        # (we never resume; this is what overflowed a 50GB volume on `large`,
        # cutting per-checkpoint size by ~2/3). Compatible with load_best.
        save_only_model=True,
        load_best_model_at_end=args.max_steps < 0,
        metric_for_best_model="eval_span_f1", greater_is_better=True,
        logging_steps=10, label_names=["labels"],
        bf16=args.device == "cuda", use_cpu=args.device == "cpu",
        dataloader_pin_memory=args.device == "cuda", seed=42, report_to=[])

    trainer = Trainer(
        model=model, args=targs,
        train_dataset=train_ds, eval_dataset=val_ds,
        data_collator=DataCollatorForTokenClassification(tok),
        compute_metrics=make_metrics(val_ds, val_recs),
        callbacks=([EarlyStoppingCallback(args.patience)]
                   if args.max_steps < 0 else []))
    trainer.train()
    if args.max_steps < 0:
        # checkpoint selected on VAL (metric_for_best_model="eval_span_f1");
        # load_best_model_at_end → trainer.model is now the best-on-val model.
        val_metrics = trainer.evaluate()
        (out / "eval_val.json").write_text(json.dumps(val_metrics, indent=2))
        trainer.save_model(out / "best")
        tok.save_pretrained(out / "best")
        # held-out TEST — never seen by training or selection. This is the
        # number aggregate_cv.py reports.
        trainer.compute_metrics = make_metrics(test_ds, test_recs)
        test_metrics = trainer.evaluate(test_ds, metric_key_prefix="test")
        (out / "eval_test.json").write_text(json.dumps(test_metrics, indent=2))
        print("\n=== val (selection) / test (held-out) ===")
        for vk in sorted(val_metrics):
            tk = vk.replace("eval_", "test_", 1)
            if vk.startswith("eval_") and isinstance(val_metrics[vk], float):
                tv = test_metrics.get(tk)
                tv = f"{tv:.3f}" if isinstance(tv, float) else "—"
                print(f"{vk[5:]:38s} val {val_metrics[vk]:.3f}   test {tv}")
    else:
        print("smoke run finished OK")


if __name__ == "__main__":
    main()
