"""Eval metrics for both tasks.

Extraction (Task A): span-level P/R/F1 vs gold (exact char-span match — pred
and gold share the same char frame), plus recall on the eyecite-missed
(manual) residual and the rate at which removed-negative spans get wrongly
tagged. The headline floor to beat is the eyecite F1 from
`../baselines_citation.md`.

Coreference (Task B): B³ P/R/F1 and the task-practical attachment rate
(non-first mentions placed in the correct cluster), overall and by surface
kind. Mirrors `../baselines_citation.py` so model and baseline are scored the
same way.
"""

from __future__ import annotations

from collections import defaultdict


def extraction_report(pred_list, gold_list, manual_list, neg_list):
    tp = fp = fn = 0
    man_hit = man_tot = 0
    neg_hit = neg_tot = 0
    for pred, gold, man, neg in zip(pred_list, gold_list, manual_list, neg_list):
        tp += len(pred & gold)
        fp += len(pred - gold)
        fn += len(gold - pred)
        man_hit += len(man & pred)
        man_tot += len(man)
        neg_hit += len(neg & pred)
        neg_tot += len(neg)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "span_precision": prec, "span_recall": rec, "span_f1": f1,
        "manual_recall": man_hit / man_tot if man_tot else 0.0,
        "neg_tagged_rate": neg_hit / neg_tot if neg_tot else 0.0,
        "tp": float(tp), "fp": float(fp), "fn": float(fn),
    }


def b3_case(gold, pred):
    """Un-normalized B³ precision/recall sums for one case's mentions
    (caller divides by total mention count). gold/pred are cluster-id lists,
    index-aligned over the case's mentions in doc order."""
    gmap, pmap = defaultdict(set), defaultdict(set)
    for i, g in enumerate(gold):
        gmap[g].add(i)
    for i, p in enumerate(pred):
        pmap[p].add(i)
    ps = rs = 0.0
    for i in range(len(gold)):
        g, p = gmap[gold[i]], pmap[pred[i]]
        inter = len(g & p)
        ps += inter / len(p)
        rs += inter / len(g)
    return ps, rs


def coref_report(cases_gold, cases_pred, cases_kind=None, cases_source=None):
    """cases_* are lists (one entry per case) of per-mention lists in doc
    order. Returns B³ + attachment overall and by kind/source."""
    tot = 0
    p_sum = r_sum = 0.0
    attach = defaultdict(lambda: [0, 0])
    for ci, (gold, pred) in enumerate(zip(cases_gold, cases_pred)):
        if not gold:
            continue
        ps, rs = b3_case(gold, pred)
        p_sum += ps
        r_sum += rs
        tot += len(gold)
        first = {}
        for i, g in enumerate(gold):
            first.setdefault(g, i)
        kinds = cases_kind[ci] if cases_kind else [None] * len(gold)
        srcs = cases_source[ci] if cases_source else [None] * len(gold)
        for i, g in enumerate(gold):
            if i == first[g]:
                continue
            prior = [j for j in range(i) if gold[j] == g]
            ok = any(pred[j] == pred[i] for j in prior)
            for b in ("all", kinds[i], srcs[i]):
                if b is None:
                    continue
                attach[b][0] += ok
                attach[b][1] += 1
    bp = p_sum / tot if tot else 0.0
    br = r_sum / tot if tot else 0.0
    return {
        "b3_precision": bp, "b3_recall": br,
        "b3_f1": 2 * bp * br / (bp + br) if bp + br else 0.0,
        "n_mentions": tot,
        "attach": {k: {"correct": v[0], "total": v[1],
                       "rate": v[0] / v[1] if v[1] else 0.0}
                   for k, v in sorted(attach.items())},
    }
