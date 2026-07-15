"""Q7 — The intersection: SCOTUS-linkable  x  circuit DB (the actual downward match).

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q7_downward_match.py').read())"

Of the SCOTUS-linkable clusters (valid docket + OCI lower-court docket# +
appeal_from in a circuit), how many have a matching (circuit court, docket
number) pair in the circuit DB?

Both sides are normalized the SAME way: run CL's cleaner (regex output where it
produces one), else light-normalize the raw; explode multi-docket strings into
tokens. Circuit index is keyed (court_id, token). SCOTUS side is keyed
(appeal_from, token-from-OCI-docket-number). NO date filter on the circuit side —
a circuit decision can predate its SCOTUS review by years.

Reports:
  matched_exact           - >=1 circuit cluster with an exact (court,token) hit
  matched_suffix_lenient  - matches after stripping trailing suffixes (-cv/-ag/(L))
  unmatched               - no hit even lenient
  matched_ambiguous       - an exact hit whose (court,token) maps to >1 circuit cluster
And, for the matched set, the ERA distribution (so we can see which eras the
linkable pairs come from):
  era_matched_circuit     - decade of the matched circuit case (date_filed)
  era_scotus_decision     - decade of the SCOTUS decision (date_filed)
  era_lower_court_judgment- decade of OCI.date_judgment (SCOTUS's record of the
                            lower-court decision date)
Writes q7_downward_match.json.
"""

import json
import os
import re

from cl.search.docket_number_cleaner import clean_docket_number_raw
from cl.search.models import OpinionCluster

OUT_DIR = "/tmp/appellate_chain"
CIRCUITS = [
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9", "ca10",
    "ca11", "cadc", "cafc",
]
SAMPLE_CAP = 30
SPLIT = re.compile(r"[;,]| and ", re.IGNORECASE)
PREFIX = re.compile(r"(?i)^\s*(nos?\.?|dockets?|case|civil action)\s*")
SUFFIX = re.compile(r"(-[a-z]{1,4})+$")
PAREN = re.compile(r"\((?:l|con|lead|l\d*)\)$", re.IGNORECASE)


def normalize(part):
    p = PREFIX.sub("", part)
    return re.sub(r"\s+", "", p).lower()


def strip_suffix(tok):
    return SUFFIX.sub("", PAREN.sub("", tok))


def to_tokens(docket_id, raw, court_id):
    if not raw or not raw.strip():
        return set()
    res = clean_docket_number_raw(docket_id, raw, court_id)
    parts = res[0].split("; ") if (res and res[0]) else SPLIT.split(raw)
    return {t for t in (normalize(p) for p in parts) if t}


def valid_scotus_docket(docket_id, dn, dn_raw):
    source = dn_raw if (dn_raw and dn_raw.strip()) else dn
    if not source or not source.strip():
        return False
    return clean_docket_number_raw(docket_id, source, "scotus") is not None


