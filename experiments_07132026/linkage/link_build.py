"""Stage 2 — Build SCOTUS<->circuit links from the extracted snapshots (no DB).

Run from ai-research (stdlib only, no CL/AWS deps):
    uv run python link_build.py --data-dir ../data
    # or: python3 link_build.py --data-dir ../data

Reads scotus_linkable.json + circuit_records.jsonl (from link_extract.py), matches
each SCOTUS cluster to circuit cluster(s) on (court, cleaned docket token), and
writes:
  links.json          - every matched SCOTUS cluster -> circuit cluster(s) + meta + CL URLs
  links_summary.json  - counts by match type
  links_sample.json   - a stratified sample for spot-checking
  inspect_links.html  - self-contained page: sample as a table with clickable CL links

Match types (per matched SCOTUS cluster, on its most specific pair; C = # circuit
clusters on that pair, S = # SCOTUS clusters on it):
  one_to_one C==1,S==1 · one_to_many C>1,S==1 · many_to_one C==1,S>1 ·
  many_to_many C>1,S>1 · recoverable_by_cleaning (digit-core match only) · unmatched
Re-run after re-extracting to refresh everything.
"""

import argparse
import json
import os
import re

CL = "https://www.courtlistener.com"
MAX_CIRCUIT_PER_LINK = 25   # cap saved candidates per SCOTUS cluster
SAMPLE_PER_TYPE = 5


def slugify(s):
    s = re.sub(r"[^\w\s-]", "", (s or "").lower()).strip()
    return re.sub(r"[\s_-]+", "-", s)[:75] or "case"


def opinion_url(cluster_id, case_name):
    return f"{CL}/opinion/{cluster_id}/{slugify(case_name)}/"


def load_scotus(path):
    with open(path) as f:
        return json.load(f)


def build_indexes(jsonl_path):
    """exact (court,token)->[cluster_id], core (court,core)->[cluster_id]."""
    exact, coreidx = {}, {}
    with open(jsonl_path) as f:
        for line in f:
            r = json.loads(line)
            cid, court = r["cluster_id"], r["court"]
            for t in r["tokens"]:
                exact.setdefault((court, t), []).append(cid)
            for c in r["cores"]:
                coreidx.setdefault((court, c), []).append(cid)
    return exact, coreidx


