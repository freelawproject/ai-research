"""Step 2 — Clairic request bodies for one field design.

  make_requests.py --design d1                              # whole opinion per request
  make_requests.py --design d2 --chunk-chars 30000 --overlap-chars 2000   # chunked, if the smoke shows truncation
  make_requests.py --design d3 --max-fields 60             # one list field per inventory case, ≤ 60 fields per request

Reads data/inputs/{cid}.json (build_inputs.py) and prompts/fields_{design}.json; writes
data/requests/{design}.jsonl, one line per request: {"key", "meta", "prompt", "body"}.
body = {"model": "clairic", "state": text, "fields": {...}, "options": {"return_state_text": true}}.
key = "{cid}:{opinion_id}:{chunk_start}". Chunks snap to the last newline before the limit and
overlap so a mention cut at a boundary is whole in one of them; to_predictions.py keeps each
span from the chunk that holds it away from the edge.
"""
import argparse
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "data", "inputs")
OUT = os.path.join(HERE, "data", "requests")
PROMPTS = os.path.join(HERE, "prompts")


def load_prompt(design):
    path = os.path.join(PROMPTS, f"fields_{design}.json")
    with open(path, "rb") as f:
        raw = f.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()[:12]


def chunks(text, size, overlap):
    if not size or len(text) <= size:
        return [(0, len(text))]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            nl = text.rfind("\n", start + size // 2, end)
            if nl > start:
                end = nl + 1
        out.append((start, end))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def fields_for(prompt, inventory, max_fields):
    """→ list of field maps: one map per request. A per-case design is split into parts of at most
    `max_fields` cases, because the server bills state × passes and refuses a request whose fields
    need more than one pass over a long state (D3 smoke: 141 fields × 27K-token state → 2 passes,
    97,736 tokens, HTTP 422 "over the limit of 65529")."""
    fixed = dict(prompt.get("fields") or {})
    pcf = prompt.get("per_case_field")
    if not pcf:
        return [fixed] if fixed else []
    per_case = {}
    for case in inventory:
        clause = pcf["citation_clause_template"].format(citation=case["citation"]) if case["citation"] else ""
        name = case["name"] or f"inventory case {case['id']}"
        per_case[pcf["name_template"].format(id=case["id"])] = {
            "type": "list", "items": "text",
            "instructions": pcf["instructions_template"].format(name=name, citation_clause=clause)}
    names = list(per_case)
    step = max_fields or len(names) or 1
    return [dict(fixed, **{n: per_case[n] for n in names[i:i + step]}) for i in range(0, len(names), step)] or ([fixed] if fixed else [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True, choices=["d1", "d2", "d3"])
    ap.add_argument("--ids", nargs="*", help="cluster ids (default: every data/inputs/*.json)")
    ap.add_argument("--chunk-chars", type=int, default=0, help="0 = whole opinion per request")
    ap.add_argument("--overlap-chars", type=int, default=2000)
    ap.add_argument("--max-fields", type=int, default=0, help="D3: split the inventory into requests of at most N fields (0 = one request)")
    a = ap.parse_args()
    prompt, phash = load_prompt(a.design)
    ids = a.ids or sorted(p[:-5] for p in os.listdir(INPUTS) if p.endswith(".json"))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{a.design}.jsonl")
    n, n_chunked, n_fields, chars = 0, 0, 0, 0
    with open(path, "w", encoding="utf-8") as out:
        for cid in ids:
            with open(os.path.join(INPUTS, f"{cid}.json"), encoding="utf-8") as f:
                doc = json.load(f)
            field_parts = fields_for(prompt, doc.get("inventory") or [], a.max_fields)
            if not field_parts:
                print(f"{cid}: no fields (empty inventory?) — skipped")
                continue
            for op in doc["opinions"]:
                parts = chunks(op["text"], a.chunk_chars, a.overlap_chars)
                n_chunked += len(parts) > 1
                for start, end in parts:
                    for k, fields in enumerate(field_parts):
                        body = {"model": "clairic", "state": op["text"][start:end], "fields": fields,
                                "options": {"return_state_text": True}}
                        meta = {"cluster_id": cid, "idx": op["idx"], "opinion_id": op["id"], "type": op["type"],
                                "chunk_start": start, "chunk_end": end, "n_chunks": len(parts),
                                "opinion_chars": len(op["text"]), "field_part": k, "n_field_parts": len(field_parts),
                                "chars": end - start, "n_fields": len(fields), "gold_mentions": op["gold_mentions"]}
                        key = f"{cid}:{op['id']}:{start}" + (f":f{k}" if len(field_parts) > 1 else "")
                        out.write(json.dumps({"key": key, "meta": meta, "prompt": f"{prompt['version']}@{phash}",
                                              "body": body}, ensure_ascii=False) + "\n")
                        n += 1
                        n_fields += len(fields)
                        chars += end - start
    print(f"{a.design}: {n} requests ({n_chunked} opinions chunked), {chars:,} state chars "
          f"(≈ {chars / 4 / 1e6:.2f}M tokens at 4 chars/token → ≈ ${chars / 4 / 1e6 * 0.10:.3f} per pass at $0.10/M), "
          f"{n_fields / max(n, 1):.1f} fields per request → {path}")


if __name__ == "__main__":
    main()
