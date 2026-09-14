"""Build the silver (eyecite-seeded) + gold citation extraction/coref dataset.

Silver: CLReplica `html_with_citations` (sampled by sample_cl_20k.py). Every
eyecite `<span class="citation">` becomes a mention; mentions that resolve to
the same CourtListener cluster (href /opinion/<id>/), the same reporter path
(/c/…), or the same eyecite data-id share a coref cluster; unresolved spans are
singletons. Spans that are plainly not case citations (statutes, regulations,
session laws, journals) are dropped — the model is CASE-ONLY (v1 scope).

Gold: the triage annotator's revised HTML (human-verified extraction + coref)
for the dev/test clusters of experiments_09022026 — `<section
data-opinion-type>` / `<cite group cited_ref>` / `<noncite>` — parsed the way
experiments_06142026/build_dataset.py did. Gold dev is the evaluation set;
gold test is carried but never scored by the training code.

Text normalization is identical for silver and gold: block tags -> newline,
tags stripped, entities unescaped, whitespace collapsed to single spaces;
offsets are into that text (what the model is fed).

    uv run python build_dataset.py --blocks "data/cl20k/blocks/*.jsonl.gz" \\
        --gold-revised ../experiments_09022026/triage/data/annotator/data/revised_html \\
        --gold-splits ../experiments_09022026/triage/inputs/splits.csv [--validate]
    # smoke on the triage sample instead of the 20K blocks:
    uv run python build_dataset.py --payload-dir ../experiments_09022026/triage/data/annotator/data/opinion_html …

Output: data/output/citations.jsonl — one record per rendered opinion:
  {citing_cluster_id, opinion_id, opinion_idx, opinion_type, split, text,
   mentions:[{start,end,text,source,cluster}], negatives:[{start,end,text,reason}]}
split ∈ train | val (VAL_FRAC of silver clusters, by cluster hash) | gold_dev | gold_test
      | train_llm (triage train clusters with Opus-corrected revised_html, --llm-train-ids)
      | train_gpt / val_gpt (round-2 GPT-seeded clusters, --gpt-train-revised/--gpt-train-ids).

Round 2 (GPT-seeded 10K, no silver blocks — warm start already carries them):
    uv run --no-project python build_dataset.py \\
        --gold-revised ../experiments_09022026/triage/data/annotator/data/revised_html \\
        --gold-splits ../experiments_09022026/triage/inputs/splits.csv \\
        --llm-train-ids ../experiments_09022026/triage/data/citation_seed/train_seeded_ids.txt \\
        --gpt-train-revised data/annotator_r2/data/revised_html --gpt-train-ids data/annotator_r2/ids_seeded.txt \\
        --out data/output/citations_r2.jsonl --validate
"""

import argparse
import csv
import glob
import gzip
import hashlib
import html as _html
import json
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "output", "citations.jsonl")

CIT_SPAN = re.compile(r'<span class="citation(?P<extra>[^"]*)"(?P<attrs>[^>]*)>(?P<inner>.*?)</span>', re.DOTALL)
HREF_ID = re.compile(r'href="/opinion/(\d+)/')
C_HREF = re.compile(r'href="(/c/[^"#]+)')
DATA_ID = re.compile(r'data-id="(\d+)"')
BLOCK = re.compile(r'</(p|div|blockquote|li|h[1-6]|section)>|<br\s*/?>', re.I)
TAG = re.compile(r"<[^>]+>")
# eyecite tags these too; they are not case citations (v1 scope = case only)
NONCASE = re.compile(r"§|\bU\.\s?S\.\s?C\.|C\.\s?F\.\s?R\.|\bStat\.\s|\bL\.\s?Rev\.|\bL\.\s?J\.|\bFed\.\s?Reg\.|"
                     r"\bRestatement\b|\bCong\.\s?Rec\.|\bU\.\s?S\.\s?C\.\s?A\.|\bAm\.\s?Jur\.|\bA\.\s?L\.\s?R\.|"
                     r"\bWright\s*&\s*Miller|\bJ\.\s?Legis\.", re.I)
