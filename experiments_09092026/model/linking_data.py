"""Linking data pipeline — used at INFERENCE as well as training.

Everything needed to turn records + mention features into batches the
AntecedentLinker can score, plus the `Id.` decode rule:

  id_rule_slots   the `Id.` -> nearest-mention rule (with the parenthetical
                  skip). Produces the per-mention forced-slot vector that
                  AntecedentLinker.predict_clusters consumes. Without it the
                  linker decodes by argmax alone and scores ~1 point lower.
  LinkDataset     one item per citing case: mention windows, candidates, feats
  Collator        pads a case into a batch
  eval_coref      cluster a dataset and score B3 + attachment
  dummy_mentions  training-only span injection (round 3 experiment)

Split out of train_linking.py so that inference does not import the trainer.
"""

from __future__ import annotations

import random
import re

import torch
from torch.utils.data import Dataset

import common as C
from common import build_candidates, mention_list, n_feats
from metrics import coref_report


DUMMY_SOURCE = "regex_dummy"
DUMMY_RE = re.compile(r"\b(?:Id\.|Ibid\.|id\.|ibid\.|supra)")


def dummy_mentions(ops, ms):
    """Short-form citation signals (`Id.`, `Ibid.`, `supra`) the correction pass
    left untagged because they carry no case antecedent — they follow a statute,
    a record cite (`Compl. ¶ 12`), or an internal cross-reference (`Part II-A,
    supra`). Injected as mentions with a cluster id of their own, so
    build_candidates marks them gold_dummy=1 and the linker trains on "no
    antecedent" as a real answer instead of never seeing the pattern (eyecite
    #348/#349 are the same failure this guards against). The identical
    injection runs at inference; the phantoms are dropped before anything is
    scored or reported."""
    taken = [[] for _ in ops]
    for m in ms:
        taken[m["opinion_idx"]].append((m["start"], m["end"]))
    out = []
    for oi, o in enumerate(ops):
        for mt in DUMMY_RE.finditer(o["text"]):
            s, e = mt.start(), mt.end()
            if any(s < te and e > ts for ts, te in taken[oi]):
                continue
            is_supra = mt.group(0).lower().startswith("supra")
            out.append({
                "text": mt.group(0), "opinion_idx": oi, "start": s, "end": e,
                "gold": f"__dummy_{oi}_{s}", "source": DUMMY_SOURCE,
                "kind": "supra" if is_supra else "id",
                "names": set(), "rep": None,
                "is_id": not is_supra, "is_supra": is_supra, "eye": None,
            })
    return out


# Signal words that open an explanatory parenthetical whose citation an `Id.`
# should NOT attach to (eyecite #348): the Id. refers to the case the
# parenthetical modifies.
PAREN_SIGNAL = re.compile(r"\((?:citing|quoting|see(?: also)?|cf\.|accord|compare|e\.g\.,?)\s", re.I)


def id_rule_slots(ms, texts):
    """Per mention: the candidate slot an `Id.`/`Ibid.` is forced to at decode
    (0 = the mention immediately before it), or -1 to keep the model's choice.
    On gold_dev the nearest prior mention is the antecedent of 294/312 (94.2%)
    `Id.`s, and the learned linker under-trusts it — 17 of its 33 Id. errors
    reached past a correct slot 0 to a distant look-alike (`is_id`+`is_nearest`
    were both 1). Exception: when the preceding citation sits inside a signal
    parenthetical that closed before the Id., skip every mention inside that
    parenthetical and take the one before it. Phantoms and cross-writing
    Id.s are left to the model."""
    out = [-1] * len(ms)
    for i, m in enumerate(ms):
        if i == 0 or m["kind"] != "id" or m["source"] == DUMMY_SOURCE:
            continue
        prev = ms[i - 1]
        if prev["opinion_idx"] != m["opinion_idx"]:
            continue
        t = texts[m["opinion_idx"]]
        between = t[prev["end"]:m["start"]]
        j = i - 1
        if between.count(")") > between.count("("):
            head_start = max(0, prev["start"] - 60)
            hits = list(PAREN_SIGNAL.finditer(t[head_start:prev["start"]]))
            if hits:
                open_pos = head_start + hits[-1].start()
                while (j >= 0 and ms[j]["opinion_idx"] == m["opinion_idx"]
                       and ms[j]["start"] > open_pos):
                    j -= 1
                if j < 0 or ms[j]["opinion_idx"] != m["opinion_idx"]:
                    continue
        out[i] = i - 1 - j
    return out


