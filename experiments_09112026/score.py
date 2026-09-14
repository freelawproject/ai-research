"""Score a gate run (and optionally a labeler run chained to it) against the expert gold.

  bash run.sh score.py --gate g_kimi_dev
  bash run.sh score.py --label l_son_dev                 # gate inferred from the label run's meta
  bash run.sh score.py --gate g_kimi_dev --label l_son_dev --split dev --baseline 0529_sonnet

Scope: every gold pair (citing, cited) with a final treatment in the run's clusters, EXCLUDING
pairs whose final or any expert vote is a direct-history label, and pairs whose final is an
"as recognized by" label. Two truths per pair: MAJORITY = the benchmark's final treatment;
ANY-EXPERT = the set of expert votes (a prediction counts if any expert gave it).
Gold pairs are tied to inventory ids by cited cluster id, then by normalized citation;
pairs that match no inventory entry are reported as extraction misses and not scored.
"""
import argparse
import collections
import csv
import json
import os

from common import (CITED, RUNS, SEVERITY, base, cluster_ids, is_cr, is_dh, is_recognized, load_baseline_predictions,
                    load_gold, load_splits, read_json, separate_opinion)
from build_inputs import inventory
from goldmap import resolve_gold_to_inventory
from records import CONF_RANK

THRESHOLDS = ["low", "medium", "high"]


def prf(pairs):
    """{label: (f1, p, r, support)} over (pred, gold) pairs."""
    tp, fp, fn = collections.Counter(), collections.Counter(), collections.Counter()
    for p, g in pairs:
        if p == g:
            tp[g] += 1
        else:
            fp[p] += 1
            fn[g] += 1
    out = {}
    for c in set(tp) | set(fp) | set(fn):
        pr = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0.0
        rc = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else 0.0
        out[c] = (2 * pr * rc / (pr + rc) if pr + rc else 0.0, pr, rc, tp[c] + fn[c])
    return out


def macro_f1(f, min_support=20):
    cs = [c for c, v in f.items() if v[3] >= min_support]
    return (sum(f[c][0] for c in cs) / len(cs) if cs else 0.0), len(cs)


