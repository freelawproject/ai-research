"""Build a citator-benchmark viewer data root for the triage sample, so the
viewer's Grouping annotator can be used for the citation-extraction /
coreference correction pass without touching the benchmark's own ledgers.

Reads the sampler output (default data/triage_sample/):
    opinion_html/{cluster_id}.json, sample_metadata.csv, authority_metadata.csv

Writes (default data/annotator/):
    data/opinion_html/{cluster_id}.json     copied verbatim
    data/assignments_long.csv               one UNLABELED row per (citing,
                                            cited) pair — the viewer needs a
                                            row to know an authority exists
    data/predictions.csv                    header only
    data/expert_aliases.csv                 header only
    data/grouping_overrides/                annotator edits land here
    overrides/citing_metadata.csv           court/case/year per citing cluster
                                            (drives the SCOTUS/FED/STATE group)
    overrides/cited_metadata.csv            header only
    overrides/excluded_cases.csv            header only
    overrides/status_notes.yaml             empty
    overrides/label_map.csv                 copied from the benchmark repo

Then run the viewer against it with ./run_annotator.sh. Re-runnable: rewrites
the CSVs, never touches grouping_overrides/ (the annotator's work).

    python make_annotator_data.py [--sample-dir D] [--out D]

Partial mode — while the sampler is still running it has written
opinion_html/*.json but no CSVs yet. `--html-only` builds the root from the
JSON files alone: authorities are derived from the CourtListener links inside
html_with_citations (cluster id from the href, case name from the link's
aria-description); court metadata is unknown so every cluster is filed under
--default-court (default scotus, the first group the sampler processes). Rerun
in normal mode once the CSVs exist; it overwrites the CSVs and keeps
grouping_overrides/.

    python make_annotator_data.py --html-only [--default-court scotus]
"""
import argparse
import csv
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

FLP = Path(__file__).resolve().parents[4]      # the workspace holding both checkouts
# the citator-benchmark checkout, a sibling of the ai-research checkout;
# CITATOR_BENCH overrides
BENCH_ROOT = Path(os.environ.get("CITATOR_BENCH", FLP / "citator-benchmark"))
GROUP_OF = {"scotus": "SCOTUS", "circuit": "FED", "state_high": "STATE"}
ASSIGN_FIELDS = ["group", "pass_type", "citing_cluster_id", "cited_cluster_id",
                 "cited_ref", "expert", "label_raw", "label", "notes",
                 "round_zip", "group_zip", "source_file", "ingested",
                 "citing_url", "cited_url", "citing_court_name",
                 "cited_court_name", "cited_case_name_short", "cited_case_name",
                 "cited_citations"]
PRED_FIELDS = ["citing_cluster_id", "cited_cluster_id", "treatment",
               "case_history", "severity", "opinion_type", "quote", "rationale",
               "citation_string", "source", "ingested"]
CITING_META_FIELDS = ["cluster_id", "source", "court", "case_name", "year",
                      "num_authorities", "num_unmatched_citations",
                      "opinion_text_length"]
CL_LINK = re.compile(
    r'<a href="/opinion/(\d+)/[^"]*"(?:\s+aria-description="Citation for case: ([^"]*)")?[^>]*>(.*?)</a>',
    re.S)
TAG = re.compile(r"<[^>]+>")


