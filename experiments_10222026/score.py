"""Score Jev one-shot labeler runs (label_v2: `applied` choice + one `rec:<label>` noul per
recognized label) on the 17 Dahl opinions.

  score.py --run jev_label_mention [--run jev_label_concat ...] [--flag-threshold 0.5] [--rec-threshold 0.5]

Per cited case the records are pooled: applied = the non-neutral choice with the highest confidence
across records (--pool confidence, default) or the most severe non-neutral choice (--pool severity);
recognized = every label whose noul ≥ --rec-threshold in any record (union); P(flag) = max over
records of 1 - P(Cited by) and of the negative-tier recognized nouls. Truths:
  * Dahl: 41 negative pairs (data/contexts/pairs.csv) → recall.
  * FLP benchmark applied finals for every gold pair of these opinions (0911 gate runs' pairs.csv:
    final, pos_any, pos_major) → binary recall / false-flag, applied label exact + tier.
  * FLP benchmark recognized finals ("X as recognized by", citator-benchmark most-negative export,
    joined by cited cluster id) → recognized detection + base-label exact.
Prints markdown tables; writes data/runs/{name}/pairs.csv.
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "data", "runs")
PAIRS = os.path.join(HERE, "data", "contexts", "pairs.csv")
PROMPT = os.path.join(HERE, "prompts", "label_v2.json")
R0911 = os.path.join(HERE, "..", "experiments_09112026", "data", "runs")
GATE_PAIRS = ("g6_gpt_dev", "g6_gpt_test")
MOST_NEG = os.path.join(HERE, "..", "..", "citator-benchmark", "data", "treatments_most_negative.csv")
INPUTS = os.path.join(HERE, "..", "experiments_09112026", "data", "inputs")

SEVERITY = [  # most → least severe; rank = index
    "Overruled by", "Abrogated by", "Reversed by", "Reversed and remanded by", "Vacated and remanded by", "Vacated by",
    "Questioned by", "Disapproved by", "Limited by", "Reversed in part; Vacated in part by",
    "Affirmed in part; Reversed in part by", "Affirmed in part; Vacated in part by",
    "Criticized by", "Distinguished by", "Declined to follow by", "Modified by", "Remanded by", "Cert. granted by",
    "Dismissed by", "Cert. denied by", "Affirmed by", "Cited by", "none"]
RANK = {t: i for i, t in enumerate(SEVERITY)}
TIER = {}
for t in SEVERITY[:6]:
    TIER[t] = "Stop"
for t in SEVERITY[6:12]:
    TIER[t] = "Warning"
for t in SEVERITY[12:18]:
    TIER[t] = "Caution"
for t in SEVERITY[18:]:
    TIER[t] = "Neutral"
NEGATIVE_TIERS = {"Stop", "Warning", "Caution"}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def base(label):
    return label.replace(" as recognized by", " by") if label else label


def load_run(name, rec_thr, pool):
    meta = json.load(open(os.path.join(RUNS, name, "meta.json")))
    reqs = {r["key"]: r["meta"] for r in read_jsonl(os.path.join(HERE, meta["requests"]))}
    rec_keys = json.load(open(PROMPT))["recognized_keys"]   # "rec:affirmed" → "Affirmed by"
    pooled, expected = {}, set()
    for m in reqs.values():
        expected.add((str(m["cluster_id"]), str(m["inv_id"])))
    for o in read_jsonl(os.path.join(RUNS, name, "outputs.jsonl")):
        ans = o.get("answers")
        if not ans or "applied" not in ans:
            continue
        m = reqs[o["key"]]
        k = (str(m["cluster_id"]), str(m["inv_id"]))
        ap = ans["applied"]
        p = pooled.setdefault(k, {"applied": "Cited by", "recognized": set(), "rec_p": {}, "p_applied": 0.0,
                                  "p_recognized": 0.0, "applied_conf": 0.0, "records": 0})
        choice, conf = ap["choice"], float(ap.get("confidence") or 0.0)
        if pool == "severity":          # most severe non-neutral label across records
            take = RANK.get(choice, 99) < RANK.get(p["applied"], 99)
        else:                           # confidence: the non-neutral label the model is surest of; Cited by only when no record treats the case
            take = choice != "Cited by" and (p["applied"] == "Cited by" or conf > p["applied_conf"]
                                             or (conf == p["applied_conf"] and RANK.get(choice, 99) < RANK.get(p["applied"], 99)))
        if take:
            p["applied"], p["applied_conf"] = choice, conf
        p["p_applied"] = max(p["p_applied"], 1.0 - float(ap.get("probabilities", {}).get("Cited by", 0.0)))
        for key, label in rec_keys.items():
            v = float((ans.get(key) or {}).get("noul") or 0.0)
            p["rec_p"][label] = max(p["rec_p"].get(label, 0.0), v)
            if v >= rec_thr:
                p["recognized"].add(label)
            if TIER[label] in NEGATIVE_TIERS:
                p["p_recognized"] = max(p["p_recognized"], v)
        p["records"] += 1
    return pooled, expected, meta


def truths(cids):
    applied, recog = {}, {}
    for run in GATE_PAIRS:
        for r in read_csv(os.path.join(R0911, run, "pairs.csv")):
            if r["citing"] in cids:
                applied[(r["citing"], r["inv_id"])] = {"final": r["final"], "pos_any": r["pos_any"] == "1",
                                                       "pos_major": r["pos_major"] == "1", "name": r["name"], "cited": r["cited"]}
    inv_by_cited = {}
    for cid in cids:
        for e in json.load(open(os.path.join(INPUTS, f"{cid}.inventory.json"))):
            if e.get("cited_cluster_id"):
                inv_by_cited[(cid, str(e["cited_cluster_id"]))] = str(e["id"])
    for r in read_csv(MOST_NEG):
        if r["citing_case_cluster_id"] in cids and r["final_treatment"].endswith("as recognized by"):
            k = inv_by_cited.get((r["citing_case_cluster_id"], r["cited_case_cluster_id"]))
            if k:
                recog[(r["citing_case_cluster_id"], k)] = base(r["final_treatment"])
    return applied, recog


def prf(flags, truth_pos, universe):
    tp = sum(1 for k in universe if flags.get(k) and truth_pos.get(k))
    fp = sum(1 for k in universe if flags.get(k) and not truth_pos.get(k))
    fn = sum(1 for k in universe if not flags.get(k) and truth_pos.get(k))
    neg = sum(1 for k in universe if not truth_pos.get(k))
    return tp, fp, fn, (tp / (tp + fp) if tp + fp else 0.0), (fp / neg if neg else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--flag-threshold", type=float, default=0.5, help="P(not Cited by) or a negative-tier recognized noul at or above → negative")
    ap.add_argument("--rec-threshold", type=float, default=0.5, help="a recognized label counts when its noul is at or above this")
    ap.add_argument("--pool", choices=["severity", "confidence"], default="confidence",
                    help="applied label per case across records: most severe non-neutral, or the non-neutral label with the highest confidence (default)")
    a = ap.parse_args()

    dahl = read_csv(PAIRS)
    cids = {p["citing_cluster_id"] for p in dahl}
    dahl_keys = {(p["citing_cluster_id"], p["inv_id"]) for p in dahl if p["inv_id"]}
    applied_t, recog_t = truths(cids)
    universe = set(applied_t)
    pos_any = {k: v["pos_any"] for k, v in applied_t.items()}
    pos_major = {k: v["pos_major"] for k, v in applied_t.items()}
    print(f"17 opinions, {len(universe)} benchmark pairs ({sum(pos_any.values())} any-expert / {sum(pos_major.values())} majority positives, "
          f"{len(recog_t)} with a recognized final), {len(dahl_keys)} Dahl pairs\n")

    summary = []
    for name in a.run:
        pooled, expected, meta = load_run(name, a.rec_threshold, a.pool)
        # binary: applied negative (label ≠ Cited by) OR recognized negative (tier not Neutral), by label
        neg_label = {k: (p["applied"] != "Cited by") or any(TIER[l] in NEGATIVE_TIERS for l in p["recognized"]) for k, p in pooled.items()}
        # binary by probability (threshold sweep)
        def neg_prob(t):
            return {k: p["p_applied"] >= t or p["p_recognized"] >= t for k, p in pooled.items()}
        d_lab = sum(1 for k in dahl_keys if neg_label.get(k))
        tp, fp, fn, prec, ff = prf(neg_label, pos_any, universe)
        tpm, _, fnm, _, _ = prf(neg_label, pos_major, universe)
        # applied label quality on gold pairs
        gold_pos = [k for k in universe if applied_t[k]["final"] not in ("Cited by", "")]
        exact = sum(1 for k in gold_pos if pooled.get(k, {}).get("applied") == applied_t[k]["final"])
        tier = sum(1 for k in gold_pos if TIER.get(pooled.get(k, {}).get("applied", "Cited by")) == TIER.get(applied_t[k]["final"]))
        pred_pos = [k for k in universe if pooled.get(k, {}).get("applied", "Cited by") != "Cited by"]
        pred_exact = sum(1 for k in pred_pos if pooled[k]["applied"] == applied_t[k]["final"])
        # recognized
        rec_det = sum(1 for k in recog_t if pooled.get(k, {}).get("recognized"))
        rec_exact = sum(1 for k in recog_t if recog_t[k] in pooled.get(k, {}).get("recognized", set()))
        rec_pred = sum(1 for k in universe if pooled.get(k, {}).get("recognized"))
        rec_labels = sum(len(pooled.get(k, {}).get("recognized", ())) for k in universe)
        summary.append((f"{name} ({meta.get('model')}, pool={a.pool})", f"{len(pooled)}/{len(expected)}", f"{d_lab}/{len(dahl_keys)}",
                        f"{tp}/{tp + fn}", f"{tpm}/{tpm + fnm}", f"{ff:.1%}", f"{prec:.2f}",
                        f"{exact}/{len(gold_pos)}", f"{tier}/{len(gold_pos)}", f"{pred_exact}/{len(pred_pos)}",
                        f"{rec_det}/{len(recog_t)}", f"{rec_exact}/{len(recog_t)}", f"{rec_pred} cases / {rec_labels} labels"))
        print(f"### {name} — flag by probability, threshold sweep (Dahl / any-expert / majority / false-flag on Cited-by / precision)")
        print("| thr | Dahl | any-expert | majority | false-flag | precision |")
        print("|---|---|---|---|---|---|")
        for t in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            fl = neg_prob(t)
            d = sum(1 for k in dahl_keys if fl.get(k))
            tp2, fp2, fn2, prec2, ff2 = prf(fl, pos_any, universe)
            tpm2, _, fnm2, _, _ = prf(fl, pos_major, universe)
            print(f"| {t} | {d}/{len(dahl_keys)} | {tp2}/{tp2 + fn2} | {tpm2}/{tpm2 + fnm2} | {ff2:.1%} | {prec2:.2f} |")
        # confusion of applied labels on gold positives
        conf = {}
        for k in gold_pos:
            g, p = applied_t[k]["final"], pooled.get(k, {}).get("applied", "—")
            conf[(g, p)] = conf.get((g, p), 0) + 1
        print(f"\n{name} — applied label on gold positives (gold → predicted):")
        for (g, p), n in sorted(conf.items(), key=lambda x: (-x[1], x[0])):
            print(f"  {n:3d}  {g} → {p}")
        lab_counts = {}
        for k in universe:
            for l in pooled.get(k, {}).get("recognized", ()):
                lab_counts[l] = lab_counts.get(l, 0) + 1
        print(f"\n{name} — recognized labels predicted at ≥ {a.rec_threshold} (cases): " +
              ", ".join(f"{l} {n}" for l, n in sorted(lab_counts.items(), key=lambda x: -x[1])) + "\n")
        with open(os.path.join(RUNS, name, "pairs.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["citing", "inv_id", "cited", "name", "records", "applied", "applied_conf", "p_applied", "recognized", "p_recognized",
                        "negative", "dahl", "final", "final_recognized", "pos_any", "pos_major"])
            for k in sorted(expected, key=lambda x: (int(x[0]), int(x[1]))):
                p, t = pooled.get(k, {}), applied_t.get(k, {})
                w.writerow([k[0], k[1], t.get("cited", ""), t.get("name", ""), p.get("records", 0), p.get("applied", ""),
                            f"{p.get('applied_conf', 0):.3f}" if p else "", f"{p.get('p_applied', 0):.3f}" if p else "",
                            "; ".join(sorted(p.get("recognized", ()), key=lambda l: RANK[l])), f"{p.get('p_recognized', 0):.3f}" if p else "", int(neg_label.get(k, False)),
                            int(k in dahl_keys), t.get("final", ""), recog_t.get(k, ""), int(t.get("pos_any", False)), int(t.get("pos_major", False))])

    print(f"### Summary (labels as chosen, recognized at ≥ {a.rec_threshold}; binary = applied ≠ Cited by OR a negative-tier recognized label)")
    print("| run | scored | Dahl recall | any-expert recall | majority recall | false-flag on Cited-by | precision | applied exact on gold pos | tier exact | precision of predicted labels | recognized detected | recognized exact | recognized predicted |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in summary:
        print("| " + " | ".join(r) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