def binary(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def run_outputs(name):
    """Outputs of one run, or the union of several (comma-separated gate runs): per cluster the
    flagged ids with the HIGHEST confidence any run gave them."""
    names = [n for n in (name or "").split(",") if n]
    merged = {}
    for n in names:
        d = os.path.join(RUNS, n, "outputs")
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".json"):
                continue
            o = read_json(os.path.join(d, f))
            cid = f[:-5]
            if "flagged" not in o:                       # labeler outputs: single run only
                merged[cid] = o
                continue
            cur = merged.setdefault(cid, {"cid": cid, "onAppeal": [], "flagged": []})
            cur["onAppeal"] = sorted(set(cur["onAppeal"]) | set(o.get("onAppeal", [])))
            best = {x["id"]: x for x in cur["flagged"]}
            for x in o["flagged"]:
                if x["id"] not in best or CONF_RANK[x["confidence"]] > CONF_RANK[best[x["id"]]["confidence"]]:
                    best[x["id"]] = x
            cur["flagged"] = [best[k] for k in sorted(best)]
    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", help="gate run, or comma-separated gate runs to union")
    ap.add_argument("--label")
    ap.add_argument("--split", choices=["dev", "test", "all"])
    ap.add_argument("--baseline", default="0529_sonnet", help="prediction source in the benchmark ledger ('' to skip)")
    ap.add_argument("--out", help="score.json path (default: the label run dir, else the gate run dir)")
    ap.add_argument("--dissent-as-cited", action="store_true",
                    help="convention: a treatment whose governing opinion is a dissent or concurrence counts as "
                         "Cited by (applied to the labeler AND the baseline)")
    a = ap.parse_args()

    label_meta = read_json(os.path.join(RUNS, a.label, "meta.json")) if a.label else None
    gate_name = a.gate or (label_meta or {}).get("from_gate")
    if not gate_name and not (label_meta and label_meta.get("from_gold")):
        ap.error("--gate is required (or a --label run prepared with --from-gate)")
    gate_meta = read_json(os.path.join(RUNS, gate_name.split(",")[0], "meta.json")) if gate_name else None
    split = a.split or (gate_meta or label_meta)["split"]
    cids = [c for c in (gate_meta or label_meta)["cids"] if split == "all" or load_splits().get(c) == split]
    label_conf = (label_meta or {}).get("min_confidence", "low")

    gold = load_gold(cids)
    gate_out = run_outputs(gate_name) if gate_name else {}
    label_out = run_outputs(a.label) if a.label else {}
    baseline = load_baseline_predictions(a.baseline, with_opinion_type=True) if a.baseline else None

    def lab_treatment(lab):
        """The labeler's treatment under the chosen convention."""
        if lab is None:
            return None
        if a.dissent_as_cited and separate_opinion(lab.get("opinionType")):
            return CITED
        return lab["treatment"]

    pairs, excluded, raw_inv, raw_flag = [], collections.Counter(), 0, collections.Counter()
    for cid in cids:
        inv = inventory(cid)
        rows = gold.get(cid, [])
        mp = resolve_gold_to_inventory(inv, rows)
        go = gate_out.get(cid)
        conf = {f["id"]: f["confidence"] for f in go["flagged"]} if go else {}
        if gate_meta and gate_meta.get("from_gold"):
            conf = {}
        oracle = label_meta.get("flagged", {}).get(cid, []) if label_meta and label_meta.get("from_gold") else None
        labels = {c["id"]: c for c in label_out[cid]["citedCases"]} if cid in label_out else {}
        raw_inv += len(inv)
        for t in THRESHOLDS:
            raw_flag[t] += sum(1 for c in conf.values() if CONF_RANK[c] >= CONF_RANK[t])
        for i, r in enumerate(rows):
            if is_dh(r["final"]) or any(is_dh(v) for v in r["votes"]):
                excluded["direct history"] += 1
                continue
            if is_recognized(r["final"]):
                excluded["recognized-only final"] += 1
                continue
            inv_id = mp.get(i)
            votes = [base(v) for v in r["votes"]]
            pairs.append({
                "cid": cid, "inv_id": inv_id, "cited": r["cited_cluster_id"], "name": r["cited_name"],
                "final": r["final"], "final_source": r["final_source"], "votes": votes,
                "pos_major": is_cr(r["final"]), "pos_any": any(is_cr(v) for v in votes),
                "conf": conf.get(inv_id) if inv_id is not None else None,
                "oracle_flag": (inv_id in oracle) if oracle is not None and inv_id is not None else None,
                "label": labels.get(inv_id) if inv_id is not None else None,
                "gate_out": go is not None,
            })

    report = {"gate_run": gate_name, "label_run": a.label, "split": split, "clusters": len(cids),
              "clusters_with_gate_output": sum(1 for c in cids if c in gate_out)}
    unmatched = [p for p in pairs if p["inv_id"] is None]
    scored = [p for p in pairs if p["inv_id"] is not None]
    report["scope"] = {
        "gold_pairs_in_scope": len(pairs), "excluded": dict(excluded),
        "unmatched_to_inventory": len(unmatched),
        "unmatched_gold_positives_major": sum(p["pos_major"] for p in unmatched),
        "unmatched_gold_positives_any": sum(p["pos_any"] for p in unmatched),
        "scored_pairs": len(scored), "positives_major": sum(p["pos_major"] for p in scored),
        "positives_any": sum(p["pos_any"] for p in scored),
        "inventory_ids_total": raw_inv,
    }

    # ── gate ──
    if gate_meta and not gate_meta.get("from_gold"):
        g = {}
        for t in THRESHOLDS:
            fl = [p for p in scored if p["conf"] is not None and CONF_RANK[p["conf"]] >= CONF_RANK[t]]
            flagged = set(id(p) for p in fl)
            tp = sum(p["pos_major"] for p in fl)
            fn = sum(p["pos_major"] for p in scored if id(p) not in flagged)
            tp_any = sum(p["pos_any"] for p in fl)
            fn_any = sum(p["pos_any"] for p in scored if id(p) not in flagged)
            ops_flagged = {p["cid"] for p in fl} | {c for c in cids if c in gate_out and any(
                CONF_RANK[f["confidence"]] >= CONF_RANK[t] for f in gate_out[c]["flagged"])}
            skipped = [c for c in cids if c in gate_out and c not in ops_flagged]
            g[t] = {
                "vs_majority": binary(tp, len(fl) - tp, fn),
                "vs_any_expert": binary(tp_any, len(fl) - tp_any, fn_any),
                "flagged_pairs": len(fl), "flagged_fraction_of_scored_pairs": round(len(fl) / len(scored), 3) if scored else 0,
                "flagged_inventory_ids": raw_flag[t],
                "flagged_fraction_of_inventory": round(raw_flag[t] / raw_inv, 3) if raw_inv else 0,
                "opinions_skipped": len(skipped), "opinions_with_flags": len(ops_flagged),
                "positives_major_in_skipped_opinions": sum(p["pos_major"] for p in scored if p["cid"] in skipped),
                "positives_any_in_skipped_opinions": sum(p["pos_any"] for p in scored if p["cid"] in skipped),
            }
        report["gate"] = g

    # ── labeler ──
    if a.label:
        if label_meta.get("from_gold"):
            fl = [p for p in scored if p["oracle_flag"]]
        else:
            fl = [p for p in scored if p["conf"] is not None and CONF_RANK[p["conf"]] >= CONF_RANK[label_conf]]
        labeled = [p for p in fl if p["label"] is not None]
        pg = [(lab_treatment(p["label"]), p["final"]) for p in labeled]
        f = prf(pg)
        conf_pairs = collections.Counter((gld, prd) for prd, gld in pg if prd != gld)
        others = [{"cid": p["cid"], "inv_id": p["inv_id"], "name": p["name"], "gold": p["final"], "votes": p["votes"],
                   "label": p["label"]["otherTreatmentLabel"], "rationale": p["label"]["rationale"][:200]}
                  for p in labeled if lab_treatment(p["label"]) == "Other treatment"]
        report["labeler"] = {
            "flagged_pairs_scored": len(fl), "labeled": len(labeled), "flagged_without_label": len(fl) - len(labeled),
            "exact_vs_majority": round(sum(prd == gld for prd, gld in pg) / len(pg), 3) if pg else None,
            "match_any_expert": round(sum(lab_treatment(p["label"]) in p["votes"] for p in labeled) / len(labeled), 3) if labeled else None,
            "severity_tier_vs_majority": round(sum(SEVERITY.get(prd) == SEVERITY.get(gld) for prd, gld in pg) / len(pg), 3) if pg else None,
            "on_gold_positives_only": {
                "n": sum(p["pos_major"] for p in labeled),
                "exact": round(sum(lab_treatment(p["label"]) == p["final"] for p in labeled if p["pos_major"]) /
                               max(1, sum(p["pos_major"] for p in labeled)), 3),
                "any_expert": round(sum(lab_treatment(p["label"]) in p["votes"] for p in labeled if p["pos_major"]) /
                                    max(1, sum(p["pos_major"] for p in labeled)), 3),
            },
            "cited_by_returned_on_gate_false_positives": round(
                sum(lab_treatment(p["label"]) == CITED for p in labeled if not p["pos_major"]) /
                max(1, sum(not p["pos_major"] for p in labeled)), 3),
            "per_class": {c: {"f1": round(v[0], 3), "p": round(v[1], 3), "r": round(v[2], 3), "support": v[3]}
                          for c, v in sorted(f.items(), key=lambda x: -x[1][3])},
            "top_confusions_gold_to_pred": [f"{g} -> {p}: {n}" for (g, p), n in conf_pairs.most_common(10)],
            "other_treatment": others,
        }

        # ── end to end (unflagged = Cited by) ──
        def e2e(pred_of):
            pg2 = [(pred_of(p), p["final"]) for p in scored]
            f2 = prf(pg2)
            mf, k = macro_f1(f2)
            posm = [p for p in scored if p["pos_major"]]
            return {"accuracy_vs_majority": round(sum(a_ == b for a_, b in pg2) / len(pg2), 4),
                    "positives_recovered": round(sum(pred_of(p) != CITED for p in posm) / len(posm), 3) if posm else None,
                    "positives_exact": round(sum(pred_of(p) == p["final"] for p in posm) / len(posm), 3) if posm else None,
                    "accuracy_any_expert": round(sum(pred_of(p) in p["votes"] for p in scored) / len(scored), 4),
                    "macro_f1": round(mf, 3), "macro_f1_classes": k,
                    "negative_flag_rate_on_cited_by_pairs": round(
                        sum(pred_of(p) != CITED for p in scored if p["final"] == CITED) /
                        max(1, sum(p["final"] == CITED for p in scored)), 4),
                    "per_class": {c: {"f1": round(v[0], 3), "p": round(v[1], 3), "r": round(v[2], 3), "support": v[3]}
                                  for c, v in sorted(f2.items(), key=lambda x: -x[1][3]) if v[3] >= 5}}
        fl_ids = {id(p) for p in fl}
        report["end_to_end"] = e2e(lambda p: lab_treatment(p["label"]) if id(p) in fl_ids and p["label"] else CITED)
        report["convention"] = "dissent/concurrence-only treatments = Cited by" if a.dissent_as_cited else "as labeled"
        if baseline:
            def bl(p):
                t = baseline.get((p["cid"], p["cited"]))
                if not t:
                    return CITED
                if a.dissent_as_cited and separate_opinion(t[1]):
                    return CITED
                return base(t[0])
            cov = sum(1 for p in scored if (p["cid"], p["cited"]) in baseline)
            report["baseline_" + a.baseline] = dict(e2e(bl), pairs_with_prediction=cov)

    # ── cost ──
    cost = {}
    for n in (gate_name, a.label):
        s = read_json(os.path.join(RUNS, n, "summary.json")) if n else None
        if s:
            cost[n] = {"usd": s["cost_usd"], "rate": s["rate"], "in": s["input_tokens"], "out": s["output_tokens"],
                       "records": s["n_records"], "errors": s["n_errors"], "truncated": len(s["truncated"])}
    report["cost"] = cost

    out_dir = os.path.join(RUNS, a.label or gate_name.split(",")[0])
    out = a.out or os.path.join(out_dir, "score_dissent_as_cited.json" if a.dissent_as_cited else "score.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "pairs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["citing", "cited", "inv_id", "name", "final", "final_source", "votes", "pos_major", "pos_any",
                    "gate_confidence", "label", "other_label", "rationale"])
        for p in pairs:
            lab = p["label"] or {}
            w.writerow([p["cid"], p["cited"], p["inv_id"], p["name"], p["final"], p["final_source"], "; ".join(p["votes"]),
                        int(p["pos_major"]), int(p["pos_any"]), p["conf"] or "", lab.get("treatment", ""),
                        lab.get("otherTreatmentLabel") or "", (lab.get("rationale") or "")[:300]])
    print(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"\nwrote {out} and {os.path.join(out_dir, 'pairs.csv')}")


if __name__ == "__main__":
    main()
