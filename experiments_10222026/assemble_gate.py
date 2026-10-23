"""Experiment 1 — turn a decision-model gate run into a 0911-format gate run so the existing
pipeline consumes it unchanged (no direct history: onAppeal and disposition stay empty):

  assemble_gate.py --gate jev_gate_concat --out jev_gate_concat \
      [--high 0.8 --medium 0.6 --low 0.4 --recognized 0.5]

→ ../experiments_09112026/data/runs/{out}/{meta.json, outputs/{cid}.json, summary.json}
Then:  cd ../experiments_09112026
       bash run.sh score.py --gate jev_gate_concat --split all            # verdict recall, DH, RR routing
       bash run.sh run_stage.py prepare --stage label --name l_sonnet3_<gate> --model sonnet \
            --split all --from-gate jev_gate_concat --min-confidence medium  # labeler on Jev's flags

Per cited case the yes-probabilities are max-pooled over its records; `more` ≥ low/medium/high
becomes a flagged entry with that confidence, `recognized` ≥ threshold a recognized entry.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "data", "runs")
RUNS_0911 = os.path.join(HERE, "..", "experiments_09112026", "data", "runs")


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_run(name):
    meta = json.load(open(os.path.join(RUNS, name, "meta.json")))
    reqs = {r["key"]: r for r in read_jsonl(os.path.join(HERE, meta["requests"]))}
    outs = [o for o in read_jsonl(os.path.join(RUNS, name, "outputs.jsonl")) if o.get("answers")]
    return meta, reqs, outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", required=True, help="1022 run name with gate_v1 answers")
    ap.add_argument("--out", required=True, help="name of the 0911-format gate run to write")
    ap.add_argument("--high", type=float, default=0.8)
    ap.add_argument("--medium", type=float, default=0.6)
    ap.add_argument("--low", type=float, default=0.4)
    ap.add_argument("--recognized", type=float, default=0.5)
    ap.add_argument("--price-per-m", type=float, default=0.042)
    a = ap.parse_args()

    meta, reqs, outs = load_run(a.gate)
    more, rec, n_rec = {}, {}, {}
    for o in outs:
        m = reqs[o["key"]]["meta"]
        k = (str(m["cluster_id"]), int(m["inv_id"]))
        ans = o["answers"]
        more[k] = max(more.get(k, 0.0), float(ans["more"]["noul"]))
        rec[k] = max(rec.get(k, 0.0), float(ans["recognized"]["noul"]))
        n_rec[k] = n_rec.get(k, 0) + 1
    cids = sorted({k[0] for k in more}, key=int)

    out_dir = os.path.join(RUNS_0911, a.out, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    n_flag = n_recog = 0
    for cid in cids:
        flagged, recognized = [], []
        for (c, gid), p in sorted(more.items()):
            if c != cid:
                continue
            conf = "high" if p >= a.high else "medium" if p >= a.medium else "low" if p >= a.low else None
            if conf:
                flagged.append({"id": gid, "confidence": conf, "quote": None, "signal": f"p={p:.3f} over {n_rec[(c, gid)]} records"})
            if rec.get((c, gid), 0.0) >= a.recognized:
                recognized.append({"id": gid, "signal": f"p={rec[(c, gid)]:.3f}"})
        out = {"onAppeal": [], "flagged": flagged, "recognized": recognized, "disposition": None,
               "n_verdicts": sum(1 for k in more if k[0] == cid), "cid": cid}
        with open(os.path.join(out_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        n_flag += len(flagged)
        n_recog += len(recognized)
    run_meta = {"name": a.out, "stage": "gate", "model": meta.get("model"), "model_id": meta.get("model"), "family": "systemone",
                "split": "all", "ids_given": False, "prompt": f"experiments_10222026/prompts/{meta.get('prompt')}.json",
                "prompt_sha": reqs[next(iter(reqs))].get("prompt_sha"), "thinking": None, "max_tokens": None, "effort": None,
                "signals": False, "from_gate": None, "from_gold": False, "min_confidence": "low", "gate_prompt_sha": None,
                "flagged": None, "flag_only": None, "recognized": None, "skipped": {}, "cids": cids,
                "source_1022": {"gate": a.gate, "thresholds": {"high": a.high, "medium": a.medium, "low": a.low,
                                                                            "recognized": a.recognized}},
                "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(RUNS_0911, a.out, "meta.json"), "w") as f:
        json.dump(run_meta, f, indent=1)
    in_tokens = sum((o.get("usage") or {}).get("input_tokens") or 0 for o in outs)
    with open(os.path.join(RUNS_0911, a.out, "summary.json"), "w") as f:
        json.dump({"n_records": len(cids), "n_ok": len(cids), "n_errors": 0, "errors": {}, "truncated": [],
                   "input_tokens": in_tokens, "output_tokens": 0, "cost_usd": round(in_tokens * a.price_per_m / 1e6, 3),
                   "rate": "on-demand", "n_clusters_out": len(cids),
                   "clusters_with_flags": sum(1 for c in cids if any(k[0] == c and more[k] >= a.low for k in more)),
                   "flagged_ids": n_flag, "recognized_ids": n_recog, "inventory_ids": len(more)}, f, indent=1)
    print(f"wrote {os.path.relpath(os.path.join(RUNS_0911, a.out), HERE)}: {len(cids)} opinions, {n_flag} flagged ids (≥ low), {n_recog} recognized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
