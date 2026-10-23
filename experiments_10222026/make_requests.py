"""Step 3 — build /v1/systemone request bodies (TypeSafe System One schema, shared by Baseten,
Perplexity, Liquid, OpenRouter, Vercel and LiteLLM) for one model.

  make_requests.py --experiment gate  --model jev-1.14.0   # exp 1: `more` + `recognized` nouls per cited-case record (setups A + B)
  make_requests.py --experiment label --model jev-1.14.0   # exp 2: applied choice + 21 recognized nouls per record (setups A + B)
  make_requests.py --experiment gate_choice --model decision-models/baseten-mammoth-27b-latest   # exp 1b: the applied choice alone as the gate

Writes data/requests/{model}_{experiment}_{setup}.jsonl: {"key", "meta", "prompt", "body"}.
One target case per request, named in the state; the questions of one request are evaluated
independently against the same state (one input charge per request).
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTEXTS = os.path.join(HERE, "data", "contexts")
OUT = os.path.join(HERE, "data", "requests")
PROMPTS = os.path.join(HERE, "prompts")
META_KEYS = ("cluster_id", "citing_case", "inv_id", "cited_cluster_id", "cited_ref", "cited_case",
             "n_mentions", "setup", "unit", "n_units", "parts", "tokens")
VERSIONS = {"gate": "gate_v1", "gate_choice": "gate_choice_v1", "label": "label_v2"}


def load_prompt(version):
    path = os.path.join(PROMPTS, f"{version}.json")
    with open(path, "rb") as f:
        raw = f.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def questions_of(prompt):
    out = {}
    for name, q in prompt["questions"].items():
        body = {"type": q["type"], "instructions": q["instructions"]}
        if q.get("criteria"):
            body["criteria"] = q["criteria"]
        out[name] = body
    return out


def state_for(r):
    court = f" ({r['citing_court']}, {r['citing_year']})" if r.get("citing_court") else ""
    cites = "; ".join(r.get("citations") or [])
    cited = r["cited_case"] or "(name not given in the opinion)"
    return {"citing_case": f"{r['citing_case']}{court}",
            "cited_case": cited + (f" — {cites}" if cites else ""),
            "passage": r["passage"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", choices=sorted(VERSIONS), required=True)
    ap.add_argument("--model", required=True, help="model id sent in the body, e.g. jev-1.14.0")
    a = ap.parse_args()
    prompt, sha = load_prompt(VERSIONS[a.experiment])
    os.makedirs(OUT, exist_ok=True)
    for setup in ("mention", "concat"):
        n, toks = 0, 0
        out_path = os.path.join(OUT, f"{a.model.replace('/', '_')}_{a.experiment}_{setup}.jsonl")
        with open(os.path.join(CONTEXTS, f"{setup}.jsonl"), encoding="utf-8") as fin, \
                open(out_path, "w", encoding="utf-8") as fout:
            for line in fin:
                r = json.loads(line)
                rec = {"key": f"{r['cluster_id']}:{r['inv_id']}:{setup}:{r['unit']}",
                       "meta": {k: r[k] for k in META_KEYS}, "prompt": prompt["version"], "prompt_sha": sha,
                       "body": {"model": a.model, "state": state_for(r), "questions": questions_of(prompt)}}
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                toks += r["tokens"]
        print(f"{os.path.relpath(out_path, HERE)}: {n:,} requests, {toks:,} passage tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