def rows_from_html(sample, default_court):
    """Partial mode: (meta rows, authority rows) derived from opinion_html/*.json."""
    meta, auth = [], []
    for path in sorted((sample / "opinion_html").glob("*.json"), key=lambda p: int(p.stem)):
        d = json.load(open(path, encoding="utf-8"))
        cid = str(d["cluster_id"])
        cited = {}
        n_chars = 0
        for o in d["opinions"]:
            html = o.get("html", "")
            n_chars += len(TAG.sub("", html))
            for m in CL_LINK.finditer(html):
                ccid, name, text = m.group(1), m.group(2) or "", TAG.sub("", m.group(3)).strip()
                if ccid == cid:
                    continue
                e = cited.setdefault(ccid, {"name": name, "cites": []})
                if name and not e["name"]:
                    e["name"] = name
                if text and text not in e["cites"]:
                    e["cites"].append(text)
        meta.append({"cluster_id": cid, "court": default_court,
                     "court_group": {"scotus": "scotus"}.get(default_court, "circuit" if default_court.startswith("ca") else "state_high"),
                     "case_name": "", "year": "", "text_length": n_chars,
                     "selection": "partial", "n_cited_clusters": len(cited)})
        for ccid, e in cited.items():
            auth.append({"citing_cluster_id": cid, "cited_cluster_id": ccid,
                         "cited_case_name": e["name"],
                         "cited_case_citations": "; ".join(e["cites"][:6])})
    return meta, auth


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-dir", default="data/triage_sample")
    ap.add_argument("--out", default="data/annotator")
    ap.add_argument("--html-only", action="store_true",
                    help="build from opinion_html/*.json alone (sampler still running)")
    ap.add_argument("--default-court", default="scotus",
                    help="court id assumed for every cluster in --html-only mode")
    a = ap.parse_args()
    sample, out = Path(a.sample_dir), Path(a.out)
    data, overrides = out / "data", out / "overrides"
    today = date.today().isoformat()

    if a.html_only:
        meta, auth = rows_from_html(sample, a.default_court)
        print(f"html-only mode: {len(meta)} clusters from opinion_html/, "
              f"{len(auth)} authorities from CL links; court assumed {a.default_court}")
    else:
        meta = list(csv.DictReader(open(sample / "sample_metadata.csv", encoding="utf-8")))
        auth = list(csv.DictReader(open(sample / "authority_metadata.csv", encoding="utf-8")))
    meta_by = {r["cluster_id"]: r for r in meta}

    # opinion html
    (data / "opinion_html").mkdir(parents=True, exist_ok=True)
    n_html = 0
    for r in meta:
        src = sample / "opinion_html" / f"{r['cluster_id']}.json"
        if src.exists():
            shutil.copyfile(src, data / "opinion_html" / src.name)
            n_html += 1

    # assignments: one unlabeled row per authority pair
    rows = []
    n_auth_by = {}
    for p in auth:
        m = meta_by.get(p["citing_cluster_id"])
        if not m:
            continue
        n_auth_by[p["citing_cluster_id"]] = n_auth_by.get(p["citing_cluster_id"], 0) + 1
        group = GROUP_OF.get(m["court_group"], "STATE")
        rows.append({
            "group": group, "pass_type": "full",
            "citing_cluster_id": p["citing_cluster_id"],
            "cited_cluster_id": p["cited_cluster_id"],
            "cited_ref": p["cited_cluster_id"],
            "expert": "", "label_raw": "", "label": "", "notes": "",
            "round_zip": "triage_sample", "group_zip": group,
            "source_file": "authority_metadata.csv", "ingested": today,
            "citing_url": f"https://www.courtlistener.com/opinion/{p['citing_cluster_id']}/x/",
            "cited_url": f"https://www.courtlistener.com/opinion/{p['cited_cluster_id']}/x/",
            "citing_court_name": "", "cited_court_name": "",
            "cited_case_name_short": "", "cited_case_name": p["cited_case_name"],
            "cited_citations": p["cited_case_citations"],
        })
    write_csv(data / "assignments_long.csv", rows, ASSIGN_FIELDS)
    write_csv(data / "predictions.csv", [], PRED_FIELDS)
    write_csv(data / "expert_aliases.csv", [], ["expert", "alias"])
    (data / "grouping_overrides").mkdir(exist_ok=True)

    write_csv(overrides / "citing_metadata.csv", [{
        "cluster_id": r["cluster_id"], "source": r["selection"],
        "court": r["court"], "case_name": r["case_name"], "year": r["year"],
        "num_authorities": n_auth_by.get(r["cluster_id"], 0),
        "num_unmatched_citations": "", "opinion_text_length": r["text_length"],
    } for r in meta], CITING_META_FIELDS)
    write_csv(overrides / "cited_metadata.csv", [],
              ["cited_cluster_id", "court", "court_name", "date_filed"])
    write_csv(overrides / "excluded_cases.csv", [],
              ["citing_cluster_id", "reason", "excluded_on"])
    (overrides / "status_notes.yaml").write_text("{}\n")
    lm = BENCH_ROOT / "overrides" / "label_map.csv"
    if lm.exists():
        shutil.copyfile(lm, overrides / "label_map.csv")

    print(f"annotator root: {out.resolve()}")
    print(f"  citing clusters {len(meta)} | opinion html copied {n_html} | "
          f"authority rows {len(rows)}")
    missing = [r["cluster_id"] for r in meta if not (data / "opinion_html" / f"{r['cluster_id']}.json").exists()]
    if missing:
        print(f"  WARNING: {len(missing)} clusters without opinion html: {missing[:10]}")


if __name__ == "__main__":
    main()
