"""Q4 — Duplicate SCOTUS / circuit cases (same court, date, docket number).

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q4_duplicates.py').read())"

Groups OpinionClusters by (court_id, date_filed, normalized docket_number).
Groups of size > 1 are cases where a 1:1 SCOTUS<->circuit match cannot be made
confidently on identity alone. Only clusters with BOTH a date_filed and a
non-empty docket number are groupable (others are reported as ungroupable).

Writes:
  q4_duplicates_summary.json  — counts (SCOTUS vs circuit)
  q4_duplicate_groups.json    — the colliding groups (feeds Q5)
"""

import json
import os
import re

from cl.search.models import OpinionCluster

OUT_DIR = "/tmp/appellate_chain"
CIRCUITS = [
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9", "ca10",
    "ca11", "cadc", "cafc",
]
POPULATIONS = ["scotus"] + CIRCUITS


def norm(dn):
    if not dn or not dn.strip():
        return None
    return re.sub(r"\s+", "", dn.strip()).lower()


def process(court_ids, label):
    groups = {}          # (court, date, docket) -> [cluster_ids]
    ungroupable = 0
    total = 0
    qs = (OpinionCluster.objects
          .filter(docket__court_id__in=court_ids)
          .values_list("id", "docket__court_id", "date_filed",
                       "docket__docket_number", "case_name")
          .iterator(chunk_size=10000))
    names = {}
    for i, (cid, court, date_filed, dn, case_name) in enumerate(qs):
        total += 1
        nd = norm(dn)
        if date_filed is None or nd is None:
            ungroupable += 1
            continue
        key = (court, date_filed.isoformat(), nd)
        groups.setdefault(key, []).append(cid)
        names[cid] = case_name
        if i and i % 200000 == 0:
            print(f"  {label}: {i} processed...")

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    dup_clusters = sum(len(v) for v in dup_groups.values())
    summary = {
        "total_clusters": total,
        "ungroupable_missing_date_or_docket": ungroupable,
        "groupable_clusters": total - ungroupable,
        "unique_group_keys": len(groups),
        "duplicate_groups": len(dup_groups),
        "clusters_in_duplicate_groups": dup_clusters,
        "max_group_size": max((len(v) for v in dup_groups.values()),
                              default=0),
    }
    print(f"{label}:", json.dumps(summary, indent=2))
    detail = [
        {"court": k[0], "date_filed": k[1], "docket_number": k[2],
         "cluster_ids": v,
         "case_names": [names.get(c) for c in v]}
        for k, v in dup_groups.items()
    ]
    return summary, detail


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    scotus_summary, scotus_detail = process(["scotus"], "scotus")
    circuit_summary, circuit_detail = process(CIRCUITS, "circuits")

    with open(os.path.join(OUT_DIR, "q4_duplicates_summary.json"), "w") as f:
        json.dump({"scotus": scotus_summary, "circuits": circuit_summary},
                  f, indent=2)
    with open(os.path.join(OUT_DIR, "q4_duplicate_groups.json"), "w") as f:
        json.dump({"scotus": scotus_detail, "circuits": circuit_detail},
                  f, indent=2)
    print("wrote q4_duplicates_summary.json + q4_duplicate_groups.json")


main()
