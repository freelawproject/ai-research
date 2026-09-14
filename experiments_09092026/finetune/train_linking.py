"""Task B — train the antecedent-ranking coreference linker.

Per-case mention windows (±context, pooled) feed AntecedentLinker; the loss is
antecedent-ranking marginal log-likelihood. Coref is evaluated by clustering
(union-find over argmax antecedents) → B³ + attachment, run each epoch to
select the best checkpoint. Isolated coref: gold mention spans are given
(extraction error is measured separately by Task A); end-to-end can be run
later by feeding Task A's predicted spans.

  uv run python train_linking.py --base large-caselaw --device cuda
Train = silver `train` records (eyecite coref clusters), selection on silver
`val` (B³ F1), final report on human-verified `gold_dev` → `best/coref_test.json`.
`gold_test` is never read.
"""

from __future__ import annotations

import argparse
import json
import os

import torch
from torch.utils.data import Dataset
from transformers import (
    AutoTokenizer, Trainer, TrainerCallback, TrainingArguments)

import common as C
from common import N_FEATS, build_candidates, mention_list
from linking_model import AntecedentLinker
from metrics import coref_report


class LinkDataset(Dataset):
    """One item = one citing case (prepared python structure)."""

    def __init__(self, records, feats, tokenizer, ctx, lwin, K):
        self.tok = tokenizer
        self.ctx, self.lwin, self.K = ctx, lwin, K
        self.cases = []
        cases = C.by_case(records)
        for cid, ops in cases.items():
            ms = mention_list(ops, feats)
            if not ms:
                continue
            self.cases.append(self._prep(cid, ops, ms))

    def _prep(self, cid, ops, ms):
        texts = {oi: o["text"] for oi, o in enumerate(ops)}
        windows = []
        for m in ms:
            t = texts[m["opinion_idx"]]
            w0 = max(0, m["start"] - self.ctx)
            sub = t[w0:m["end"] + self.ctx]
            rs, re_ = m["start"] - w0, m["end"] - w0
            enc = self.tok(sub, truncation=True, max_length=self.lwin,
                           return_offsets_mapping=True)
            mm = [1 if (ce > cs and cs < re_ and ce > rs) else 0
                  for cs, ce in enc["offset_mapping"]]
            if not any(mm):              # mention truncated out — pool token 1
                mm = [0] * len(enc["input_ids"])
                if len(mm) > 1:
                    mm[1] = 1
                elif mm:
                    mm[0] = 1
            windows.append({"input_ids": enc["input_ids"],
                            "attention_mask": enc["attention_mask"],
                            "mention_mask": mm})
        return {"cid": cid, "ms": ms, "windows": windows,
                "gold": [m["gold"] for m in ms],
                "kind": [m["kind"] for m in ms],
                "source": [m["source"] for m in ms]}

    def __len__(self):
        return len(self.cases)

    def __getitem__(self, i):
        return self.cases[i]


class Collator:
    """Collate ONE case (batch_size=1) into [M, ...] tensors."""

    def __init__(self, K):
        self.K = K

    def __call__(self, batch):
        case = batch[0]
        ms, wins = case["ms"], case["windows"]
        M = len(ms)
        L = max(len(w["input_ids"]) for w in wins)
        ids = torch.zeros(M, L, dtype=torch.long)
        am = torch.zeros(M, L, dtype=torch.long)
        mm = torch.zeros(M, L, dtype=torch.long)
        for r, w in enumerate(wins):
            n = len(w["input_ids"])
            ids[r, :n] = torch.tensor(w["input_ids"])
            am[r, :n] = torch.tensor(w["attention_mask"])
            mm[r, :n] = torch.tensor(w["mention_mask"])
        cand, feats, gold_ante, gold_dummy = build_candidates(ms, self.K)
        return {"input_ids": ids, "attention_mask": am, "mention_mask": mm,
                "cand_idx": torch.tensor(cand, dtype=torch.long),
                "pair_feats": torch.tensor(feats, dtype=torch.float),
                "gold_ante": torch.tensor(gold_ante, dtype=torch.float),
                "gold_dummy": torch.tensor(gold_dummy, dtype=torch.float)}


def eval_coref(model, ds, coll):
    """Cluster every case in ``ds`` (union-find over argmax antecedents) and
    score B³ + attachment. Used both for per-epoch val selection and the final
    held-out test pass."""
    dev = next(model.parameters()).device
    was_training = model.training
    model.eval()
    g, p, k, s = [], [], [], []
    for case in ds.cases:
        batch = coll([case])
        batch = {kk: v.to(dev) for kk, v in batch.items()}
        pred = model.predict_clusters(batch)
        g.append(case["gold"]); p.append(pred)
        k.append(case["kind"]); s.append(case["source"])
    rep = coref_report(g, p, k, s)
    if was_training:
        model.train()
    return rep


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="base-dapt")
    ap.add_argument("--train-split", default="train")
    ap.add_argument("--val-split", default="val")
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
    ap.add_argument("--grad-checkpoint", action="store_true",
                    help="gradient checkpointing on the encoder (memory save "
                         "for --base large / big cases)")
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--device", choices=["mps", "cpu", "cuda"], default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--init", default=None,
                    help="warm start: a trained linker state (runs/<name>/best/model.pt) loaded over the "
                         "--base-initialised module before training")
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
    train_ds = LinkDataset(train_recs, feats, tok, args.ctx, args.lwin, args.cand)
    val_ds = LinkDataset(val_recs, feats, tok, args.ctx, args.lwin, args.cand)
    test_ds = LinkDataset(test_recs, feats, tok, args.ctx, args.lwin, args.cand)
    print(f"{len(train_ds)} train / {len(val_ds)} val / {len(test_ds)} {args.test_split} cases")

    attn = "sdpa" if args.device in ("mps", "cpu") else None
    model = AntecedentLinker(base_id, N_FEATS, attn_impl=attn)
    if args.init:
        init_pt = args.init if args.init.endswith(".pt") else os.path.join(args.init, "model.pt")
        model.load_state_dict(torch.load(init_pt, map_location="cpu"))
        print(f"warm start from {init_pt}")
    if args.grad_checkpoint:
        model.encoder.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False})
    coll = Collator(args.cand)

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
        cb = CorefEvalCallback(trainer, val_ds, coll, out)
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
        test_rep = eval_coref(model, test_ds, coll)
        (out / "best" / "coref_test.json").write_text(
            json.dumps(test_rep, indent=2))
        print(f"\nbest val B3 F1 = {cb.best:.3f}  |  "
              f"held-out test B3 F1 = {test_rep['b3_f1']:.3f} "
              f"(saved to {out / 'best'})")
    else:
        print("smoke run finished OK")


if __name__ == "__main__":
    main()
