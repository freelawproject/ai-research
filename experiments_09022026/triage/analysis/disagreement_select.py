"""Select the places where the encoder disagrees with the CURRENT labels of an
annotator root and render them as adjudication inputs for an Opus pass.

Round-3 seeding idea: instead of re-seeding whole opinions, point Opus at the
spots where two independent systems — the eyecite+GPT label the encoder was
trained on, and the encoder's own end-to-end prediction — disagree. On dev the
disagreement flag points at 82% of GPT's false positives and 71% of its misses
(analysis/disagreement_dev.py); a perfect adjudicator there lifts GPT's mention
F1 0.972 -> 0.993.

Inputs: an encoder prediction file (model/inference.py over the root's
records, e.g. --split train,validation) and the root itself (CITSEED_ANNOT /
CITSEED_OUT, as for citation_seed.py). The "current" state is State(cid, False):
eyecite seed + the GPT overrides in that root.

For every opinion with at least one disagreement this writes, under
<CITSEED_OUT>/inputs_adjudicate/ (or --inputs-dir):
  {cid}.txt        the fixer input rendered from the CURRENT state (same
                   <citingCase>/<citedCaseInventory>/<opinion> as `prepare`, so
                   mention ids are the current tags) + a <disagreements> block
  {cid}.map.json   the mention map `apply` needs (+ an "adjudicate" summary)
  summary.csv, ids_flagged.txt (most disagreements first), stats.json

Then: runners/bedrock_batch.sh export --name adj_r3 --inputs-dir <that dir>
--prompt prompts/triage_citation_fixer_v5d.md --ids $(cat .../ids_flagged.txt)
-> submit / fetch -> lib/citation_seed.py apply --inputs-dir <that dir> --out-dir …

    CITSEED_ANNOT=…/experiments_09092026/data/annotator_r2 CITSEED_OUT=…/data/citation_seed_r2 \\
    python3 analysis/disagreement_select.py --pred …/experiments_09092026/data/output/pred_train_validation_r4_rule.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, HERE)
import citation_seed as CS  # noqa: E402
from citation_seed import ANNOT, OUT, State, _match, render_input  # noqa: E402
from disagreement_dev import kind, merge_windows  # noqa: E402
from score_encoder import EncoderState  # noqa: E402

CTX_CHARS = 55


# citation_seed re-reads two CSVs per State; a 10K-opinion root needs them once.
_removed_cache = {}
_meta_cache = {}


def _script_removed_cached(cid):
    if not _removed_cache:
        p = os.path.join(ANNOT, "overrides", "citation_changes.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("by") == "script" and r.get("action") == "removed":
                    _removed_cache.setdefault(r["citing_cluster_id"], set()).add(r["ref"])
        _removed_cache.setdefault("__loaded__", set())
    return set(_removed_cache.get(str(cid), set()))


def _meta_of_cached(cid):
    if not _meta_cache:
        p = os.path.join(ANNOT, "overrides", "citing_metadata.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                _meta_cache[r["cluster_id"]] = r
        _meta_cache.setdefault("__loaded__", {})
    return _meta_cache.get(str(cid), {})


CS.script_removed = _script_removed_cached
CS.meta_of = _meta_of_cached


def _q(text):
    """quoted span text on one line"""
    return re.sub(r"\s+", " ", text).strip()


def _ctx(text, s, e, w=CTX_CHARS):
    a, b = max(0, s - w), min(len(text), e + w)
    return re.sub(r"\s+", " ", text[a:s] + "⟦" + text[s:e] + "⟧" + text[e:b]).strip()


def dedupe_mentions(ms):
    """GPT's applied state can hold the same manual span several times (one `add`
    per `before` context that resolved to the same occurrence); the dataset
    export dedupes them, so dedupe here too or each extra copy becomes a
    spurious current_only item (4% of items in the 2026-09-19 run)."""
    seen, out = set(), []
    for m in ms:
        if (m[0], m[1], m[2]) not in seen:
            seen.add((m[0], m[1], m[2]))
            out.append(m)
    return out


def compute_items(cur, enc, key2m=None):
    """Structured disagreements between the CURRENT state and the encoder.

    Returns (items, ms_c, ms_e, c2e): items are dicts with op, s, e, type
    (current_only | encoder_only | grouping), kind, text, cur_ref (mN when
    key2m maps it), cur_gid, enc_gid, desc (the encoder's side, one phrase)
    and ctx; sorted by position. ms_c is deduped."""
    key2m = key2m or {}
    ms_c, ms_e = dedupe_mentions(cur.mentions()), enc.mentions()
    c2e = dict(_match(ms_c, ms_e))
    e2c = {v: k for k, v in c2e.items()}
    items = []

    def mref(ic):
        m = ms_c[ic]
        n = key2m.get(m[5])
        return f"m{n}" if n else f'"{m[0]}:{m[1]}"'

    def qt(m):
        return _q(cur.texts[m[0]][m[1]:m[2]])

    for ic, m in enumerate(ms_c):
        if ic in c2e:
            continue
        items.append({"op": m[0], "s": m[1], "e": m[2], "type": "current_only", "kind": kind(qt(m)), "text": qt(m),
                      "cur_ref": mref(ic), "cur_gid": m[3], "enc_gid": None, "desc": "not a case mention",
                      "ctx": _ctx(cur.texts[m[0]], m[1], m[2])})
    enc_groups = defaultdict(list)
    for ie, m in enumerate(ms_e):
        enc_groups[m[3]].append(ie)
    for ie, m in enumerate(ms_e):
        if ie in e2c:
            continue
        mates = Counter(ms_c[e2c[j]][3] for j in enc_groups[m[3]] if j in e2c)
        hint = f"groups it with {mates.most_common(1)[0][0]}" if mates else "new case"
        items.append({"op": m[0], "s": m[1], "e": m[2], "type": "encoder_only", "kind": kind(qt(m)), "text": qt(m),
                      "cur_ref": None, "cur_gid": None, "enc_gid": m[3], "desc": f'case mention "{qt(m)}", {hint}',
                      "ctx": _ctx(cur.texts[m[0]], m[1], m[2])})
    shared = sorted(c2e, key=lambda i: (ms_c[i][0], ms_c[i][1]))
    for a in range(len(shared)):
        ia = shared[a]
        merge_with, split_from = [], []
        for b in range(a):
            ib = shared[b]
            same_c = ms_c[ia][3] == ms_c[ib][3]
            same_e = ms_e[c2e[ia]][3] == ms_e[c2e[ib]][3]
            if same_c == same_e:
                continue
            (merge_with if same_e else split_from).append(ib)
        if not merge_with and not split_from:
            continue
        m = ms_c[ia]
        parts = []
        if merge_with:
            ex = merge_with[-1]
            parts.append(f'same case as {mref(ex)} "{qt(ms_c[ex])}" ({ms_c[ex][3]})'
                         + (f" and {len(merge_with) - 1} more" if len(merge_with) > 1 else ""))
        if split_from:
            ex = split_from[-1]
            parts.append(f'NOT the same case as {mref(ex)} "{qt(ms_c[ex])}"'
                         + (f" and {len(split_from) - 1} more" if len(split_from) > 1 else ""))
        items.append({"op": m[0], "s": m[1], "e": m[2], "type": "grouping", "kind": kind(qt(m)), "text": qt(m),
                      "cur_ref": mref(ia), "cur_gid": m[3], "enc_gid": ms_e[c2e[ia]][3], "desc": "; ".join(parts),
                      "ctx": _ctx(cur.texts[m[0]], m[1], m[2])})
    items.sort(key=lambda x: (x["op"], x["s"]))
    return items, ms_c, ms_e, c2e


def item_line(x):
    cur_part = f'{x["cur_ref"]} "{x["text"]}" in {x["cur_gid"]}' if x["type"] != "encoder_only" else "untagged"
    return f'{x["kind"]} | current: {cur_part} | encoder: {x["desc"]} | {x["ctx"]}'


def select(cid, pred, ctx, max_items):
    cur = State(cid, False)
    text, mp = render_input(cur, CS.meta_of(cid))
    key2m = {v["key"]: m for m, v in mp["mentions"].items()}          # occ key / manual id -> mention number
    enc = EncoderState(cid, pred[str(cid)], cur)
    items, ms_c, ms_e, _ = compute_items(cur, enc, key2m)
    truncated = max(0, len(items) - max_items)
    kept = items[:max_items]
    kinds = Counter(x["type"] for x in items)
    wins = merge_windows([(x["op"], x["s"], x["e"]) for x in items], ctx)
    block = ""
    if kept:
        lines = [f'<disagreements n="{len(kept)}"' + (f' truncated="{truncated}"' if truncated else "") + ">"]
        for i, x in enumerate(kept, 1):
            lines.append(f"d{i} | {x['type']} | {item_line(x)}")
        lines.append("</disagreements>")
        block = "\n".join(lines) + "\n"
    summary = {"n_items": len(items), "n_kept": len(kept), "truncated": truncated, "kinds": dict(kinds),
               "n_windows": len(wins), "window_chars": sum(e - s for _, s, e in wins),
               "opinion_chars": sum(len(t) for t in cur.texts.values()),
               "n_current_mentions": len(ms_c), "n_encoder_mentions": len(ms_e)}
    return text, block, mp, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="inference.py output over this root's records")
    ap.add_argument("--ids", help="file of cluster ids to consider (default: every id in --pred)")
    ap.add_argument("--ctx", type=int, default=400, help="chars either side of a flagged span for the window estimate")
    ap.add_argument("--max-items", type=int, default=80, help="cap on <disagreements> lines per opinion")
    ap.add_argument("--inputs-dir", default=os.path.join(OUT, "inputs_adjudicate"))
    ap.add_argument("--limit", type=int, help="stop after this many opinions (smoke)")
    a = ap.parse_args()
    pred = json.load(open(a.pred, encoding="utf-8"))
    ids = [l.strip() for l in open(a.ids) if l.strip()] if a.ids else sorted(pred, key=int)
    if a.limit:
        ids = ids[:a.limit]
    os.makedirs(a.inputs_dir, exist_ok=True)
    rows, tot = [], Counter()
    for n, cid in enumerate(ids, 1):
        if str(cid) not in pred:
            continue
        if not os.path.exists(os.path.join(ANNOT, "data", "opinion_html", f"{cid}.json")):
            tot["no_payload"] += 1
            continue
        text, block, mp, summ = select(cid, pred, a.ctx, a.max_items)
        tot["opinions"] += 1
        tot["items"] += summ["n_items"]
        tot["windows"] += summ["n_windows"]
        tot["window_chars"] += summ["window_chars"]
        tot["opinion_chars"] += summ["opinion_chars"]
        for k, v in summ["kinds"].items():
            tot[f"kind_{k}"] += v
        if not block:
            continue
        tot["flagged"] += 1
        tot["flagged_chars"] += summ["opinion_chars"]
        with open(os.path.join(a.inputs_dir, f"{cid}.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n" + block)
        mp["seed_state"] = False
        mp["adjudicate"] = summ
        with open(os.path.join(a.inputs_dir, f"{cid}.map.json"), "w", encoding="utf-8") as f:
            json.dump(mp, f, indent=1)
        rows.append({"cid": cid, **{k: v for k, v in summ.items() if k != "kinds"}, **summ["kinds"]})
        if n % 500 == 0:
            print(f"  {n}/{len(ids)} …", file=sys.stderr)
    rows.sort(key=lambda r: -r["n_items"])
    if rows:
        keys = sorted({k for r in rows for k in r}, key=lambda k: (k != "cid", k))
        with open(os.path.join(a.inputs_dir, "summary.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        with open(os.path.join(a.inputs_dir, "ids_flagged.txt"), "w") as f:
            f.write("\n".join(str(r["cid"]) for r in rows) + "\n")
    stats = dict(tot)
    with open(os.path.join(a.inputs_dir, "stats.json"), "w") as f:
        json.dump(stats, f, indent=1)
    n = max(1, tot["opinions"])
    print(f"== {tot['opinions']} opinions scanned ({tot['no_payload']} without payload); {tot['flagged']} flagged "
          f"({tot['flagged'] / n:.1%}); {tot['items']} items ({tot['items'] / n:.1f}/opinion); "
          f"{tot['windows']} windows = {tot['window_chars']:,} of {tot['opinion_chars']:,} chars "
          f"({tot['window_chars'] / max(1, tot['opinion_chars']):.1%})")
    print("   items by kind:", {k[5:]: v for k, v in stats.items() if k.startswith("kind_")})
    print(f"   flagged opinions hold {tot['flagged_chars']:,} chars ≈ {tot['flagged_chars'] / 3.6 / 1e6:.2f}M input tokens "
          f"if sent whole to Opus")
    print(f"   wrote {len(rows)} inputs -> {a.inputs_dir} (summary.csv, ids_flagged.txt, stats.json)")


if __name__ == "__main__":
    main()
