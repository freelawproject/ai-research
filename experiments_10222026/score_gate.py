"""Score Jev gate runs (gate_v1: `more` + `recognized` nouls) as a gate, next to the 0911 GPT gate.

  score_gate.py --run jev_gate_mention [--run jev_gate_concat ...]

Per cited case: P(more) and P(recognized) = max over records. A case is flagged at threshold t
when P(more) ≥ t; "flag OR recognized" also counts P(recognized) ≥ t. Truths as in score.py:
Dahl 41 negatives; benchmark any-expert / majority positives and Cited-by pairs; GPT gate rows
= 0911 g6 two-pass union at ≥ medium, with and without its recognized marks.
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "data", "runs")
PAIRS = os.path.join(HERE, "data", "contexts", "pairs.csv")
R0911 = os.path.join(HERE, "..", "experiments_09112026", "data", "runs")
GATE_RUNS = ("g6_gpt_dev", "g6_gpt_test", "g6b_gpt_dev", "g6b_gpt_test")
CONF = {"low": 1, "medium": 2, "high": 3}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_run(name):
    meta = json.load(open(os.path.join(RUNS, name, "meta.json")))
    reqs = {r["key"]: r["meta"] for r in read_jsonl(os.path.join(HERE, meta["requests"]))}
    more, rec = {}, {}
    for o in read_jsonl(os.path.join(RUNS, name, "outputs.jsonl")):
        a = o.get("answers")
        if not a:
            continue
        m = reqs[o["key"]]
        k = (str(m["cluster_id"]), str(m["inv_id"]))
        if "more" in a:                       # gate_v1: yes/no marks
            more[k] = max(more.get(k, 0.0), float(a["more"]["noul"]))
            rec[k] = max(rec.get(k, 0.0), float(a["recognized"]["noul"]))
        elif "applied" in a:                  # gate_choice_v1: 1 − P(Cited by)
            more[k] = max(more.get(k, 0.0), 1.0 - float(a["applied"].get("probabilities", {}).get("Cited by", 0.0)))
            rec.setdefault(k, 0.0)
    return more, rec, meta


def gpt_gate(cids):
    flags, recog = {}, set()
    for run in GATE_RUNS:
        for cid in cids:
            p = os.path.join(R0911, run, "outputs", f"{cid}.json")
            if not os.path.exists(p):
                continue
            o = json.load(open(p))
            for f in o.get("flagged", []):
                k = (cid, str(f["id"]))
                flags[k] = max(flags.get(k, 0), CONF.get(f.get("confidence"), 0))
            for r in o.get("recognized", []):
                recog.add((cid, str(r["id"])))
    return {k for k, c in flags.items() if c >= 2}, recog


def counts(flagged, dahl, pos, posm, cited):
    return (sum(1 for k in dahl if k in flagged), len(pos & flagged), len(posm & flagged), len(cited & flagged))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    a = ap.parse_args()
    dahl_rows = read_csv(PAIRS)
    cids = sorted({r["citing_cluster_id"] for r in dahl_rows}, key=int)
    dahl = [(r["citing_cluster_id"], r["inv_id"]) for r in dahl_rows if r["inv_id"]]
    truth = {}
    for run in GATE_RUNS[:2]:
        for r in read_csv(os.path.join(R0911, run, "pairs.csv")):
            if r["citing"] in cids:
                truth[(r["citing"], r["inv_id"])] = r
    pos = {k for k, r in truth.items() if r["pos_any"] == "1"}
    posm = {k for k, r in truth.items() if r["pos_major"] == "1"}
    cited = {k for k, r in truth.items() if r["final"] == "Cited by"}
    gflags, grecog = gpt_gate(cids)
    hdr = f"| gate | Dahl (of {len(dahl)}) | any-expert (of {len(pos)}) | majority (of {len(posm)}) | false flags on Cited-by (of {len(cited)}) |"
    print(hdr + "\n|---|---|---|---|---|")
    for label, S in (("GPT gate ×2, union ≥ medium", gflags), ("GPT gate ×2, union ≥ medium OR recognized", gflags | grecog)):
        d, tp, tpm, ff = counts(S, dahl, pos, posm, cited)
        print(f"| {label} | {d} | {tp} | {tpm} | {ff} ({ff / len(cited):.1%}) |")
    for name in a.run:
        more, rec, meta = load_run(name)
        pos, posm, cited = pos & set(more), posm & set(more), cited & set(more)
        for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
            S = {k for k, p in more.items() if p >= t}
            d, tp, tpm, ff = counts(S, dahl, pos, posm, cited)
            S2 = S | {k for k, p in rec.items() if p >= t}
            d2, tp2, tpm2, ff2 = counts(S2, dahl, pos, posm, cited)
            print(f"| {name} more ≥ {t} | {d} | {tp} | {tpm} | {ff} ({ff / len(cited):.1%}) |")
            print(f"| {name} more OR recognized ≥ {t} | {d2} | {tp2} | {tpm2} | {ff2} ({ff2 / len(cited):.1%}) |")
        if any(rec.values()):
            print(f"| {name}: cases with P(recognized) ≥ 0.5 | {sum(1 for p in rec.values() if p >= 0.5)} of {len(rec)} (GPT gate marked {len(grecog)}) | | | |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
