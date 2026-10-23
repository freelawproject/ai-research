"""Build the two decision-model inputs for every cited case of the Dahl citing opinions.

Setup A (`mention`): one record per mention — the mention plus BEFORE tokens of context before
and AFTER tokens after. The pair is flagged when any mention record is flagged.
Setup B (`concat`): one record per cited case — every mention window (same BEFORE/AFTER),
overlapping windows merged, concatenated in document order and packed into chunks of at most
BUDGET tokens. A cited case with more than one chunk gets one record per chunk; the pair is
flagged when any chunk is flagged.

In both setups the target case is named in the record and every mention of it in the passage is
wrapped in <cite>…</cite>; other cases appear as plain text. Tokens are Qwen3 tokens (the
backbone family of mammoth-1, elephant-1 and pplx-decider). Writes data/contexts/{mention,
concat}.jsonl, data/contexts/pairs.csv (Dahl gold joined to inventory ids) and prints a summary.
"""
import argparse
import bisect
import csv
import json
import os
import re
import sys

from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
EXT_CSV = os.path.join(HERE, "..", "..", "external-benchmark", "output", "external_benchmark.csv")
INPUTS = os.path.join(HERE, "..", "experiments_09112026", "data", "inputs")
CITING_META = os.path.join(HERE, "..", "experiments_05182026", "data", "citing_metadata.csv")
OUT_DIR = os.path.join(HERE, "data", "contexts")
QWEN_TOKENIZER = "Qwen/Qwen3-0.6B"

PART_TAGS = ("lead", "plurality", "concurrence", "dissent", "combined", "trialcourt")
TAG = re.compile(r"<(/?)(\w+)(?:\s+group=\"(\d+)\")?\s*>")
SEP = "\n[…]\n"


def dahl_rows():
    with open(EXT_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["source"] == "dahl"]


def citing_meta():
    with open(CITING_META, newline="", encoding="utf-8") as f:
        return {r["cluster_id"]: r for r in csv.DictReader(f)}


def parse_tagged(tagged):
    """Strip tags → (plain, mentions) with mentions = [(start, end, inv_id, part)] in plain coords."""
    out, mentions, pos, part, open_m = [], [], 0, "", None
    for m in TAG.finditer(tagged):
        out.append(tagged[pos:m.start()])
        pos = m.end()
        closing, name, group = m.group(1), m.group(2), m.group(3)
        if name == "citedCase":
            if not closing:
                open_m = (sum(map(len, out)), int(group))
            elif open_m:
                mentions.append((open_m[0], sum(map(len, out)), open_m[1], part))
                open_m = None
        elif name in PART_TAGS:
            part = "" if closing else name
    out.append(tagged[pos:])
    return "".join(out), mentions


def token_offsets(tok, text):
    enc = tok(text, return_offsets_mapping=True, add_special_tokens=False)
    starts = [s for s, _ in enc["offset_mapping"]]
    ends = [e for _, e in enc["offset_mapping"]]
    return starts, ends


def mark(plain, span, target_mentions):
    """Slice plain[span] and wrap every target mention inside it with <cite> tags."""
    s, e = span
    pieces, pos = [], s
    for ms, me, _, _ in target_mentions:
        if ms >= s and me <= e:
            pieces.append(plain[pos:ms])
            pieces.append("<cite>" + plain[ms:me] + "</cite>")
            pos = me
    pieces.append(plain[pos:e])
    return "".join(pieces).strip()


