"""Step 1 — the 49 gold_dev clusters in the annotator's gold frame, plus sizing.

Writes data/inputs/{cid}.json:
  {"cluster_id", "opinions": [{"idx", "id", "type", "text", "chars", "tokens_o200k", "gold_mentions"}],
   "inventory": [{"id", "name", "citation"}]}
and data/inputs/index.csv (one row per opinion), then prints the sizing table.

Text = State(cid, seed_state=False).texts — the frame the triage scorer anchors in, so spans
returned on this text need no re-anchoring (score_encoder re-anchors by text + context anyway).
Inventory (design D3 only) = the 0911 inventory's cited-case NAME plus ONE reporter citation,
both obtainable from CourtListener metadata; the inventory's mention strings are never copied.
Tokens are o200k counts, a proxy: Clairic's tokenizer is undisclosed and its usage field is the
measurement once a request has run.
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys

import tiktoken

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRIAGE = os.path.join(ROOT, "experiments_09022026", "triage")
sys.path.insert(0, os.path.join(TRIAGE, "lib"))
from citation_seed import State  # noqa: E402

INVENTORIES = os.path.join(ROOT, "experiments_09112026", "data", "inputs")
DEV_IDS = os.path.join(TRIAGE, "data", "citation_seed", "dev_ids.txt")
OUT = os.path.join(HERE, "data", "inputs")
RE_REPORTER = re.compile(r"^(\d+\s+[A-Z][A-Za-z0-9.'&\- ]*?\s+\d+)")
PASS_TOKENS = 8_192
CONTEXT_TOKENS = 65_536


def read_ids(path):
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def inventory_for(cid):
    path = os.path.join(INVENTORIES, f"{cid}.inventory.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    out = []
    for r in rows:
        citation = ""
        for c in r.get("citations") or []:
            m = RE_REPORTER.match(c.strip())
            if m:
                citation = m.group(1)
                break
        out.append({"id": r["id"], "name": (r.get("name") or "").strip(), "citation": citation})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", help="cluster ids (default: the 49 gold_dev ids)")
    a = ap.parse_args()
    ids = a.ids or read_ids(DEV_IDS)
    enc = tiktoken.get_encoding("o200k_base")
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for cid in ids:
        gold = State(cid, seed_state=False)
        counts = {}
        for m in gold.mentions():
            counts[m[0]] = counts.get(m[0], 0) + 1
        opinions = []
        for idx, op in enumerate(gold.opinions):
            oid = str(op["id"])
            text = gold.texts[oid]
            rec = {"idx": idx, "id": oid, "type": gold.op_type[oid], "text": text, "chars": len(text),
                   "tokens_o200k": len(enc.encode(text)), "gold_mentions": counts.get(oid, 0)}
            opinions.append(rec)
        inv = inventory_for(cid)
        with open(os.path.join(OUT, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump({"cluster_id": str(cid), "opinions": opinions, "inventory": inv}, f, ensure_ascii=False)
        for rec in opinions:
            rows.append({"cluster_id": str(cid), "idx": rec["idx"], "opinion_id": rec["id"], "type": rec["type"],
                         "chars": rec["chars"], "tokens_o200k": rec["tokens_o200k"],
                         "gold_mentions": rec["gold_mentions"], "n_inventory": len(inv),
                         "inventory_with_citation": sum(1 for i in inv if i["citation"])})
    with open(os.path.join(OUT, "index.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    toks = sorted(r["tokens_o200k"] for r in rows)
    print(f"{len(ids)} clusters, {len(rows)} opinions, {sum(r['gold_mentions'] for r in rows):,} gold mentions, "
          f"{sum(toks):,} o200k tokens")
    print(f"tokens per opinion: median {statistics.median(toks):,.0f}, p90 {toks[int(0.9 * len(toks))]:,}, "
          f"max {toks[-1]:,}; over one {PASS_TOKENS:,}-token pass: {sum(t > PASS_TOKENS for t in toks)}; "
          f"over the {CONTEXT_TOKENS:,} context: {sum(t > CONTEXT_TOKENS for t in toks)}")
    inv = [r["n_inventory"] for r in rows if r["idx"] == 0]
    print(f"inventory per cluster (D3 fields): median {statistics.median(inv):.0f}, max {max(inv)}, "
          f"missing {sum(1 for n in inv if n == 0)}")
    print(f"wrote {OUT}/{{cid}}.json + index.csv")


if __name__ == "__main__":
    main()
