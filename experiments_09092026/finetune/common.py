"""Shared data + alignment helpers for the citation extraction + coref
finetune (experiments_09092026: eyecite-seeded silver training, gold eval;
ported from experiments_06142026/finetune). Two SEPARATE models (decided): an extraction token
classifier (Task A) and an antecedent-ranking linker (Task B), both on the
same ModernBERT backbone choices. This module holds everything that is pure
data wrangling — kept tokenizer-agnostic and unit-testable.

Data in:  ../data/output/citations.jsonl       (built by ../build_dataset.py)
          ../data/output/mention_features.jsonl (built by ../mention_features.py)
Splits are record tags: train | val (silver, eyecite labels) | gold_dev |
gold_test (human-verified). Training selects on val, reports on gold_dev;
gold_test is never touched by this code.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from pathlib import Path

from transformers import TrainingArguments

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
# CIT_JSONL / CIT_FEATS point the trainers at another build (e.g. the round-2
# citations_r2.jsonl) without renaming files; the pod bundle stages the chosen
# build under the canonical names so nothing needs setting there.
JSONL = Path(os.environ.get("CIT_JSONL", EXP / "data" / "output" / "citations.jsonl"))
FEATS = Path(os.environ.get("CIT_FEATS", EXP / "data" / "output" / "mention_features.jsonl"))

# Backbone choices. `base-dapt` = the legal-domain DAPT trunk from
# flp/encoder_finetune (continue-pretrained ModernBERT-base); override
# DAPT_PATH via env/CLI if the checkpoint lives elsewhere on the pod.
DAPT_PATH = (EXP.parent.parent / "encoder_finetune" / "runs" / "dapt_base"
             / "best")
BASES = {
    "base": "answerdotai/ModernBERT-base",
    "large": "answerdotai/ModernBERT-large",
    "large-caselaw": "ai-law-society-lab/CaseLawModernBERT-large",  # continue-pretrained on case law (default)
    "base-dapt": str(DAPT_PATH),
}


def warmup_kwargs(ratio: float = 0.1) -> dict:
    """TrainingArguments warmup as a fraction of total steps, across
    transformers 4.x (`warmup_ratio`) and 5.x (removed; a float `warmup_steps`
    in [0, 1) now means a ratio)."""
    names = {f.name for f in dataclasses.fields(TrainingArguments)}
    return ({"warmup_ratio": ratio} if "warmup_ratio" in names
            else {"warmup_steps": ratio})


# extraction label scheme (BIO over CITATION spans)
ID2LABEL = {0: "O", 1: "B-CIT", 2: "I-CIT"}
LABEL2ID = {v: k for k, v in ID2LABEL.items()}


# ---------------- loading ----------------
def load_records(path=JSONL):
    """One record per rendered opinion (see build_dataset.py)."""
    return [json.loads(ln) for ln in open(path) if ln.strip()]


def by_case(records):
    """{citing_cluster_id: [opinion records in doc order]}."""
    out = {}
    for r in records:
        out.setdefault(r["citing_cluster_id"], []).append(r)
    return out


def load_features(path=FEATS):
    """{(cid, opinion_idx, start): feature dict}."""
    out = {}
    if not Path(path).exists():
        return out
    for ln in open(path):
        if not ln.strip():
            continue
        f = json.loads(ln)
        out[(f["citing_cluster_id"], f["opinion_idx"], f["start"])] = f
    return out


# ---------------- splits (record tags) ----------------
def select(records, split, max_docs=None, seed=42):
    """Records whose ``split`` tag matches (``split`` may be a comma-separated
    list, e.g. "train_gpt,train_llm"); optional deterministic cap on the
    number of citing clusters (shuffled by a fixed seed, whole clusters kept)."""
    wanted = {s.strip() for s in split.split(",") if s.strip()}
    recs = [r for r in records if r.get("split") in wanted]
    if max_docs is None:
        return recs
    cids = sorted({r["citing_cluster_id"] for r in recs})
    state = seed & 0xFFFFFFFF
    for i in range(len(cids) - 1, 0, -1):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        j = state % (i + 1)
        cids[i], cids[j] = cids[j], cids[i]
    keep = set(cids[:max_docs])
    return [r for r in recs if r["citing_cluster_id"] in keep]


# ---------------- extraction: char spans -> token BIO ----------------
def align_bio(offsets, spans):
    """Map a window's token ``offsets`` (list of (char_start, char_end),
    char offsets RELATIVE TO THE WINDOW TEXT; specials are (0,0)) to BIO
    label ids using mention ``spans`` (list of (start, end), same frame).

    Pure + tokenizer-free so it is unit-testable. A token gets B-CIT at the
    first span-overlapping token, I-CIT after; specials/empties get -100;
    everything else O.
    """
    labels = [0] * len(offsets)
    for (s, e) in sorted(spans):
        started = False
        for ti, (cs, ce) in enumerate(offsets):
            if ce <= cs:           # special / empty token
                continue
            if cs >= e:            # past the span (offsets ascending)
                break
            if ce <= s:            # before the span
                continue
            labels[ti] = LABEL2ID["I-CIT"] if started else LABEL2ID["B-CIT"]
            started = True
    for ti, (cs, ce) in enumerate(offsets):
        if ce <= cs:
            labels[ti] = -100
    return labels


def spans_in_window(mentions, w_start, w_end):
    """Mentions fully inside [w_start, w_end), re-based to window coords.
    Returns [(rel_start, rel_end, mention), ...]. Mentions straddling the
    window edge are dropped (the overlapping next window will hold them)."""
    out = []
    for m in mentions:
        if m["start"] >= w_start and m["end"] <= w_end:
            out.append((m["start"] - w_start, m["end"] - w_start, m))
    return out


# ---------------- linking: mentions, features, candidates (torch-free) ----
N_FEATS = 7


def mention_list(ops, feats):
    """Doc-ordered mentions for one citing case with derived features
    (joined from mention_features.jsonl by (cid, opinion_idx, start))."""
    ms = []
    for oi, o in enumerate(ops):
        for m in o["mentions"]:
            f = feats.get((o["citing_cluster_id"], oi, m["start"]), {})
            ms.append({
                "text": m["text"], "opinion_idx": oi,
                "start": m["start"], "end": m["end"],
                "gold": m["cluster"], "source": m["source"],
                "kind": f.get("kind", ""),
                "names": set(f.get("name_tokens", [])),
                "rep": f.get("reporter_key"),
                "is_id": bool(f.get("is_id")),
                "is_supra": bool(f.get("is_supra")),
            })
    ms.sort(key=lambda x: (x["opinion_idx"], x["start"]))
    return ms


def pair_features(mi, mj, i, j):
    """Legal pair features for mention i and candidate antecedent j (i>j)."""
    return [
        min(len(mi["names"] & mj["names"]), 5) / 5.0,
        1.0 if (mi["rep"] and mi["rep"] == mj["rep"]) else 0.0,
        1.0 if j == i - 1 else 0.0,
        1.0 if mi["is_id"] else 0.0,
        1.0 if mi["is_supra"] else 0.0,
        1.0 if mi["opinion_idx"] == mj["opinion_idx"] else 0.0,
        math.log1p(i - j) / 5.0,
    ]


def build_candidates(ms, K):
    """Per-mention antecedent candidates (nearest-first, capped at K), their
    pair features, and gold markers. Plain python so it is unit-testable;
    the collator tensorizes the result.

    Returns (cand, feats, gold_ante, gold_dummy):
      cand[i]      list[K] of antecedent index j (slot 0 = i-1), -1 = pad
      feats[i]     list[K] of N_FEATS floats
      gold_ante[i] list[K] of 0/1 (1 = j shares i's gold cluster)
      gold_dummy[i] 1.0 if no in-window candidate is gold, else 0.0
    """
    M = len(ms)
    cand = [[-1] * K for _ in range(M)]
    feats = [[[0.0] * N_FEATS for _ in range(K)] for _ in range(M)]
    gold_ante = [[0.0] * K for _ in range(M)]
    gold_dummy = [1.0] * M
    for i in range(M):
        cands = list(range(max(0, i - K), i))
        for slot, j in enumerate(reversed(cands)):   # slot 0 = nearest (i-1)
            cand[i][slot] = j
            feats[i][slot] = pair_features(ms[i], ms[j], i, j)
            if ms[j]["gold"] == ms[i]["gold"]:
                gold_ante[i][slot] = 1.0
        if any(gold_ante[i]):
            gold_dummy[i] = 0.0
    return cand, feats, gold_ante, gold_dummy


def trim_span(text, s, e):
    """Strip leading/trailing whitespace from a [s,e) char span so BPE-decoded
    spans (whose first token's offset includes the leading space, e.g. ' 304')
    align with gold spans, which are tightened to non-space content. Returns
    (s, e) or None if empty after trimming."""
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return (s, e) if e > s else None


def decode_spans(labels, offsets):
    """Inverse of align_bio: BIO label ids + window offsets -> list of
    (char_start, char_end) spans in window coords. Used at inference."""
    spans = []
    cur = None
    for lab, (cs, ce) in zip(labels, offsets):
        if ce <= cs:
            continue
        name = ID2LABEL.get(int(lab), "O")
        if name == "B-CIT":
            if cur:
                spans.append(cur)
            cur = [cs, ce]
        elif name == "I-CIT" and cur:
            cur[1] = ce
        else:
            if cur:
                spans.append(cur)
            cur = None
    if cur:
        spans.append(cur)
    return [tuple(s) for s in spans]