class LinkDataset(Dataset):
    """One item = one citing case (prepared python structure)."""

    def __init__(self, records, feats, tokenizer, ctx, lwin, K, inject_dummies=False,
                 max_mentions=700):
        self.tok = tokenizer
        self.ctx, self.lwin, self.K = ctx, lwin, K
        self.cases = []
        self.n_win = self.n_truncated = 0   # lwin too small for ctx => wasted context
        n_dummy = 0
        skipped = []
        cases = C.by_case(records)
        for cid, ops in cases.items():
            ms = mention_list(ops, feats)
            if not ms:
                continue
            if max_mentions and len(ms) > max_mentions:
                # Chunking bounds the peak inside one encoder call, but training
                # still retains every chunk's activations until backward, so a
                # 6,122-mention consolidated opinion OOMs regardless. Capping on
                # REAL mentions (before injection) keeps the trained case set
                # identical to round 2's for a clean comparison.
                skipped.append((cid, len(ms)))
                continue
            if inject_dummies:
                extra = dummy_mentions(ops, ms)
                n_dummy += len(extra)
                ms = sorted(ms + extra,
                            key=lambda x: (x["opinion_idx"], x["start"]))
            self.cases.append(self._prep(cid, ops, ms))
        if skipped:
            print(f"  [LinkDataset] skipped {len(skipped)} case(s) over "
                  f"{max_mentions} real mentions: {skipped}")
        if n_dummy:
            print(f"  [LinkDataset] injected {n_dummy} no-antecedent mentions "
                  f"across {len(self.cases)} cases")
        if self.n_win:
            print(f"  [LinkDataset] {self.n_truncated}/{self.n_win} windows hit "
                  f"the lwin={self.lwin} cap ({self.n_truncated / self.n_win:.1%}) "
                  f"at ctx={self.ctx}")

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
            self.n_win += 1
            self.n_truncated += len(enc["input_ids"]) >= self.lwin
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
                "id_rule": id_rule_slots(ms, texts),
                "gold": [m["gold"] for m in ms],
                "kind": [m["kind"] for m in ms],
                "source": [m["source"] for m in ms]}

    def __len__(self):
        return len(self.cases)

    def __getitem__(self, i):
        return self.cases[i]


class Collator:
    """Collate ONE case (batch_size=1) into [M, ...] tensors.

    ``eyecite`` adds the three eyecite-grouping pair features (round 5).
    ``eye_dropout`` masks the whole channel for a random share of cases so
    the linker also learns to link WITHOUT eyecite (production may run the
    encoder alone); 1.0 masks every case = the "no eyecite" eval condition."""

    def __init__(self, K, eyecite=False, eye_dropout=0.0):
        self.K, self.eyecite, self.eye_dropout = K, eyecite, eye_dropout

    def __call__(self, batch):
        case = batch[0]
        ms, wins = case["ms"], case["windows"]
        if self.eyecite and self.eye_dropout > 0 and random.random() < self.eye_dropout:
            ms = [{**m, "eye": None} for m in ms]
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
        cand, feats, gold_ante, gold_dummy = build_candidates(ms, self.K, self.eyecite)
        return {"input_ids": ids, "attention_mask": am, "mention_mask": mm,
                "id_rule": torch.tensor(case["id_rule"], dtype=torch.long),
                "cand_idx": torch.tensor(cand, dtype=torch.long),
                "pair_feats": torch.tensor(feats, dtype=torch.float),
                "gold_ante": torch.tensor(gold_ante, dtype=torch.float),
                "gold_dummy": torch.tensor(gold_dummy, dtype=torch.float)}


def eval_coref(model, ds, coll, id_rule=True):
    """Cluster every case in ``ds`` (union-find over argmax antecedents, with
    the Id.->nearest decode rule unless id_rule=False) and score B³ +
    attachment. Used both for per-epoch val selection and the final held-out
    test pass."""
    dev = next(model.parameters()).device
    was_training = model.training
    model.eval()
    g, p, k, s = [], [], [], []
    for case in ds.cases:
        batch = coll([case])
        batch = {kk: v.to(dev) for kk, v in batch.items()}
        pred = model.predict_clusters(batch, id_rule=id_rule)
        # Injected no-antecedent phantoms are scaffolding, not predictions to
        # score: dropping them keeps B3 / attach comparable across rounds.
        keep = [i for i, src in enumerate(case["source"]) if src != DUMMY_SOURCE]
        g.append([case["gold"][i] for i in keep])
        p.append([pred[i] for i in keep])
        k.append([case["kind"][i] for i in keep])
        s.append([case["source"][i] for i in keep])
    rep = coref_report(g, p, k, s)
    if was_training:
        model.train()
    return rep
