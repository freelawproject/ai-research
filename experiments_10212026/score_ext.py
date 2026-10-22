"""Score the external benchmark (Kirschenfeld + Hellyer pairs) against the two-stage pipeline.

Joins each labelled pair to the citing cluster's inventory (cited cluster id → normalised
citation → case name, as goldmap.py), reads the gate (g6_ext ∪ g6b_ext) and labeler
(l_sonnet3_ext) outputs, and writes data/eval_pairs.csv plus summary tables (markdown) to stdout.

Binary: a side is negative when its label is anything but Cited by / Affirmed / cert strings;
pipeline negative = applied OR any recognized treatment negative.
Granular: the annotator's FLP label (crosswalk) vs the pipeline's labels — exact base label in
the same class, base label in any class, and severity tier of the most severe pipeline label.
"""

import csv
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
P0911 = os.path.join(HERE, "..", "experiments_09112026")
sys.path.insert(0, P0911)
from common import TIER_RANK, base, tier_of  # noqa: E402
from goldmap import resolve_gold_to_inventory  # noqa: E402

DATA = os.path.join(P0911, "data")
GATES = ("g6_ext", "g6b_ext")
LABEL = "l_sonnet3_ext"
NOT_NEG = ("Cited by", "Affirmed", "Cert")


def read_json(p, default=None):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def negative(label):
    label = (label or "").strip()
    return bool(label) and not label.startswith(NOT_NEG) and label != "Other treatment"


def ids_of(lst):
    return {str(x if not isinstance(x, dict) else x.get("id")): (x.get("confidence") if isinstance(x, dict) else "")
            for x in (lst or [])}


def most_severe(labels):
    labels = [l for l in labels if l]
    if not labels:
        return ""
    return max(labels, key=lambda l: TIER_RANK.get(tier_of(l) or "Neutral", 0))


