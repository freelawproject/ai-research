"""Join expert gold pairs to inventory ids: by CourtListener cluster id first, then by
normalized citation (volume + reporter + first page) when the gold authority has no
cluster id or its spans were not linked by eyecite."""
from collections import defaultdict

from common import citation_variants, name_tokens, normalize_citation


def inventory_index(inv):
    by_cid, by_norm = {}, defaultdict(set)
    for e in inv:
        if e.get("cited_cluster_id") is not None:
            by_cid.setdefault(str(e["cited_cluster_id"]), e["id"])
        for c in e.get("citations", []):
            n = normalize_citation(c)
            if n:
                by_norm[n].add(e["id"])
    return by_cid, by_norm


def _entry_text(e):
    return " ".join([e.get("name") or ""] + list(e.get("citations", [])))


def resolve_gold_to_inventory(inv, rows):
    """{row_index: inventory_id}: by CL cluster id, else by normalized citation, else by case
    name (both parties' distinctive tokens present in exactly one entry's name/mention texts)."""
    by_cid, by_norm = inventory_index(inv)
    texts = {e["id"]: name_tokens(_entry_text(e)) for e in inv}
    out = {}
    for i, r in enumerate(rows):
        if r["cited_cluster_id"] and r["cited_cluster_id"] in by_cid:
            out[i] = by_cid[r["cited_cluster_id"]]
            continue
        cands = set()
        for c in citation_variants(r["cited_citation"]):
            n = normalize_citation(c)
            if n:
                cands |= by_norm.get(n, set())
        if len(cands) == 1:
            out[i] = next(iter(cands))
            continue
        parts = [name_tokens(p) for p in (r["cited_name"] or "").split(" v. ")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        hits = {gid for gid, toks in texts.items() if all(p & toks for p in parts)}
        if len(hits) == 1:
            out[i] = next(iter(hits))
    return out
