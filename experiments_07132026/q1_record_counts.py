"""Q1 — How many SCOTUS and circuit records are in CL.

Run inside the cl-django container against CLReplica:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q1_record_counts.py').read())"

Counts dockets and opinion clusters for SCOTUS and each of the 13 federal
circuit courts. Cluster is the primary linkable unit; dockets reported for
context. Writes /tmp/appellate_chain/q1_record_counts.json.
"""

import json
import os

from cl.search.models import Docket, OpinionCluster

OUT_DIR = "/tmp/appellate_chain"
CIRCUITS = [
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9", "ca10",
    "ca11", "cadc", "cafc",
]


def court_counts(court_id):
    dockets = Docket.objects.filter(court_id=court_id).count()
    clusters = OpinionCluster.objects.filter(
        docket__court_id=court_id
    ).count()
    return {"dockets": dockets, "clusters": clusters}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    result = {"scotus": court_counts("scotus"), "circuits": {}}
    print("scotus:", result["scotus"])

    circuit_totals = {"dockets": 0, "clusters": 0}
    for court in CIRCUITS:
        c = court_counts(court)
        result["circuits"][court] = c
        circuit_totals["dockets"] += c["dockets"]
        circuit_totals["clusters"] += c["clusters"]
        print(f"{court}:", c)

    result["circuit_totals"] = circuit_totals
    print("circuit_totals:", circuit_totals)

    out = os.path.join(OUT_DIR, "q1_record_counts.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
