"""Step 4 — Clairic answers → prediction file in the triage scorer's shape, plus the usage report.

  to_predictions.py --run d1 [--requests data/requests/d1.jsonl] [--out data/pred_d1.json]

Output {cid: {"opinions": [{"opinion_idx", "opinion_type", "mentions": [{"start", "end", "text",
"before", "group"}]}]}} — what experiments_09022026/triage/analysis/score_encoder.py reads, which
re-anchors every mention in the gold frame by text + the 40 preceding characters, so the answer's
offset basis (state or state_text) does not matter for scoring. The basis IS reported: the share
of spans whose state[start:end] is one of the answer's values.

Groups: D1/D2 answers carry no coreference → every mention is its own group (score the mention
row only; the coref row is meaningless). D3 → group = the inventory field the span came from.
Chunked opinions: a span is kept from the chunk that holds it at least half an overlap away from
a cut edge; duplicates by (opinion, start, end) collapse to one.
"""
import argparse
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "data", "runs")
REQUESTS = os.path.join(HERE, "data", "requests")
BEFORE = 120  # longer than the encoder's 40 so duplicate citation strings with the same 40-char context still anchor
PRICE_PER_M = 0.10


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def group_of(field, design):
    return f"inv{field[5:]}" if design == "d3" and field.startswith("case_") else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--requests", help="default data/requests/{run's design}.jsonl")
    ap.add_argument("--out")
    ap.add_argument("--overlap-chars", type=int, default=2000, help="must match make_requests.py")
    a = ap.parse_args()
    with open(os.path.join(RUNS, a.run, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    req_path = a.requests or os.path.join(HERE, meta["requests"])
    design = os.path.basename(req_path).split(".")[0].split("_")[0]
    reqs = {r["key"]: r for r in read_jsonl(req_path)}
    outs = [r for r in read_jsonl(os.path.join(RUNS, a.run, "outputs.jsonl")) if not r.get("error")]
    pred, seen = {}, set()
    spans_total, basis_ok, basis_checked, list_max, singleton = 0, 0, 0, 0, 0
    usage = Counter()
    passes, truncated = Counter(), 0
    for o in outs:
        req = reqs.get(o["key"])
        if not req:
            continue
        m, state = req["meta"], req["body"]["state"]
        text = o.get("state_text") or state
        cid, lo, hi = m["cluster_id"], m["chunk_start"], m["chunk_end"]
        keep_lo = lo if lo == 0 else lo + a.overlap_chars // 2
        keep_hi = hi if hi >= m["opinion_chars"] else hi - a.overlap_chars // 2
        ops = pred.setdefault(cid, {"opinions": {}})["opinions"]
        op = ops.setdefault(m["idx"], {"opinion_idx": m["idx"], "opinion_type": m["type"], "mentions": []})
        u = o.get("usage") or {}
        for k in ("input_tokens", "state_tokens", "schema_tokens", "output_tokens"):
            usage[k] += u.get(k) or 0
        passes[u.get("passes") or 0] += 1
        truncated += bool(o.get("truncated"))
        for field, ans in (o.get("answers") or {}).items():
            # list answers: `value` = the UNIQUE strings, `spans` = every occurrence (sorted, non-overlapping),
            # so spans are the mentions and their text is read from the state (smoke 2026-10-23).
            spans = ans.get("spans") or ([ans["span"]] if ans.get("span") else [])
            vset = set(ans.get("value") or []) if isinstance(ans.get("value"), list) else {ans.get("value")}
            list_max = max(list_max, len(spans))
            for sp in spans:
                if not sp or len(sp) != 2:
                    continue
                s, e = int(sp[0]), int(sp[1])
                spans_total += 1
                basis_checked += 1
                basis_ok += state[s:e] in vset
                abs_s = lo + s
                if not keep_lo <= abs_s < keep_hi:
                    continue
                key = (cid, m["idx"], abs_s, lo + e)
                if key in seen:
                    continue
                seen.add(key)
                g = group_of(field, design)
                if g is None:
                    singleton += 1
                    g = f"s{singleton}"
                op["mentions"].append({"start": abs_s, "end": lo + e, "text": text[s:e],
                                       "before": text[max(0, s - BEFORE):s], "group": g})
    for cid in pred:
        pred[cid]["opinions"] = [pred[cid]["opinions"][k] for k in sorted(pred[cid]["opinions"])]
        for op in pred[cid]["opinions"]:
            op["mentions"].sort(key=lambda x: x["start"])
    out_path = a.out or os.path.join(HERE, "data", f"pred_{a.run}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pred, f, ensure_ascii=False)
    n_m = sum(len(op["mentions"]) for c in pred.values() for op in c["opinions"])
    print(f"{a.run} ({design}): {len(outs)} answered requests → {len(pred)} clusters, {n_m:,} mentions "
          f"(from {spans_total:,} spans; longest list {list_max}) → {out_path}")
    print(f"offset basis = state on {basis_ok}/{basis_checked} spans; truncated {truncated}/{len(outs)}; "
          f"passes {dict(sorted(passes.items()))}")
    print(f"usage: {dict(usage)}; ≈ ${usage['input_tokens'] * PRICE_PER_M / 1e6:.3f} at ${PRICE_PER_M}/M input")


if __name__ == "__main__":
    main()
