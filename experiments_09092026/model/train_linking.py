"""Task B — train the antecedent-ranking coreference linker.

Per-case mention windows (±context, pooled) feed AntecedentLinker; the loss is
antecedent-ranking marginal log-likelihood. Coref is evaluated by clustering
(union-find over argmax antecedents) → B³ + attachment, run each epoch to
select the best checkpoint. Isolated coref: gold mention spans are given
(extraction error is measured separately by Task A); end-to-end can be run
later by feeding Task A's predicted spans.

  uv run python train_linking.py --base large-caselaw --device cuda
The three splits are arguments (`--train-split` / `--val-split` /
`--test-split`). By default: train on `train`, select on `validation` by B³ F1
each epoch, and report on the human-verified `gold_dev` records →
`best/coref_test.json`. `gold_test` is never read.
"""

from __future__ import annotations

import argparse
import json
import os

import torch
from transformers import (
    AutoTokenizer, Trainer, TrainerCallback, TrainingArguments)

import common as C
from linking_data import Collator, LinkDataset, eval_coref
from linking_model import AntecedentLinker


class CorefEvalCallback(TrainerCallback):
    """Cluster val cases each epoch → B³ F1; keep the best checkpoint."""

    def __init__(self, trainer, val_ds, collator, out):
        self.t, self.val, self.coll, self.out = trainer, val_ds, collator, out
        self.best = -1.0

    def on_epoch_end(self, args, state, control, **kw):
        model = self.t.model
        rep = eval_coref(model, self.val, self.coll)
        f1 = rep["b3_f1"]
        print(f"  [coref-eval] epoch {state.epoch:.0f}: B3 F1={f1:.3f} "
              f"(P={rep['b3_precision']:.3f} R={rep['b3_recall']:.3f}) "
              f"attach_manual={rep['attach'].get('manual', {}).get('rate', 0):.3f}")
        if f1 > self.best:
            self.best = f1
            (self.out / "best").mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), self.out / "best" / "model.pt")
            (self.out / "best" / "coref_val.json").write_text(
                json.dumps(rep, indent=2))


