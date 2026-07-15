"""Q3b — Decompose why only ~21% of SCOTUS clusters have lower-court info.

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q3b_oci_decomposition.py').read())"

Tests whether the missing OCI is due to (a) cases outside the scrape/importer
universe (era/coverage), (b) merged-but-no-lower-court-info, or (c) other.

For each valid-docket SCOTUS cluster (same 'valid' as Q2/Q3), records:
  in_scrape_universe = docket has ScotusDocketMetadata (was merged by importer)
  has_oci            = docket has OriginatingCourtInformation
  decade of date_filed
Cross-tabs in_scrape_universe x has_oci, and a per-decade has_oci rate.
Writes q3b_oci_decomposition.json.
"""

import json
import os

from cl.search.docket_number_cleaner import clean_docket_number_raw
from cl.search.models import OpinionCluster

OUT_DIR = "/tmp/appellate_chain"


def valid(docket_id, dn, dn_raw):
    source = dn_raw if (dn_raw and dn_raw.strip()) else dn
    if not source or not source.strip():
        return False
    return clean_docket_number_raw(docket_id, source, "scotus") is not None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    crosstab = {"in_scrape": {"has_oci": 0, "no_oci": 0},
                "not_in_scrape": {"has_oci": 0, "no_oci": 0}}
    by_decade = {}  # decade -> {"valid": n, "has_oci": n}
    valid_total = 0

    qs = (OpinionCluster.objects
          .filter(docket__court_id="scotus")
          .values_list("id", "docket_id", "date_filed",
                       "docket__docket_number", "docket__docket_number_raw",
                       "docket__scotus_metadata__id",
                       "docket__originating_court_information__id")
          .iterator(chunk_size=10000))

    for i, row in enumerate(qs):
        (cid, docket_id, date_filed, dn, dn_raw, meta_id, oci_id) = row
        if not valid(docket_id, dn, dn_raw):
            continue
        valid_total += 1
        in_scrape = meta_id is not None
        has_oci = oci_id is not None
        outer = "in_scrape" if in_scrape else "not_in_scrape"
        inner = "has_oci" if has_oci else "no_oci"
        crosstab[outer][inner] += 1

        decade = (date_filed.year // 10 * 10) if date_filed else "unknown"
        d = by_decade.setdefault(str(decade), {"valid": 0, "has_oci": 0})
        d["valid"] += 1
        if has_oci:
            d["has_oci"] += 1

        if i and i % 100000 == 0:
            print(f"  {i} processed...")

    result = {
        "valid_docket_scotus_clusters": valid_total,
        "crosstab_in_scrape_x_has_oci": crosstab,
        "by_decade": dict(sorted(by_decade.items())),
    }
    print(json.dumps(result, indent=2))
    out = os.path.join(OUT_DIR, "q3b_oci_decomposition.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
