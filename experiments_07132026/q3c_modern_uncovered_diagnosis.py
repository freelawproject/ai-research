"""Q3c — Why are ~81k MODERN (2000+) SCOTUS clusters not in the scrape universe?

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q3c_modern_uncovered_diagnosis.py').read())"

The SCOTUS importer merges on `Docket.docket_number_core`. So for each valid
2000+ SCOTUS cluster whose docket has NO ScotusDocketMetadata (not in scrape),
we ask: does its `docket_number_core` match a docket that IS in the scrape?

  core_dup_of_covered : core matches an in-scrape SCOTUS docket
                        -> the case IS covered; this is a DUPLICATE CL docket
                        that never got merged (reason A).
  core_not_covered    : non-empty core, no in-scrape match
                        -> not in the scrape inventory OR core/format differs
                        (reason B/C — potentially real missing coverage).
  no_core             : docket_number_core empty (can't test).

Also breaks down by docket-number format (NN-NNNN / NNANNN / Orig|Misc / other).
Writes q3c_modern_uncovered_diagnosis.json.
"""

import json
import os
import re

from cl.search.docket_number_cleaner import clean_docket_number_raw
from cl.search.models import Docket, OpinionCluster

OUT_DIR = "/tmp/appellate_chain"
SAMPLE_CAP = 25
SCOTUS_N = re.compile(r"^\d{1,2}-\d{1,6}$")
SCOTUS_A = re.compile(r"^\d{2}[aA]\d{1,4}$")


def valid(docket_id, dn, dn_raw):
    source = dn_raw if (dn_raw and dn_raw.strip()) else dn
    if not source or not source.strip():
        return False
    return clean_docket_number_raw(docket_id, source, "scotus") is not None


def fmt(dn):
    if not dn:
        return "other"
    c = re.sub(r"\s+", "", dn.strip())
    if SCOTUS_N.match(c):
        return "NN-NNNN"
    if SCOTUS_A.match(c):
        return "NNANNN"
    low = dn.lower()
    if "orig" in low or "misc" in low:
        return "orig_or_misc"
    return "other"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("building in-scrape core set...")
    in_scrape_cores = set(
        Docket.objects
        .filter(court_id="scotus", scotus_metadata__isnull=False)
        .exclude(docket_number_core="")
        .values_list("docket_number_core", flat=True)
    )
    print(f"in-scrape SCOTUS dockets with non-empty core: {len(in_scrape_cores)}")

    counts = {"total_modern_not_in_scrape_valid": 0,
              "core_dup_of_covered": 0, "core_not_covered": 0, "no_core": 0}
    by_format = {}
    crosstab = {}  # "format|core_status" -> n
    samples = {"core_dup_of_covered": [], "core_not_covered": []}

    qs = (OpinionCluster.objects
          .filter(docket__court_id="scotus",
                  docket__scotus_metadata__isnull=True,
                  date_filed__year__gte=2000)
          .values_list("id", "docket_id", "docket__docket_number",
                       "docket__docket_number_raw",
                       "docket__docket_number_core")
          .iterator(chunk_size=10000))

    for i, (cid, docket_id, dn, dn_raw, core) in enumerate(qs):
        if not valid(docket_id, dn, dn_raw):
            continue
        counts["total_modern_not_in_scrape_valid"] += 1
        f = fmt(dn)
        by_format[f] = by_format.get(f, 0) + 1

        if not core:
            status = "no_core"
        elif core in in_scrape_cores:
            status = "core_dup_of_covered"
        else:
            status = "core_not_covered"
        counts[status] += 1
        ck = f"{f}|{status}"
        crosstab[ck] = crosstab.get(ck, 0) + 1
        if status in samples and len(samples[status]) < SAMPLE_CAP:
            samples[status].append([cid, dn, core])

        if i and i % 100000 == 0:
            print(f"  {i} scanned...")

    result = {"counts": counts, "by_format": by_format,
              "crosstab_format_x_corestatus": crosstab, "samples": samples}
    print(json.dumps({"counts": counts, "by_format": by_format,
                      "crosstab": crosstab}, indent=2))
    out = os.path.join(OUT_DIR, "q3c_modern_uncovered_diagnosis.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
