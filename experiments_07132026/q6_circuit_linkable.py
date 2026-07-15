"""Q6 — Circuit-side linkable pool: valid docket number AND post-2000.

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q6_circuit_linkable.py').read())"

For a circuit cluster to be a link target (lower court of a SCOTUS case, or
later linked down to its district), it needs a docket number to match on and to
sit in the era the linking data covers (~post-2000). This counts, over all 13
federal circuit courts:

  valid docket    = CL cleaner returns regex_cleaned or needs_llm (same as Q2)
  post-2000       = date_filed year >= 2000
  valid_post2000  = the "possibly linkable" pool (the answer)

cafc is not in the cleaner's court_map, so it's reported separately (its dockets
exist but aren't cleaned — a known gap). Also emits a per-decade table and a
per-court valid_post2000 breakdown. Writes q6_circuit_linkable.json.
"""

import json
import os

from cl.search.docket_number_cleaner import clean_docket_number_raw, court_map
from cl.search.models import OpinionCluster

OUT_DIR = "/tmp/appellate_chain"
CIRCUITS = [
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9", "ca10",
    "ca11", "cadc", "cafc",
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    counts = {k: 0 for k in
              ["total", "empty", "other_none", "valid",
               "valid_post2000", "valid_pre2000", "valid_unknown_date",
               "cafc_total", "cafc_post2000", "cafc_with_docket"]}
    by_decade = {}          # decade -> {"total": n, "valid": n}
    valid_post2000_by_court = {}

    qs = (OpinionCluster.objects
          .filter(docket__court_id__in=CIRCUITS)
          .values_list("id", "docket_id", "docket__court_id", "date_filed",
                       "docket__docket_number", "docket__docket_number_raw")
          .iterator(chunk_size=10000))

    for i, (cid, docket_id, court_id, date_filed, dn, dn_raw) in enumerate(qs):
        counts["total"] += 1
        year = date_filed.year if date_filed else None
        decade = str(year // 10 * 10) if year else "unknown"
        d = by_decade.setdefault(decade, {"total": 0, "valid": 0})
        d["total"] += 1
        source = dn_raw if (dn_raw and dn_raw.strip()) else dn

        # cafc: not supported by the cleaner -> report separately.
        if court_id not in court_map:
            counts["cafc_total"] += 1
            if source and source.strip():
                counts["cafc_with_docket"] += 1
            if year and year >= 2000:
                counts["cafc_post2000"] += 1
            continue

        if not source or not source.strip():
            counts["empty"] += 1
            continue

        res = clean_docket_number_raw(docket_id, source, court_id)
        if res is None:
            counts["other_none"] += 1
            continue
        # regex_cleaned or needs_llm -> valid (has a usable docket number)
        counts["valid"] += 1
        d["valid"] += 1
        if year is None:
            counts["valid_unknown_date"] += 1
        elif year >= 2000:
            counts["valid_post2000"] += 1
            valid_post2000_by_court[court_id] = (
                valid_post2000_by_court.get(court_id, 0) + 1
            )
        else:
            counts["valid_pre2000"] += 1

        if i and i % 200000 == 0:
            print(f"  {i} processed...")

    result = {
        "counts": counts,
        "by_decade": dict(sorted(by_decade.items())),
        "valid_post2000_by_court": dict(
            sorted(valid_post2000_by_court.items(), key=lambda x: -x[1])
        ),
    }
    print(json.dumps({"counts": counts}, indent=2))
    out = os.path.join(OUT_DIR, "q6_circuit_linkable.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