def load_meta(jsonl_path, wanted):
    """Second pass: metadata for the circuit clusters we actually matched."""
    meta = {}
    with open(jsonl_path) as f:
        for line in f:
            r = json.loads(line)
            if r["cluster_id"] in wanted:
                meta[r["cluster_id"]] = {
                    "cluster_id": r["cluster_id"], "court": r["court"],
                    "case_name": r["case_name"], "date_filed": r["date_filed"],
                    "docket_number": r["docket_number"],
                }
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    args = ap.parse_args()
    d = args.data_dir
    scotus = load_scotus(os.path.join(d, "scotus_linkable.json"))
    circ_jsonl = os.path.join(d, "circuit_records.jsonl")
    print(f"scotus-linkable: {len(scotus)}; building circuit indexes...")
    exact, coreidx = build_indexes(circ_jsonl)
    print(f"exact keys {len(exact)}, core keys {len(coreidx)}")

    # SCOTUS-side pair counts (S) for cardinality.
    scotus_pairs = {}
    for r in scotus:
        for t in r["tokens"]:
            k = (r["appeal_from"], t)
            scotus_pairs[k] = scotus_pairs.get(k, 0) + 1

    counts = {k: 0 for k in
              ["one_to_one", "one_to_many", "many_to_one", "many_to_many",
               "recoverable_by_cleaning", "unmatched"]}
    links, wanted, unmatched_sample = [], set(), []

    for r in scotus:
        court = r["appeal_from"]
        exact_pairs = [(t, exact[(court, t)]) for t in r["tokens"]
                       if (court, t) in exact]
        if exact_pairs:
            # cardinality label comes from the most specific single docket pair,
            # but we LIST every matched circuit opinion across all lower-court
            # dockets (a consolidated appeal can name several).
            best_t, best_ids = min(exact_pairs, key=lambda p: len(p[1]))
            C, S = len(best_ids), scotus_pairs[(court, best_t)]
            mtype = ("one_to_one" if C == 1 and S == 1 else
                     "one_to_many" if C > 1 and S == 1 else
                     "many_to_one" if C == 1 and S > 1 else "many_to_many")
            cids = sorted({cid for _, ids in exact_pairs for cid in ids})
            n_dockets = len(exact_pairs)
        else:
            core_ids = []
            matched_cores = 0
            for c in r["cores"]:
                ids = coreidx.get((court, c))
                if ids:
                    core_ids += ids
                    matched_cores += 1
            if core_ids:
                mtype, cids = "recoverable_by_cleaning", sorted(set(core_ids))
                n_dockets = matched_cores
            else:
                mtype, cids, n_dockets = "unmatched", [], 0
        counts[mtype] += 1
        if mtype == "unmatched":
            if len(unmatched_sample) < SAMPLE_PER_TYPE:
                unmatched_sample.append({
                    "match_type": "unmatched",
                    "scotus": {"cluster_id": r["cluster_id"],
                               "case_name": r["case_name"],
                               "date_filed": r["date_filed"],
                               "docket_number": r["scotus_docket"],
                               "url": opinion_url(r["cluster_id"], r["case_name"])},
                    "appeal_from": court, "oci_docket": r["oci_docket"],
                    "oci_needs_llm": r.get("oci_needs_llm", False),
                    "n_circuit": 0, "circuit": []})
            continue
        capped = cids[:MAX_CIRCUIT_PER_LINK]
        wanted.update(capped)
        links.append({
            "match_type": mtype,
            "scotus": {"cluster_id": r["cluster_id"],
                       "case_name": r["case_name"],
                       "date_filed": r["date_filed"],
                       "docket_number": r["scotus_docket"],
                       "url": opinion_url(r["cluster_id"], r["case_name"])},
            "appeal_from": court,
            "oci_docket": r["oci_docket"],
            "oci_needs_llm": r.get("oci_needs_llm", False),
            "n_lower_dockets_matched": n_dockets,
            "n_circuit": len(cids),
            "circuit_cluster_ids": capped,
        })

    print("resolving circuit metadata...")
    meta = load_meta(circ_jsonl, wanted)
    for lk in links:
        lk["circuit"] = [
            {**meta[c], "url": opinion_url(c, meta[c]["case_name"])}
            for c in lk["circuit_cluster_ids"] if c in meta
        ]

    counts["matched_total"] = sum(
        counts[k] for k in ["one_to_one", "one_to_many",
                            "many_to_one", "many_to_many"])
    counts["scotus_linkable_total"] = len(scotus)
    with open(os.path.join(d, "links.json"), "w") as f:
        json.dump(links, f)
    with open(os.path.join(d, "links_summary.json"), "w") as f:
        json.dump(counts, f, indent=2)
    print(json.dumps(counts, indent=2))

    # Stratified sample for spot-checking (matched types + unmatched).
    sample, seen = [], {}
    for lk in links:
        t = lk["match_type"]
        if seen.get(t, 0) < SAMPLE_PER_TYPE:
            sample.append(lk)
            seen[t] = seen.get(t, 0) + 1
    sample += unmatched_sample
    with open(os.path.join(d, "links_sample.json"), "w") as f:
        json.dump(sample, f, indent=2)
    write_inspect_html(os.path.join(d, "inspect_links.html"), sample, counts)
    print(f"wrote links.json / links_summary.json / links_sample.json / "
          f"inspect_links.html to {d}")


