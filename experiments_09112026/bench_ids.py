"""Cluster-id lists for the citation-fix pass.

  bash run.sh bench_ids.py seed        # the 283 benchmark clusters WITHOUT verified gold grouping
  bash run.sh bench_ids.py gold        # the 99 with verified gold grouping (triage small dev/test)
  bash run.sh bench_ids.py centralia   # subset of `seed` whose centralia reading passed the coverage guard
Prints one id per line; also writes data/ids_<kind>.txt.
"""
import csv
import os
import sys

from common import DATA, TRIAGE, cluster_ids, read_json

SMALL_SPLITS = os.path.join(TRIAGE, "inputs", "splits.csv")


def gold_ids():
    with open(SMALL_SPLITS, encoding="utf-8") as f:
        return {r["cluster_id"] for r in csv.DictReader(f) if r["split"] in ("dev", "test")}


def ids(kind):
    allc = cluster_ids("all")
    gold = gold_ids()
    if kind == "gold":
        out = [c for c in allc if c in gold]
    elif kind == "seed":
        out = [c for c in allc if c not in gold]
    elif kind == "centralia":
        feed = read_json(os.path.join(DATA, "centralia_feed.json"), {})
        out = [c for c in allc if c not in gold and feed.get(c, {}).get("used")]
    else:
        sys.exit(f"unknown kind {kind}")
    with open(os.path.join(DATA, f"ids_{kind}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return out


if __name__ == "__main__":
    print("\n".join(ids(sys.argv[1])))