def main():
    pairs = list(csv.DictReader(open(os.path.join(HERE, "data", "pairs.csv"), encoding="utf-8")))
    by_citing = defaultdict(list)
    for i, r in enumerate(pairs):
        by_citing[r["citing_cluster_id"]].append(i)
    out = [dict(r) for r in pairs]
    for citing, idxs in by_citing.items():
        inv = read_json(os.path.join(DATA, "inputs", f"{citing}.inventory.json"), [])
        rows = [{"cited_cluster_id": pairs[i]["cited_cluster_id"], "cited_citation": pairs[i]["cited_cl_citation"],
                 "cited_name": pairs[i]["cited_cl_case_name"]} for i in idxs]
        joined = resolve_gold_to_inventory(inv, rows)
        flagged, recog = {}, set()
        for g in GATES:
            o = read_json(os.path.join(DATA, "runs", g, "outputs", f"{citing}.json"), {}) or {}
            for k, conf in ids_of(o.get("flagged")).items():
                if conf in ("medium", "high"):
                    flagged[k] = conf
            recog |= set(ids_of(o.get("recognized")))
        lab = read_json(os.path.join(DATA, "runs", LABEL, "outputs", f"{citing}.json"), {}) or {}
        lab_by_id = {str(c["id"]): c for c in lab.get("citedCases", [])}
        for j, i in enumerate(idxs):
            r = out[i]
            iid = joined.get(j)
            r["inv_id"] = str(iid) if iid is not None else ""
            if iid is None:
                r.update(join="unjoined", gate="", pipeline_applied="", pipeline_opinion_type="",
                         pipeline_recognized="", pipeline_negative="", pipeline_most_severe="")
                continue
            r["join"] = "id" if any(str(e.get("cited_cluster_id")) == r["cited_cluster_id"] for e in inv if str(e["id"]) == str(iid)) else "citation/name"
            r["gate"] = "flagged" if str(iid) in flagged else ("recognized" if str(iid) in recog else "not flagged")
            c = lab_by_id.get(str(iid))
            applied = c["treatment"] if c else "Cited by"
            rec = [t["treatment"] for t in (c.get("recognizedTreatments") or [])] if c else []
            r["pipeline_applied"] = applied
            r["pipeline_opinion_type"] = (c or {}).get("opinionType", "")
            r["pipeline_recognized"] = "; ".join(rec)
            r["pipeline_negative"] = str(negative(applied) or any(negative(t) for t in rec))
            r["pipeline_most_severe"] = most_severe([applied] + rec)
            # granular
            ann = r["label_granular_flp"]
            if ann:
                ann_base, ann_class = base(ann), r["label_class"]
                pipe_same_class = applied if ann_class in ("applied", "direct_history") else ""
                same = (base(pipe_same_class) == ann_base) if ann_class in ("applied", "direct_history") else (ann_base in {base(t) for t in rec})
                anyc = ann_base in ({base(applied)} | {base(t) for t in rec})
                r["gran_exact_same_class"] = str(same)
                r["gran_base_any_class"] = str(anyc)
                r["gran_tier_match"] = str(tier_of(ann) == tier_of(r["pipeline_most_severe"] or "Cited by"))
            else:
                r["gran_exact_same_class"] = r["gran_base_any_class"] = r["gran_tier_match"] = ""
    fields = list(out[0].keys())
    with open(os.path.join(HERE, "data", "eval_pairs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)

    # ---- summaries
    for src in ("kirschenfeld", "hellyer"):
        rs = [r for r in out if r["source"] == src]
        jd = [r for r in rs if r["join"] != "unjoined"]
        print(f"\n### {src}: {len(rs)} pairs, {len(jd)} joined ({Counter(r['join'] for r in jd).most_common()}), {len(rs)-len(jd)} unjoined")
        print("gate:", Counter(r["gate"] for r in jd).most_common())
        ann_neg = [r for r in jd if r["label_binary"] == "negative"]
        print(f"annotator negative {len(ann_neg)} | ambiguous {sum(1 for r in jd if r['label_binary']=='ambiguous')} | not_negative {sum(1 for r in jd if r['label_binary']=='not_negative')}")
        pn = [r for r in jd if r["pipeline_negative"] == "True"]
        print(f"pipeline negative {len(pn)} (applied {sum(1 for r in pn if negative(r['pipeline_applied']))}, recognized-only {sum(1 for r in pn if not negative(r['pipeline_applied']))})")
        for cat in ("negative", "not_negative", "ambiguous"):
            sub = [r for r in jd if r["label_binary"] == cat]
            if sub:
                print(f"  annotator {cat:12} n={len(sub):3} -> pipeline negative {sum(1 for r in sub if r['pipeline_negative']=='True')}, not negative {sum(1 for r in sub if r['pipeline_negative']!='True')}")
        gr = [r for r in jd if r["label_granular_flp"]]
        print(f"granular (annotator has an FLP label): n={len(gr)}; exact same class {sum(1 for r in gr if r['gran_exact_same_class']=='True')}; base label any class {sum(1 for r in gr if r['gran_base_any_class']=='True')}; tier match {sum(1 for r in gr if r['gran_tier_match']=='True')}")
        conf = Counter((r["label_granular_flp"], r["pipeline_most_severe"] or "Cited by") for r in gr)
        print("annotator label -> pipeline most severe (top 14):")
        for (a, p), n in conf.most_common(14):
            print(f"   {a:38} -> {p:38} {n}")
        if src == "hellyer":
            acan = [r for r in jd if r["label_granular_raw"] == "ACAN"]
            print(f"ACAN (no FLP label) n={len(acan)}: pipeline negative {sum(1 for r in acan if r['pipeline_negative']=='True')}")
        # citator reference
        if src == "hellyer":
            for cit in ("shepards", "keycite", "bcite"):
                cn = sum(1 for r in jd if r[f"{cit}_code"])
                agree = sum(1 for r in jd if bool(r[f"{cit}_code"]) == (r["label_binary"] == "negative"))
                print(f"  reference {cit}: codes on {cn} joined pairs; binary agreement with annotator {agree}/{len(jd)}")
    uj = [r for r in out if r["join"] == "unjoined"]
    print("\nunjoined sample:", [(r["source"], r["citing_cluster_id"], r["cited_cl_case_name"][:25], r["cited_cl_citation"]) for r in uj[:8]])


if __name__ == "__main__":
    main()