SECTION = re.compile(r'<section[^>]*data-opinion-type="([^"]*)"[^>]*>(.*?)</section>', re.DOTALL)
GOLD_TAG = re.compile(r'<cite(?P<attrs>[^>]*)>(?P<cite>.*?)</cite>|<noncite>(?P<noncite>.*?)</noncite>', re.DOTALL)
GROUP = re.compile(r'group="([^"]*)"')
CHANGE = re.compile(r'change="([^"]*)"')
NONCITE_WRAP = re.compile(r"<noncite>(.*?)</noncite>", re.DOTALL)
S1, S2, S3 = "\x01", "\x02", "\x03"


def normalize(raw):
    """Collapse whitespace; return (text, raw_index -> text_index map)."""
    chars, mp, last_space = [], [0] * (len(raw) + 1), True
    for i, ch in enumerate(raw):
        mp[i] = len(chars)
        if ch.isspace():
            if last_space:
                continue
            chars.append(" ")
            last_space = True
        else:
            chars.append(ch)
            last_space = False
    mp[len(raw)] = len(chars)
    text = "".join(chars)
    # strip leading space (index map stays valid because only a leading space is dropped)
    if text.startswith(" "):
        text = text[1:]
        mp = [max(0, x - 1) for x in mp]
    return text.rstrip(), mp


def spans_to_records(raw_marked, spans_meta):
    """raw_marked: de-tagged text with S1<key>S2<inner>S3 sentinels; spans_meta: key -> meta.
    Returns (text, [(start, end, meta)]) in normalized-text coordinates."""
    out, i, hits = [], 0, []
    for m in re.finditer(f"{S1}(.*?){S2}(.*?){S3}", raw_marked, re.S):
        out.append(raw_marked[i:m.start()])
        start = sum(len(x) for x in out)
        inner = m.group(2)
        out.append(inner)
        hits.append((start, start + len(inner), spans_meta[m.group(1)]))
        i = m.end()
    out.append(raw_marked[i:])
    raw = "".join(out)
    text, mp = normalize(raw)
    res = []
    for s, e, meta in hits:
        ts, te = min(mp[s], len(text)), min(mp[e], len(text))
        while ts < te and text[ts] == " ":
            ts += 1
        while te > ts and text[te - 1] == " ":
            te -= 1
        if te > ts:
            res.append((ts, te, meta))
    return text, res


def strip_html_keep_marks(html):
    html = BLOCK.sub("\n", html)
    html = TAG.sub("", html)
    return _html.unescape(html)


def drop_combined(ops):
    seps = [o for o in ops if not str(o.get("type") or "").startswith("010")]
    return seps or ops


def silver_records(payload, split):
    """One CL cluster payload -> records (one per writing)."""
    cid = int(payload["cluster_id"])
    ops = drop_combined(payload["opinions"])
    recs, seed_to_gid, n_drop = [], {}, 0

    def gid_for(seed):
        if seed not in seed_to_gid:
            seed_to_gid[seed] = str(len(seed_to_gid) + 1)
        return seed_to_gid[seed]

    for oi, op in enumerate(ops):
        meta, idx = {}, [0]

        def annotate(m):
            key = f"{op['id']}:{idx[0]}"
            idx[0] += 1
            inner_html, attrs = m.group("inner"), m.group("attrs") or ""
            inner_text = _html.unescape(TAG.sub("", inner_html))
            if NONCASE.search(inner_text):
                return inner_html  # leave as plain text: not a case citation
            href, chref, did = HREF_ID.search(inner_html), C_HREF.search(inner_html), DATA_ID.search(attrs)
            seed = (f"c{href.group(1)}" if href else f"cite:{chref.group(1)}" if chref
                    else f"d{did.group(1)}" if did else f"solo:{key}")
            meta[key] = {"cluster": gid_for(seed), "resolved": bool(href)}
            return f"{S1}{key}{S2}{inner_html}{S3}"

        marked = CIT_SPAN.sub(annotate, op.get("html", ""))
        text, hits = spans_to_records(strip_html_keep_marks(marked), meta)
        mentions = [{"start": s, "end": e, "text": text[s:e], "source": "eyecite", "cluster": mt["cluster"]}
                    for s, e, mt in hits]
        recs.append({"citing_cluster_id": cid, "opinion_id": op["id"], "opinion_idx": oi,
                     "opinion_type": str(op.get("type") or ""), "split": split, "text": text,
                     "mentions": mentions, "negatives": []})
    return recs