def merge(spans):
    spans = sorted(spans)
    out = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def build(tok, cid, meta, before, after, budget):
    with open(os.path.join(INPUTS, f"{cid}.tagged.txt"), encoding="utf-8") as f:
        tagged = f.read()
    with open(os.path.join(INPUTS, f"{cid}.inventory.json"), encoding="utf-8") as f:
        inv = json.load(f)
    plain, mentions = parse_tagged(tagged)
    starts, ends = token_offsets(tok, plain)
    n_tok = len(starts)

    def tok_window(ms, me):
        ti = bisect.bisect_right(starts, ms) - 1
        tj = bisect.bisect_left(ends, me)
        a, b = max(0, ti - before), min(n_tok - 1, tj + after)
        return a, b

    def chars(a, b):
        return starts[a], ends[b]

    m = meta.get(str(cid), {})
    base = {"cluster_id": int(cid), "citing_case": m.get("case_name", ""), "citing_court": m.get("court", ""),
            "citing_year": m.get("year", "")}
    by_id = {}
    for ms, me, gid, part in mentions:
        by_id.setdefault(gid, []).append((ms, me, gid, part))

    rec_a, rec_b = [], []
    for e in inv:
        gid = e["id"]
        ments = by_id.get(gid, [])
        if not ments:
            continue
        head = dict(base, inv_id=gid, cited_cluster_id=e.get("cited_cluster_id"), cited_ref=e.get("cited_ref"),
                    cited_case=e.get("name", ""), citations=e.get("citations", []), n_mentions=len(ments))
        # Setup A — one record per mention
        for k, (ms, me, _, part) in enumerate(ments):
            a, b = tok_window(ms, me)
            passage = mark(plain, chars(a, b), ments)
            rec_a.append(dict(head, setup="mention", unit=k, n_units=len(ments), parts=[part],
                              tokens=b - a + 1, passage=passage))
        # Setup B — merged windows packed into chunks ≤ budget
        windows = merge([tok_window(ms, me) for ms, me, _, _ in ments])
        pieces = []  # (a, b) token spans, each ≤ budget
        for a, b in windows:
            while b - a + 1 > budget:
                pieces.append((a, a + budget - 1))
                a = a + budget
            pieces.append((a, b))
        chunks, cur, cur_tok = [], [], 0
        for a, b in pieces:
            n = b - a + 1
            if cur and cur_tok + n > budget:
                chunks.append(cur)
                cur, cur_tok = [], 0
            cur.append((a, b))
            cur_tok += n
        if cur:
            chunks.append(cur)
        for k, ch in enumerate(chunks):
            text = SEP.join(mark(plain, chars(a, b), ments) for a, b in ch)
            parts = sorted({p for ms, me, _, p in ments if any(starts[a] <= ms and me <= ends[b] for a, b in ch)})
            rec_b.append(dict(head, setup="concat", unit=k, n_units=len(chunks), parts=parts,
                              tokens=sum(b - a + 1 for a, b in ch), passage=text))
    return rec_a, rec_b, inv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=int, default=256, help="context tokens before each mention")
    ap.add_argument("--after", type=int, default=512, help="context tokens after each mention")
    ap.add_argument("--budget", type=int, default=16_000, help="max passage tokens per concat record")
    a = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(QWEN_TOKENIZER)
    meta = citing_meta()
    rows = dahl_rows()
    cids = sorted({r["citing_cluster_id"] for r in rows}, key=int)
    os.makedirs(OUT_DIR, exist_ok=True)

    all_a, all_b, pairs = [], [], []
    for cid in cids:
        rec_a, rec_b, inv = build(tok, cid, meta, a.before, a.after, a.budget)
        all_a += rec_a
        all_b += rec_b
        by_cluster = {str(e["cited_cluster_id"]): e for e in inv if e.get("cited_cluster_id")}
        by_name = {e["name"].lower(): e for e in inv if e.get("name")}
        for r in rows:
            if r["citing_cluster_id"] != cid:
                continue
            e = by_cluster.get(r["cited_cluster_id"]) or by_name.get(r["cited_cl_case_name"].lower())
            gid = e["id"] if e else None
            pairs.append({
                "citing_cluster_id": cid, "citing_case": r["citing_cl_case_name"],
                "cited_cluster_id": r["cited_cluster_id"], "cited_case": r["cited_cl_case_name"],
                "inv_id": gid, "join": "cluster" if e and str(e.get("cited_cluster_id")) == r["cited_cluster_id"]
                else ("name" if e else "unjoined"),
                "n_mentions": e["n_mentions"] if e else 0,
                "mention_records": sum(1 for x in rec_a if x["inv_id"] == gid) if e else 0,
                "concat_records": sum(1 for x in rec_b if x["inv_id"] == gid) if e else 0,
                "dahl_label": r["label_binary"], "shepards": r["shepards_code"], "dahl_note": r["annotator_note"],
            })

    for name, recs in (("mention", all_a), ("concat", all_b)):
        with open(os.path.join(OUT_DIR, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT_DIR, "pairs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(pairs[0].keys()))
        w.writeheader()
        w.writerows(pairs)
    with open(os.path.join(OUT_DIR, "params.json"), "w") as f:
        json.dump({"before": a.before, "after": a.after, "budget": a.budget, "tokenizer": QWEN_TOKENIZER}, f, indent=1)

    ta, tb = sum(r["tokens"] for r in all_a), sum(r["tokens"] for r in all_b)
    multi = sum(1 for r in all_b if r["n_units"] > 1 and r["unit"] == 0)
    print(f"window {a.before}/{a.after} tokens, concat budget {a.budget}\n")
    print("| setup | records | cited cases | passage tokens | $ at 0.042/M | $ at 0.02/M |")
    print("|---|---|---|---|---|---|")
    print(f"| mention (A) | {len(all_a):,} | {len({(r['cluster_id'], r['inv_id']) for r in all_a}):,} | {ta:,} | {ta*0.042/1e6:.3f} | {ta*0.02/1e6:.3f} |")
    print(f"| concat (B) | {len(all_b):,} | {len({(r['cluster_id'], r['inv_id']) for r in all_b}):,} | {tb:,} | {tb*0.042/1e6:.3f} | {tb*0.02/1e6:.3f} |")
    print(f"\nconcat: {multi} cited cases need more than one chunk; max chunk {max(r['tokens'] for r in all_b):,} tokens; "
          f"mention records max {max(r['tokens'] for r in all_a):,} tokens")
    print("\nDahl pairs:", len(pairs), "joined by cluster", sum(p["join"] == "cluster" for p in pairs),
          "by name", sum(p["join"] == "name" for p in pairs), "unjoined", sum(p["join"] == "unjoined" for p in pairs))
    print("| citing | cited | mentions | A records | B records | Dahl |")
    print("|---|---|---|---|---|---|")
    for p in pairs:
        print(f"| {p['citing_case']} | {p['cited_case']} | {p['n_mentions']} | {p['mention_records']} | {p['concat_records']} | {p['dahl_label']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