def decade(y):
    return str(y // 10 * 10) if y else "unknown"


def bump(d, k):
    d[k] = d.get(k, 0) + 1


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- Build circuit index: (court, token) -> [count, min_year, max_year] ----
    print("building circuit index...")
    circ_full = {}
    circ_strip = {}
    n_circ = 0
    qs = (OpinionCluster.objects
          .filter(docket__court_id__in=CIRCUITS)
          .values_list("docket_id", "docket__court_id", "date_filed",
                       "docket__docket_number", "docket__docket_number_raw")
          .iterator(chunk_size=10000))
    for i, (docket_id, court, date_filed, dn, dn_raw) in enumerate(qs):
        source = dn_raw if (dn_raw and dn_raw.strip()) else dn
        yr = date_filed.year if date_filed else None
        for t in to_tokens(docket_id, source, court):
            k = (court, t)
            e = circ_full.get(k)
            if e is None:
                circ_full[k] = [1, yr, yr]
            else:
                e[0] += 1
                if yr is not None:
                    e[1] = yr if e[1] is None else min(e[1], yr)
                    e[2] = yr if e[2] is None else max(e[2], yr)
            st = strip_suffix(t)
            if st:
                bump(circ_strip, (court, st))
        n_circ += 1
        if i and i % 200000 == 0:
            print(f"  circuit: {i} processed...")
    print(f"circuit clusters: {n_circ}; full keys: {len(circ_full)}; "
          f"strip keys: {len(circ_strip)}")

    # ---- Walk SCOTUS-linkable clusters, match against the index ----
    print("matching SCOTUS-linkable clusters...")
    counts = {k: 0 for k in
              ["scotus_linkable_total", "matched_exact",
               "matched_suffix_lenient", "unmatched", "matched_ambiguous"]}
    by_court = {}
    era_matched_circuit = {}
    era_scotus_decision = {}
    era_lower_court_judgment = {}
    samples = {"matched": [], "unmatched": []}

    qs = (OpinionCluster.objects
          .filter(docket__court_id="scotus",
                  docket__appeal_from_id__in=CIRCUITS)
          .values_list("id", "docket_id", "date_filed",
                       "docket__docket_number", "docket__docket_number_raw",
                       "docket__appeal_from_id",
                       "docket__originating_court_information__docket_number",
                       "docket__originating_court_information__date_judgment")
          .iterator(chunk_size=10000))

    for i, row in enumerate(qs):
        (cid, docket_id, s_date, dn, dn_raw, court, oci_dn, lc_date) = row
        if not oci_dn or not oci_dn.strip():
            continue
        if not valid_scotus_docket(docket_id, dn, dn_raw):
            continue
        counts["scotus_linkable_total"] += 1
        bc = by_court.setdefault(court, {"linkable": 0, "matched_exact": 0})
        bc["linkable"] += 1

        tokens = to_tokens(docket_id, oci_dn, court)
        exact_hit = ambiguous = False
        matched_years = []
        for t in tokens:
            e = circ_full.get((court, t))
            if e:
                exact_hit = True
                if e[0] > 1:
                    ambiguous = True
                if e[1] is not None:
                    matched_years.append(e[1])
        lenient_hit = exact_hit
        if not exact_hit:
            for t in tokens:
                if circ_strip.get((court, strip_suffix(t)), 0):
                    lenient_hit = True
                    break

        if exact_hit:
            counts["matched_exact"] += 1
            bc["matched_exact"] += 1
            if ambiguous:
                counts["matched_ambiguous"] += 1
            circ_year = min(matched_years) if matched_years else None
            bump(era_matched_circuit, decade(circ_year))
            bump(era_scotus_decision, decade(s_date.year if s_date else None))
            bump(era_lower_court_judgment,
                 decade(lc_date.year if lc_date else None))
        if lenient_hit:
            counts["matched_suffix_lenient"] += 1
        else:
            counts["unmatched"] += 1

        if exact_hit and len(samples["matched"]) < SAMPLE_CAP:
            samples["matched"].append([cid, court, oci_dn, sorted(tokens)])
        elif not lenient_hit and len(samples["unmatched"]) < SAMPLE_CAP:
            samples["unmatched"].append([cid, court, oci_dn, sorted(tokens)])

        if i and i % 20000 == 0:
            print(f"  scotus: {i} scanned...")

    result = {
        "counts": counts,
        "by_court": dict(sorted(by_court.items(),
                                key=lambda x: -x[1]["linkable"])),
        "era_matched_circuit": dict(sorted(era_matched_circuit.items())),
        "era_scotus_decision": dict(sorted(era_scotus_decision.items())),
        "era_lower_court_judgment": dict(sorted(era_lower_court_judgment.items())),
        "samples": samples,
    }
    print(json.dumps({"counts": counts,
                      "era_matched_circuit": result["era_matched_circuit"]},
                     indent=2))
    out = os.path.join(OUT_DIR, "q7_downward_match.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