def gold_records(html, cid, split):
    """Revised HTML -> records (mirrors experiments_06142026/build_dataset.parse_section)."""
    recs = []
    sections = SECTION.findall(html) or [("", html)]
    for oi, (otype, body) in enumerate(sections):
        body = NONCITE_WRAP.sub(lambda mm: mm.group(1) if "<cite" in mm.group(1) else mm.group(0), body)
        meta, parts, k = {}, [], 0
        pos = 0
        for m in GOLD_TAG.finditer(body):
            parts.append(body[pos:m.start()])
            key = f"g{k}"
            k += 1
            if m.group("cite") is not None:
                g = GROUP.search(m.group("attrs"))
                ch = CHANGE.search(m.group("attrs"))
                meta[key] = {"kind": "cite", "cluster": g.group(1) if g else "", "change": ch.group(1) if ch else ""}
                inner = m.group("cite")
            else:
                meta[key] = {"kind": "noncite"}
                inner = m.group("noncite")
            parts.append(f"{S1}{key}{S2}{inner}{S3}")
            pos = m.end()
        parts.append(body[pos:])
        text, hits = spans_to_records(strip_html_keep_marks("".join(parts)), meta)
        mentions = [{"start": s, "end": e, "text": text[s:e],
                     "source": "manual" if mt["change"] == "added" else "eyecite", "cluster": mt["cluster"]}
                    for s, e, mt in hits if mt["kind"] == "cite"]
        negatives = [{"start": s, "end": e, "text": text[s:e], "reason": "removed"}
                     for s, e, mt in hits if mt["kind"] == "noncite"]
        recs.append({"citing_cluster_id": int(cid), "opinion_id": f"{cid}-{oi}", "opinion_idx": oi,
                     "opinion_type": otype, "split": split, "text": text,
                     "mentions": sorted(mentions, key=lambda x: x["start"]), "negatives": negatives})
    return recs


def val_split(cid, frac):
    h = int(hashlib.sha1(str(cid).encode()).hexdigest(), 16) % 10_000
    return "val" if h < frac * 10_000 else "train"


