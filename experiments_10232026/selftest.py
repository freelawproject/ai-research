"""Offline wiring check: fake Clairic answers from the gold mentions → to_predictions → scorer.

  selftest.py --design d1 [--ids 513272 529973] [--requests data/requests/d1.jsonl]
  selftest.py --design d3 ...        # groups from the field name → coref row must also be 1.0

Writes data/runs/_selftest_{design}/{meta.json, outputs.jsonl} shaped like run_clairic.py's output,
with every gold mention of each request's state returned as a list item with its span, then runs
to_predictions.py and score_encoder.py. Mention P/R/F must come back 1.000 (coref too for d3);
with a chunked request file this also exercises the overlap rule. No network, no model.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRIAGE = os.path.join(ROOT, "experiments_09022026", "triage")
sys.path.insert(0, os.path.join(TRIAGE, "lib"))
from citation_seed import State  # noqa: E402

SCORER = os.path.join(TRIAGE, "analysis", "score_encoder.py")


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="d1", choices=["d1", "d2", "d3"])
    ap.add_argument("--ids", nargs="*", default=["513272", "529973", "2369968"])
    ap.add_argument("--requests")
    a = ap.parse_args()
    req_path = a.requests or os.path.join(HERE, "data", "requests", f"{a.design}.jsonl")
    reqs = [r for r in read_jsonl(req_path) if r["meta"]["cluster_id"] in a.ids]
    name = f"_selftest_{a.design}"
    run_dir = os.path.join(HERE, "data", "runs", name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "meta.json"), "w") as f:
        json.dump({"name": name, "requests": os.path.relpath(req_path, HERE), "model": "selftest"}, f)
    gold = {cid: State(cid, seed_state=False) for cid in a.ids}
    n_out = 0
    with open(os.path.join(run_dir, "outputs.jsonl"), "w", encoding="utf-8") as out:
        for r in reqs:
            m, state = r["meta"], r["body"]["state"]
            g = gold[m["cluster_id"]]
            lo, hi = m["chunk_start"], m["chunk_end"]
            fields = {}
            for oid, s, e, gid, _kind, _key in g.mentions():
                if oid != m["opinion_id"] or s < lo or e > hi:
                    continue
                field = f"case_{gid[1:]}" if a.design == "d3" else "case_citations"
                f = fields.setdefault(field, {"type": "list", "value": [], "spans": [], "confidence": 1.0})
                f["value"].append(state[s - lo:e - lo])
                f["spans"].append([s - lo, e - lo])
            out.write(json.dumps({"key": r["key"], "answers": fields, "truncated": False,
                                  "usage": {"input_tokens": len(state) // 4, "passes": 1}, "state_text": None,
                                  "request_id": None, "elapsed": 0, "error": None}, ensure_ascii=False) + "\n")
            n_out += 1
    print(f"{name}: {n_out} fake answers from gold for {a.ids}")
    subprocess.run([sys.executable, os.path.join(HERE, "to_predictions.py"), "--run", name], check=True)
    subprocess.run([sys.executable, SCORER, "--pred", os.path.join(HERE, "data", f"pred_{name}.json"),
                    "--ids", *a.ids, "--label", f"selftest {a.design}"], check=True)


if __name__ == "__main__":
    main()
