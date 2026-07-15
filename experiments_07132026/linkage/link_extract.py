"""Stage 1 — Extract SCOTUS-linkable + circuit records from CLReplica (re-runnable).

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/linkage/link_extract.py').read())"

Writes two snapshots the standalone linker (link_build.py) consumes. Re-run this
whenever the data is re-queried; link_build.py then produces fresh links with no
DB access.

  scotus_linkable.json  - list of SCOTUS clusters that have a circuit lower court
                          + OCI docket number (the left side of the link)
  circuit_records.jsonl - one line per circuit cluster (the match target pool)

Both carry precomputed match tokens (cleaned via CL's docket cleaner) and digit
cores, so link_build.py needs no CL dependency.
"""

import json
import os
import re

from cl.search.docket_number_cleaner import clean_docket_number_raw
from cl.search.models import OpinionCluster

OUT_DIR = "/tmp/appellate_chain/linkage"
CIRCUITS = [
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9", "ca10",
    "ca11", "cadc", "cafc",
]
SPLIT = re.compile(r"[;,]| and ", re.IGNORECASE)
PREFIX = re.compile(r"(?i)^\s*(nos?\.?|dockets?|case|civil action)\s*")
DIGITS = re.compile(r"\D")
MIN_CORE = 5


def normalize(part):
    return re.sub(r"\s+", "", PREFIX.sub("", part)).lower()


def core(tok):
    d = DIGITS.sub("", tok)
    return d if len(d) >= MIN_CORE else None


def tokenize(docket_id, raw, court_id):
    """(tokens, cores) for a docket string, cleaned like production."""
    if not raw or not raw.strip():
        return [], [], False
    res = clean_docket_number_raw(docket_id, raw, court_id)
    parts = res[0].split("; ") if (res and res[0]) else SPLIT.split(raw)
    toks = sorted({t for t in (normalize(p) for p in parts) if t})
    cores = sorted({c for c in (core(t) for t in toks) if c})
    needs_llm = bool(res is not None and res[1] is not None)
    return toks, cores, needs_llm


def iso(d):
    return d.isoformat() if d else None


def extract_scotus():
    rows = []
    qs = (OpinionCluster.objects
          .filter(docket__court_id="scotus",
                  docket__appeal_from_id__in=CIRCUITS)
          .values_list("id", "docket_id", "case_name", "date_filed",
                       "docket__docket_number", "docket__docket_number_raw",
                       "docket__appeal_from_id",
                       "docket__originating_court_information__docket_number")
          .iterator(chunk_size=10000))
    for i, row in enumerate(qs):
        (cid, docket_id, name, date_filed, dn, dn_raw, court, oci_dn) = row
        if not oci_dn or not oci_dn.strip():
            continue
        # require a valid SCOTUS docket (same rule as the analysis)
        src = dn_raw if (dn_raw and dn_raw.strip()) else dn
        if clean_docket_number_raw(docket_id, src, "scotus") is None:
            continue
        toks, cores, oci_llm = tokenize(docket_id, oci_dn, court)
        rows.append({
            "cluster_id": cid, "case_name": name, "date_filed": iso(date_filed),
            "scotus_docket": dn, "appeal_from": court, "oci_docket": oci_dn,
            "tokens": toks, "cores": cores, "oci_needs_llm": oci_llm,
        })
        if i and i % 20000 == 0:
            print(f"  scotus: {i}...")
    path = os.path.join(OUT_DIR, "scotus_linkable.json")
    with open(path, "w") as f:
        json.dump(rows, f)
    print(f"wrote {path} ({len(rows)} rows)")


def extract_circuit():
    path = os.path.join(OUT_DIR, "circuit_records.jsonl")
    n = 0
    qs = (OpinionCluster.objects
          .filter(docket__court_id__in=CIRCUITS)
          .values_list("id", "docket_id", "docket__court_id", "case_name",
                       "date_filed", "docket__docket_number",
                       "docket__docket_number_raw")
          .iterator(chunk_size=10000))
    with open(path, "w") as f:
        for i, row in enumerate(qs):
            (cid, docket_id, court, name, date_filed, dn, dn_raw) = row
            src = dn_raw if (dn_raw and dn_raw.strip()) else dn
            toks, cores, _ = tokenize(docket_id, src, court)
            f.write(json.dumps({
                "cluster_id": cid, "court": court, "case_name": name,
                "date_filed": iso(date_filed), "docket_number": dn,
                "tokens": toks, "cores": cores,
            }) + "\n")
            n += 1
            if i and i % 200000 == 0:
                print(f"  circuit: {i}...")
    print(f"wrote {path} ({n} rows)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("extracting SCOTUS-linkable...")
    extract_scotus()
    print("extracting circuit records...")
    extract_circuit()
    print("done.")


main()