def iter_payloads(blocks, payload_dir, pools):
    for pat in blocks or []:
        for path in sorted(glob.glob(pat)):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for ln in f:
                    r = json.loads(ln)
                    if pools and r.get("pool", "selected") not in pools:
                        continue
                    yield r
    if payload_dir:
        for fn in sorted(os.listdir(payload_dir)):
            if fn.endswith(".json") and not fn.endswith(".cl.json"):
                yield json.load(open(os.path.join(payload_dir, fn), encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", nargs="*", help="glob(s) of sample_cl_20k block files")
    ap.add_argument("--payload-dir", help="dir of {cid}.json payloads (smoke)")
    ap.add_argument("--pools", nargs="*", default=["selected", "topup"], help="sampler pools to use")
    ap.add_argument("--gold-revised", help="dir of revised_html/{cid}.html")
    ap.add_argument("--gold-splits", help="splits.csv (cluster_id, split=dev|test)")
    ap.add_argument("--llm-train-ids", help="ids file: train clusters whose revised_html carries LLM-corrected "
                                            "(Opus v5) grouping -> split train_llm (gold-format records, not silver)")
    ap.add_argument("--gpt-train-revised", help="round-2: dir of revised_html/{cid}.html exported from the GPT-seeded "
                                                "annotator root (export_r2_revised_html.sh)")
    ap.add_argument("--gpt-train-ids", help="round-2: ids file (annotator_r2/ids_seeded.txt) -> gold-format records "
                                            "tagged train_gpt, with --gpt-val-frac of the clusters (by hash) tagged val_gpt")
    ap.add_argument("--gpt-val-frac", type=float, default=0.02)
    ap.add_argument("--val-frac", type=float, default=0.02)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    stats = Counter()
    gold_ids = set()
    with open(a.out, "w", encoding="utf-8") as out:
        if a.gold_revised and a.gold_splits:
            sp = {r["cluster_id"]: r["split"] for r in csv.DictReader(open(a.gold_splits))}
            for cid, s in sp.items():
                if s not in ("dev", "test"):
                    continue
                path = os.path.join(a.gold_revised, f"{cid}.html")
                if not os.path.exists(path) or os.path.getsize(path) == 0:
                    stats["gold_missing_html"] += 1  # zero-byte files came from a viewer bug (2026-09-09); regenerate, do not emit empties
                    continue
                gold_ids.add(int(cid))
                for r in gold_records(open(path, encoding="utf-8").read(), cid, f"gold_{s}"):
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    stats[f"rec_gold_{s}"] += 1
                    stats[f"men_gold_{s}"] += len(r["mentions"])
        if a.gold_revised and a.llm_train_ids:
            for cid in open(a.llm_train_ids).read().split():
                path = os.path.join(a.gold_revised, f"{cid}.html")
                if not os.path.exists(path) or os.path.getsize(path) == 0:
                    stats["llm_train_missing_html"] += 1
                    continue
                gold_ids.add(int(cid))
                for r in gold_records(open(path, encoding="utf-8").read(), cid, "train_llm"):
                    if not r["mentions"]:
                        continue
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    stats["rec_train_llm"] += 1
                    stats["men_train_llm"] += len(r["mentions"])
        if a.gpt_train_revised and a.gpt_train_ids:
            # round-2 GPT-seeded clusters: gold-format records (the revised HTML carries
            # GPT's adds/removes/regroups); a hashed slice is held out as val_gpt so the
            # trainers can select on same-distribution labels instead of eyecite silver.
            for cid in open(a.gpt_train_ids).read().split():
                path = os.path.join(a.gpt_train_revised, f"{cid}.html")
                if not os.path.exists(path) or os.path.getsize(path) == 0:
                    stats["gpt_train_missing_html"] += 1
                    continue
                if int(cid) in gold_ids:
                    stats["gpt_train_skipped_gold"] += 1
                    continue
                gold_ids.add(int(cid))
                split = "val_gpt" if val_split(cid, a.gpt_val_frac) == "val" else "train_gpt"
                for r in gold_records(open(path, encoding="utf-8").read(), cid, split):
                    if not r["mentions"]:
                        stats["rec_empty_dropped"] += 1
                        continue
                    if a.validate:
                        for m in r["mentions"]:
                            assert r["text"][m["start"]:m["end"]] == m["text"], (cid, m)
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    stats[f"rec_{split}"] += 1
                    stats[f"men_{split}"] += len(r["mentions"])
                    stats[f"men_{split}_manual"] += sum(m["source"] == "manual" for m in r["mentions"])
                    stats[f"neg_{split}"] += len(r["negatives"])
                    stats[f"clusters_{split}"] += len({m["cluster"] for m in r["mentions"]})
        for payload in iter_payloads(a.blocks, a.payload_dir, set(a.pools)):
            cid = int(payload["cluster_id"])
            if cid in gold_ids:
                stats["silver_skipped_gold"] += 1
                continue
            split = val_split(cid, a.val_frac)
            for r in silver_records(payload, split):
                if not r["mentions"]:
                    stats["rec_empty_dropped"] += 1
                    continue
                if a.validate:
                    for m in r["mentions"]:
                        assert r["text"][m["start"]:m["end"]] == m["text"], (cid, m)
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                stats[f"rec_{split}"] += 1
                stats[f"men_{split}"] += len(r["mentions"])
                stats[f"clusters_{split}"] += len({m["cluster"] for m in r["mentions"]})
    # stats sit next to the output, named after it: citations.jsonl -> stats.json, citations_r2.jsonl -> stats_r2.json
    stats_path = os.path.join(os.path.dirname(a.out),
                              "stats" + os.path.basename(a.out).replace("citations", "", 1).replace(".jsonl", ".json"))
    json.dump(dict(stats), open(stats_path, "w"), indent=1)
    print(json.dumps(dict(stats), indent=1))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
