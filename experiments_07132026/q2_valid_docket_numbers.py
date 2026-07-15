"""Q2 — Valid docket numbers, using CL's OWN merged cleaning logic.

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q2_valid_docket_numbers.py').read())"

Instead of an ad-hoc regex, this reuses the production cleaner
`cl.search.docket_number_cleaner.clean_docket_number_raw`, which returns:
  (cleaned, None)     -> handled by regex (clean, usable)
  (None, docket_id)   -> needs LLM cleaning (regex couldn't standardize it)
  None                -> empty raw OR court not supported by the cleaner
Note: `court_map` supports scotus + cadc + ca1..ca11 (FEDERAL_APPELLATE);
**cafc is NOT in the map** -> reported as unsupported_court.

Counts at the OpinionCluster level (primary unit). Source string = docket's
`docket_number_raw` if populated, else `docket_number` (cleaning flag is OFF,
so `docket_number` still holds the original value). The needs_llm bucket is the
input to the Q2b LLM cost analysis.

Writes q2_valid_docket_numbers.json + samples per bucket.
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
SAMPLE_CAP = 25


def tally(court_ids, label):
    counts = {k: 0 for k in
              ["total", "empty", "unsupported_court", "regex_cleaned",
               "regex_cleaned_but_empty", "needs_llm", "other_none"]}
    unsupported_courts = {}
    samples = {"needs_llm": [], "regex_cleaned": [], "unsupported_court": []}
    qs = (OpinionCluster.objects
          .filter(docket__court_id__in=court_ids)
          .values_list("id", "docket_id", "docket__court_id",
                       "docket__docket_number", "docket__docket_number_raw")
          .iterator(chunk_size=10000))
    for i, (cid, docket_id, court_id, dn, dn_raw) in enumerate(qs):
        counts["total"] += 1
        source = dn_raw if (dn_raw and dn_raw.strip()) else dn

        if court_id not in court_map:
            counts["unsupported_court"] += 1
            unsupported_courts[court_id] = unsupported_courts.get(court_id, 0) + 1
            if len(samples["unsupported_court"]) < SAMPLE_CAP:
                samples["unsupported_court"].append([cid, court_id, source])
            continue
        if not source or not source.strip():
            counts["empty"] += 1
            continue

        res = clean_docket_number_raw(docket_id, source, court_id)
        if res is None:
            counts["other_none"] += 1
        elif res[1] is not None:
            counts["needs_llm"] += 1
            if len(samples["needs_llm"]) < SAMPLE_CAP:
                samples["needs_llm"].append([cid, court_id, source])
        else:
            if res[0] and res[0].strip():
                counts["regex_cleaned"] += 1
                if len(samples["regex_cleaned"]) < SAMPLE_CAP:
                    samples["regex_cleaned"].append([cid, source, res[0]])
            else:
                counts["regex_cleaned_but_empty"] += 1

        if i and i % 200000 == 0:
            print(f"  {label}: {i} processed...")

    print(f"{label}:", json.dumps(counts))
    return {"counts": counts, "unsupported_courts": unsupported_courts,
            "samples": samples}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    result = {
        "scotus": tally(["scotus"], "scotus"),
        "circuits": tally(CIRCUITS, "circuits"),
    }
    out = os.path.join(OUT_DIR, "q2_valid_docket_numbers.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