def load_init(model, state):
    """Load a warm-start state; when the checkpoint's pair scorer has FEWER
    feature columns than the model (a round-4 linker into the eyecite-channel
    model), keep its weights for the shared columns and start the new columns
    at zero, so training begins exactly where the old model was. Returns the
    number of padded columns (0 = plain load)."""
    key = "pair.0.weight"
    padded = 0
    cur = model.state_dict()
    if key in state and state[key].shape != cur[key].shape:
        old, new = state[key], cur[key]
        if old.shape[0] != new.shape[0] or old.shape[1] > new.shape[1]:
            raise ValueError(f"{key}: cannot adapt {tuple(old.shape)} -> {tuple(new.shape)}")
        w = torch.zeros_like(new)
        w[:, :old.shape[1]] = old
        state = dict(state)
        state[key] = w
        padded = new.shape[1] - old.shape[1]
    model.load_state_dict(state)
    return padded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="base-dapt")
    ap.add_argument("--train-split", default="train")
    ap.add_argument("--val-split", default="validation")
    ap.add_argument("--test-split", default="gold_dev")
    ap.add_argument("--max-train-docs", type=int, default=None)
    ap.add_argument("--max-val-docs", type=int, default=400)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--ctx", type=int, default=160, help="±chars per window")
    ap.add_argument("--lwin", type=int, default=96, help="max window subwords")
    ap.add_argument("--cand", type=int, default=256,
                    help="max antecedents K (256 → only 0.4%% of same-cluster "
                         "mentions have no in-window antecedent; 512 → none)")
    ap.add_argument("--chunk", type=int, default=32768,
                    help="AntecedentLinker slice budget: tokens per encoder call "
                         "(and candidate-pairs per scorer call). Sized in tokens "
                         "so it costs the same when --lwin changes; caps peak "
                         "memory independently of how many mentions a case has")
    ap.add_argument("--max-mentions", type=int, default=700,
                    help="skip cases with more than this many real mentions — "
                         "training retains every chunk's activations, so the "
                         "rare consolidated opinion still OOMs even chunked")
    ap.add_argument("--dummies", action="store_true",
                    help="inject untagged Id./Ibid./supra as no-antecedent mentions "
                         "(round 3; hurt id attach 0.894 -> 0.808, off by default)")
    ap.add_argument("--grad-checkpoint", action="store_true",
                    help="HF layer-wise gradient checkpointing inside the encoder")
    ap.add_argument("--chunk-checkpoint", action="store_true",
                    help="recompute each mention slice during backward instead of "
                         "retaining it (AntecedentLinker recompute=True): peak "
                         "memory becomes independent of case size AND window "
                         "width. Supersedes --grad-checkpoint; both on = "
                         "recompute inside recompute")
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--device", choices=["mps", "cpu", "cuda"], default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--init", default=None,
                    help="warm start: a trained linker state (runs/<name>/best/model.pt) loaded over the "
                         "--base-initialised module before training; a state trained without the eyecite "
                         "channel is padded (new feature columns start at zero)")
    ap.add_argument("--eyecite-feats", action="store_true",
                    help="round 5: feed eyecite's own grouping (mention_features eyecite_gid) as 3 extra pair "
                         "features — the linker starts from eyecite's groups instead of from scratch")
    ap.add_argument("--eyecite-dropout", type=float, default=0.2,
                    help="share of training cases whose eyecite channel is masked (with --eyecite-feats)")
    args = ap.parse_args()
    if args.device is None:
        args.device = ("cuda" if torch.cuda.is_available()
                       else "mps" if torch.backends.mps.is_available()
                       else "cpu")
    base_id = C.BASES.get(args.base, args.base)
    name = args.name or f"link_{args.base.replace('/', '_')}"
    out = C.HERE / "runs" / name
    out.mkdir(parents=True, exist_ok=True)

    records = C.load_records()
    feats = C.load_features()
    train_recs = C.select(records, args.train_split, args.max_train_docs)
    val_recs = C.select(records, args.val_split, args.max_val_docs)
    test_recs = C.select(records, args.test_split)

    tok = AutoTokenizer.from_pretrained(base_id)
    inject = args.dummies
    mm = args.max_mentions
    train_ds = LinkDataset(train_recs, feats, tok, args.ctx, args.lwin, args.cand, inject, mm)
    val_ds = LinkDataset(val_recs, feats, tok, args.ctx, args.lwin, args.cand, inject, mm)
    test_ds = LinkDataset(test_recs, feats, tok, args.ctx, args.lwin, args.cand, inject, mm)
    print(f"{len(train_ds)} train / {len(val_ds)} val / {len(test_ds)} {args.test_split} cases")

    attn = "sdpa" if args.device in ("mps", "cpu") else None
    nf = n_feats(args.eyecite_feats)
    model = AntecedentLinker(base_id, nf, attn_impl=attn, chunk=args.chunk,
                             recompute=args.chunk_checkpoint)
    if args.chunk_checkpoint and args.grad_checkpoint:
        print("note: --chunk-checkpoint already recomputes each slice; "
              "--grad-checkpoint on top means recompute inside recompute")
    if args.init:
        init_pt = args.init if args.init.endswith(".pt") else os.path.join(args.init, "model.pt")
        padded = load_init(model, torch.load(init_pt, map_location="cpu"))
        print(f"warm start from {init_pt}" + (f" (padded {padded} new feature columns with zeros)" if padded else ""))
    if args.grad_checkpoint:
        model.encoder.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False})
    coll = Collator(args.cand, args.eyecite_feats, args.eyecite_dropout)
    eval_coll = Collator(args.cand, args.eyecite_feats)          # never masked at eval

    targs = TrainingArguments(
        output_dir=str(out),
        num_train_epochs=args.epochs, max_steps=args.max_steps,
        learning_rate=args.lr,
        per_device_train_batch_size=1,          # one case per step
        gradient_accumulation_steps=args.grad_accum,
        **C.warmup_kwargs(0.1), weight_decay=0.01, lr_scheduler_type="linear",
        eval_strategy="no", save_strategy="no",
        logging_steps=10, remove_unused_columns=False,
        bf16=args.device == "cuda", use_cpu=args.device == "cpu",
        dataloader_pin_memory=args.device == "cuda", seed=42, report_to=[])

    trainer = Trainer(model=model, args=targs, train_dataset=train_ds,
                      data_collator=coll)
    cb = None
    if args.max_steps < 0:
        cb = CorefEvalCallback(trainer, val_ds, eval_coll, out)
        trainer.add_callback(cb)
    trainer.train()
    if args.max_steps < 0:
        # reload the best-on-VAL checkpoint, then score the held-out TEST set
        # once — never seen by training or selection. → aggregate_cv.py reads
        # coref_test.json.
        best_pt = out / "best" / "model.pt"
        if best_pt.exists():
            model.load_state_dict(torch.load(best_pt, map_location="cpu"))
            model.to(next(model.parameters()).device)
        test_rep = eval_coref(model, test_ds, eval_coll)
        (out / "best" / "coref_test.json").write_text(
            json.dumps(test_rep, indent=2))
        print(f"\nbest val B3 F1 = {cb.best:.3f}  |  "
              f"held-out test B3 F1 = {test_rep['b3_f1']:.3f} "
              f"(saved to {out / 'best'})")
        if args.eyecite_feats:
            # the same model with the eyecite channel masked on every case: what
            # it does when eyecite is not run in front of it
            noeye = eval_coref(model, test_ds, Collator(args.cand, True, 1.0))
            (out / "best" / "coref_test_noeye.json").write_text(json.dumps(noeye, indent=2))
            print(f"  without the eyecite channel: test B3 F1 = {noeye['b3_f1']:.3f}")
    else:
        print("smoke run finished OK")


if __name__ == "__main__":
    main()
