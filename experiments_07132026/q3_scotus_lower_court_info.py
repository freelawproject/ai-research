"""Q3 — SCOTUS records (with valid docket #) that have valid lower-court info.

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q3_scotus_lower_court_info.py').read())"

Lower-court info now lives in-database (Alberto's SCOTUS ingest):
  Docket.originating_court_information.docket_number  (the join key downward)
  Docket.appeal_from (FK -> Court)  /  Docket.appeal_from_str (string fallback)

This also tells us whether the SCOTUS importer (CL #6954) has actually
populated OriginatingCourtInformation in CLReplica yet.

Buckets among SCOTUS clusters WITH a valid docket number:
  has_oci                : originating_court_information row exists
  oci_docket_number      : that row's docket_number is non-empty
  appeal_from_fk         : appeal_from (resolved Court FK) is set
  appeal_from_is_circuit : appeal_from is one of the 13 circuits
  appeal_from_str_only   : only appeal_from_str is set (no FK)
  linkable               : oci_docket_number AND appeal_from_is_circuit
Writes q3_scotus_lower_court_info.json.
"""

import json
import os

from cl.search.docket_number_cleaner import clean_docket_number_raw
from cl.search.models import OpinionCluster

OUT_DIR = "/tmp/appellate_chain"
CIRCUITS = {
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9", "ca10",
    "ca11", "cadc", "cafc",
}
SAMPLE_CAP = 25


def valid_scotus_docket(docket_id, dn, dn_raw):
    """Same 'valid' definition as Q2: run CL's merged cleaner on the source
    string (raw if populated, else docket_number). scotus is in court_map, so a
    non-None result means non-empty (regex_cleaned or needs_llm)."""
    source = dn_raw if (dn_raw and dn_raw.strip()) else dn
    if not source or not source.strip():
        return False
    return clean_docket_number_raw(docket_id, source, "scotus") is not None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    counts = {k: 0 for k in
              ["scotus_clusters_total", "valid_docket_number", "has_oci",
               "oci_docket_number", "appeal_from_fk", "appeal_from_is_circuit",
               "appeal_from_str_only", "linkable"]}
    appeal_from_dist = {}
    samples = {"linkable": [], "valid_docket_no_lower_info": []}

    qs = (OpinionCluster.objects
          .filter(docket__court_id="scotus")
          .values_list(
              "id",
              "docket_id",
              "docket__docket_number",
              "docket__docket_number_raw",
              "docket__appeal_from_id",
              "docket__appeal_from_str",
              "docket__originating_court_information__id",
              "docket__originating_court_information__docket_number",
          )
          .iterator(chunk_size=10000))

    for i, row in enumerate(qs):
        (cid, docket_id, dn, dn_raw, appeal_from_id, appeal_from_str,
         oci_id, oci_dn) = row
        counts["scotus_clusters_total"] += 1
        if not valid_scotus_docket(docket_id, dn, dn_raw):
            continue
        counts["valid_docket_number"] += 1

        has_oci = oci_id is not None
        oci_dn_ok = bool(oci_dn and oci_dn.strip())
        af_fk = bool(appeal_from_id)
        af_circuit = af_fk and appeal_from_id in CIRCUITS
        af_str_only = (not af_fk) and bool(
            appeal_from_str and appeal_from_str.strip()
        )

        if has_oci:
            counts["has_oci"] += 1
        if oci_dn_ok:
            counts["oci_docket_number"] += 1
        if af_fk:
            counts["appeal_from_fk"] += 1
            appeal_from_dist[appeal_from_id] = (
                appeal_from_dist.get(appeal_from_id, 0) + 1
            )
        if af_circuit:
            counts["appeal_from_is_circuit"] += 1
        if af_str_only:
            counts["appeal_from_str_only"] += 1

        if oci_dn_ok and af_circuit:
            counts["linkable"] += 1
            if len(samples["linkable"]) < SAMPLE_CAP:
                samples["linkable"].append(
                    [cid, dn, appeal_from_id, oci_dn]
                )
        elif not (has_oci or af_fk or af_str_only):
            if len(samples["valid_docket_no_lower_info"]) < SAMPLE_CAP:
                samples["valid_docket_no_lower_info"].append([cid, dn])

        if i and i % 100000 == 0:
            print(f"  {i} processed...")

    result = {
        "counts": counts,
        "appeal_from_distribution": dict(
            sorted(appeal_from_dist.items(), key=lambda x: -x[1])
        ),
        "samples": samples,
    }
    print(json.dumps(counts, indent=2))
    out = os.path.join(OUT_DIR, "q3_scotus_lower_court_info.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