def write_inspect_html(path, sample, counts):
    data = json.dumps(sample)
    tot = counts.get("matched_total", 0)
    summary = json.dumps(counts)
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCOTUS &#8594; Circuit — link spot-check</title>
<style>
:root{--bg:#f5f6f8;--surface:#fff;--ink:#17212e;--ink2:#465063;--muted:#7c8697;
--line:#e4e7ec;--c1:#2b6a99;--green:#1a7a52;--amber:#b9761b;--red:#b1442f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;line-height:1.5}
.wrap{max-width:1000px;margin:0 auto;padding:32px 20px 60px}
h1{font-size:1.4rem;margin:0 0 4px}.sub{color:var(--muted);font-size:.85rem;margin:0 0 20px}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 22px}
.chip{font-size:.78rem;background:var(--surface);border:1px solid var(--line);
border-radius:20px;padding:5px 12px;color:var(--ink2)}.chip b{color:var(--ink);font-variant-numeric:tabular-nums}
.rec{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
.mt{display:inline-block;font-size:.68rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
padding:3px 9px;border-radius:20px;margin-bottom:10px}
.one_to_one{background:color-mix(in srgb,var(--green) 16%,#fff);color:var(--green)}
.many_to_one{background:color-mix(in srgb,var(--green) 12%,#fff);color:var(--green)}
.one_to_many{background:color-mix(in srgb,var(--amber) 18%,#fff);color:var(--amber)}
.many_to_many{background:color-mix(in srgb,var(--red) 15%,#fff);color:var(--red)}
.recoverable_by_cleaning{background:color-mix(in srgb,var(--c1) 15%,#fff);color:var(--c1)}
.side{font-size:.9rem}.side .lbl{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
a{color:var(--c1);text-decoration:none}a:hover{text-decoration:underline}
.dk{color:var(--muted);font-size:.82rem;font-variant-numeric:tabular-nums}
.arrow{color:var(--muted);margin:8px 0 6px;font-size:.8rem}
.circ{margin:5px 0 0 14px;padding-left:12px;border-left:2px solid var(--line)}
.llm{font-size:.68rem;color:var(--amber);border:1px solid color-mix(in srgb,var(--amber) 40%,#fff);
border-radius:4px;padding:1px 5px;margin-left:6px}
</style></head><body><div class="wrap">
<h1>SCOTUS &#8594; Circuit — link spot-check</h1>
<p class="sub">A stratified sample of the linked pairs. Click any case to open its CourtListener page and verify the match.</p>
<div class="counts" id="counts"></div><div id="list"></div>
<script>
const SAMPLE=__DATA__;const COUNTS=__SUMMARY__;const TOTAL=__TOTAL__;
const order=["one_to_one","many_to_one","one_to_many","many_to_many","recoverable_by_cleaning","unmatched"];
const nice={one_to_one:"one-to-one",many_to_one:"many-to-one",one_to_many:"one-to-many",
many_to_many:"many-to-many",recoverable_by_cleaning:"recoverable by cleaning",unmatched:"unmatched"};
document.getElementById("counts").innerHTML=order.filter(k=>k in COUNTS).map(k=>
`<span class="chip">${nice[k]}: <b>${COUNTS[k].toLocaleString()}</b></span>`).join("")+
`<span class="chip">matched total: <b>${(COUNTS.matched_total||0).toLocaleString()}</b> of ${(COUNTS.scotus_linkable_total||0).toLocaleString()} linkable</span>`;
document.getElementById("list").innerHTML=SAMPLE.map(r=>{
 const s=r.scotus;
 const surl=`https://www.courtlistener.com/?q=${encodeURIComponent('"'+(r.oci_docket||"")+'"')}&type=o&court=${r.appeal_from}`;
 const circ=(r.circuit&&r.circuit.length)?r.circuit.map(c=>`<div class="circ side"><span class="lbl">${c.court}</span>
   <a href="${c.url}" target="_blank" rel="noopener">${c.case_name||"(unnamed)"}</a>
   <span class="dk">· ${c.docket_number||""} · ${c.date_filed||""}</span></div>`).join("")
   :`<div class="dk circ">No matching circuit opinion — <a href="${surl}" target="_blank" rel="noopener">search ${r.appeal_from} on CourtListener</a> (no result).</div>`;
 const more=(r.n_circuit||0)>((r.circuit||[]).length)?`<div class="dk circ">…and ${r.n_circuit-r.circuit.length} more</div>`:"";
 return `<div class="rec"><span class="mt ${r.match_type}">${nice[r.match_type]}</span>
  <div class="side"><span class="lbl">SCOTUS</span>
   <a href="${s.url}" target="_blank" rel="noopener">${s.case_name||"(unnamed)"}</a>
   <span class="dk">· ${s.docket_number||""} · ${s.date_filed||""}</span>
   ${r.oci_needs_llm?'<span class="llm">docket needs LLM cleaning</span>':''}</div>
  <div class="arrow">appealed from <b>${r.appeal_from}</b>, lower-court docket <b>${r.oci_docket}</b> &#8594;</div>
  ${circ}${more}</div>`;
}).join("");
</script></div></body></html>"""
    html = (html.replace("__DATA__", data)
                .replace("__SUMMARY__", summary)
                .replace("__TOTAL__", str(tot)))
    with open(path, "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
