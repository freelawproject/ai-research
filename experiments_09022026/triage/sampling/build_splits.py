"""Dev/test split of the citator-benchmark citing clusters for the triage
experiment. Cluster-level split (no pair of a cluster crosses the line),
stratified by court group x has-any-positive, fixed seed. Positives exclude
pairs whose only non-neutral label is an "as recognized by" form.

Downsized 2026-09-03 (Rachel): --dev/--test cap the sets; within each
stratum, clusters Rachel has already VERIFIED (double_reviewed) are taken
first, then resolved-only (cluster_done), then the rest at random. The full 150/233 split is
kept as inputs/splits_full.csv. Clusters not selected are 'unused'.

    python build_splits.py --dev 50 --test 100   # writes inputs/splits.csv + prints table

Reads the benchmark exports directly; nothing is modified there.
"""
import argparse
import collections
import csv
import json
import os
import random
from pathlib import Path

BENCH = Path("/Users/rachel/Desktop/flp/citator-benchmark/data")
OUT = Path(__file__).parent.parent / "inputs" / "splits.csv"
SEED = 20260902
DEV_SHARE = 150 / 384


def is_positive(t: str) -> bool:
    return bool(t) and t != "Cited by" and not t.endswith(" as recognized by")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", type=int, default=150)
    ap.add_argument("--test", type=int, default=233)
    a = ap.parse_args()
    gold, verified = set(), set()
    for f in os.listdir(BENCH / "grouping_overrides"):
        if f.endswith(".json") and not f.startswith("."):
            ov = json.load(open(BENCH / "grouping_overrides" / f))
            if ov.get("cluster_done"):
                gold.add(f[:-5])
            if ov.get("double_reviewed"):
                verified.add(f[:-5])
    group = {r["citing_cluster_id"]: r["group"]
             for r in csv.DictReader(open(BENCH / "citing_cases.csv"))}
    pairs = collections.Counter()
    positives = collections.Counter()
    for r in csv.DictReader(open(BENCH / "treatments_most_negative.csv")):
        cid = r["citing_case_cluster_id"]
        if not r["final_treatment"]:
            continue
        pairs[cid] += 1
        if is_positive(r["final_treatment"]):
            positives[cid] += 1
    clusters = sorted(group)
    strata = collections.defaultdict(list)
    for cid in clusters:
        strata[(group[cid], positives[cid] > 0)].append(cid)

    rng = random.Random(SEED)
    rows = []
    total = len(clusters)
    for key in sorted(strata):
        ids = strata[key]
        rng.shuffle(ids)
        # verified first, then resolved-only, then the rest (random within each tier)
        ids.sort(key=lambda c: 0 if c in verified else 1 if c in gold else 2)
        share = len(ids) / total
        n_dev = round(a.dev * share)
        n_test = round(a.test * share)
        for i, cid in enumerate(ids):
            split = "dev" if i < n_dev else "test" if i < n_dev + n_test else "unused"
            rows.append({"cluster_id": cid, "split": split,
                         "court_group": key[0], "has_positive": int(key[1]),
                         "gold_grouping": int(cid in gold), "verified": int(cid in verified),
                         "n_final_pairs": pairs[cid], "n_positive_pairs": positives[cid]})
    rows.sort(key=lambda r: (r["split"], r["court_group"], int(r["cluster_id"])))
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {OUT} ({len(rows)} clusters)\n")
    print(f"{'split':6s} {'group':7s} {'clusters':>8s} {'w/ positive':>11s} {'verif':>5s} {'gold':>5s} {'pairs':>6s} {'positives':>9s}")
    for split in ("dev", "test", "unused"):
        for g in ("SCOTUS", "FED", "STATE"):
            sub = [r for r in rows if r["split"] == split and r["court_group"] == g]
            print(f"{split:6s} {g:7s} {len(sub):8d} {sum(r['has_positive'] for r in sub):11d} {sum(r['verified'] for r in sub):5d} {sum(r['gold_grouping'] for r in sub):5d} "
                  f"{sum(r['n_final_pairs'] for r in sub):6d} {sum(r['n_positive_pairs'] for r in sub):9d}")
        sub = [r for r in rows if r["split"] == split]
        print(f"{split:6s} {'ALL':7s} {len(sub):8d} {sum(r['has_positive'] for r in sub):11d} {sum(r['verified'] for r in sub):5d} {sum(r['gold_grouping'] for r in sub):5d} "
              f"{sum(r['n_final_pairs'] for r in sub):6d} {sum(r['n_positive_pairs'] for r in sub):9d}\n")


if __name__ == "__main__":
    main()
