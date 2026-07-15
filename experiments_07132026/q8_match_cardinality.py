"""Q8 — Match cardinality + cleaning-recovery for the SCOTUS<->circuit intersection.

Run inside the cl-django container:
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q8_match_cardinality.py').read())"

For each SCOTUS-linkable cluster, match its OCI lower-court docket# against the
circuit index at two tiers, and classify:

Tier 1 — EXACT (current cleaner tokens agree). Split by cardinality, where for
the matched pair C = # circuit clusters and S = # SCOTUS-linkable clusters:
  one_to_one   C==1,S==1   one_to_many  C>1,S==1
  many_to_one  C==1,S>1    many_to_many C>1,S>1

Tier 2 — RECOVERABLE BY CLEANING: no exact hit, but the digit-core of the docket
number matches a circuit docket's digit-core (len>=5). This is what more
aggressive normalization / automated (LLM) docket cleaning would recover —
suffix differences (-cv/-ag), format drift, and the `needs_llm` dockets that the
regex cleaner couldn't standardize. Reported as an upper bound.

Otherwise UNMATCHED (no circuit docket even at digit-core) — a genuine gap
(the circuit opinion likely isn't in CL).

Also tracks whether each cluster's OCI docket is `needs_llm` (regex cleaner
couldn't standardize it) so we can see how much recovery hinges on LLM cleaning.
Writes q8_match_cardinality.json.
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
SAMPLE_CAP = 25
SPLIT = re.compile(r"[;,]| and ", re.IGNORECASE)
PREFIX = re.compile(r"(?i)^\s*(nos?\.?|dockets?|case|civil action)\s*")
DIGITS = re.compile(r"\D")
MIN_CORE = 5   # min digits for a core match (guards against trivial collisions)


def normalize(part):
    return re.sub(r"\s+", "", PREFIX.sub("", part)).lower()


def core(tok):
    d = DIGITS.sub("", tok)
    return d if len(d) >= MIN_CORE else None


def to_tokens(docket_id, raw, court_id):
    if not raw or not raw.strip():
        return set()
    res = clean_docket_number_raw(docket_id, raw, court_id)
    parts = res[0].split("; ") if (res and res[0]) else SPLIT.split(raw)
    return {t for t in (normalize(p) for p in parts) if t}


def scotus_docket_status(docket_id, dn, dn_raw):
    """Returns 'invalid' / 'regex' / 'needs_llm' for the SCOTUS docket number."""
    source = dn_raw if (dn_raw and dn_raw.strip()) else dn
    if not source or not source.strip():
        return "invalid"
    res = clean_docket_number_raw(docket_id, source, "scotus")
    if res is None:
        return "invalid"
    return "needs_llm" if res[1] is not None else "regex"


def bump(d, k):
    d[k] = d.get(k, 0) + 1


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- circuit index: exact (court,token)->C  and core set (court,digits) ----
    print("building circuit index...")
    circ_full, circ_core = {}, set()
    qs = (OpinionCluster.objects
          .filter(docket__court_id__in=CIRCUITS)
          .values_list("docket_id", "docket__court_id",
                       "docket__docket_number", "docket__docket_number_raw")
          .iterator(chunk_size=10000))
    for i, (docket_id, court, dn, dn_raw) in enumerate(qs):
        source = dn_raw if (dn_raw and dn_raw.strip()) else dn
        for t in to_tokens(docket_id, source, court):
            bump(circ_full, (court, t))
            cc = core(t)
            if cc:
                circ_core.add((court, cc))
        if i and i % 200000 == 0:
            print(f"  circuit: {i}...")
    print(f"circuit: exact keys {len(circ_full)}, core keys {len(circ_core)}")

    # ---- SCOTUS-linkable pass 1: scotus_pairs (court,token)->S + collect ----
    print("indexing SCOTUS-linkable...")
    scotus_pairs = {}
    linkable = []   # (cid, court, tokens, oci_status)
    qs = (OpinionCluster.objects
          .filter(docket__court_id="scotus",
                  docket__appeal_from_id__in=CIRCUITS)
          .values_list("id", "docket_id", "docket__docket_number",
                       "docket__docket_number_raw", "docket__appeal_from_id",
                       "docket__originating_court_information__docket_number")
          .iterator(chunk_size=10000))
    for i, (cid, docket_id, dn, dn_raw, court, oci_dn) in enumerate(qs):
        if not oci_dn or not oci_dn.strip():
            continue
        if scotus_docket_status(docket_id, dn, dn_raw) == "invalid":
            continue
        tokens = to_tokens(docket_id, oci_dn, court)
        # is the OCI (lower-court) docket number itself needs_llm?
        oci_res = clean_docket_number_raw(docket_id, oci_dn, court)
        oci_llm = (oci_res is not None and oci_res[1] is not None)
        linkable.append((cid, court, tokens, oci_llm))
        for t in tokens:
            bump(scotus_pairs, (court, t))
    print(f"scotus-linkable: {len(linkable)}")

    # ---- classify ----
    counts = {k: 0 for k in
              ["scotus_linkable_total", "one_to_one", "one_to_many",
               "many_to_one", "many_to_many", "recoverable_by_cleaning",
               "unmatched", "oci_needs_llm_total",
               "recoverable_needs_llm", "unmatched_needs_llm"]}
    samples = {k: [] for k in
               ["one_to_one", "one_to_many", "recoverable_by_cleaning",
                "unmatched"]}

    for cid, court, tokens, oci_llm in linkable:
        counts["scotus_linkable_total"] += 1
        if oci_llm:
            counts["oci_needs_llm_total"] += 1
        exact = [t for t in tokens if (court, t) in circ_full]
        if exact:
            best = min(exact, key=lambda t: (circ_full[(court, t)],
                                             scotus_pairs[(court, t)]))
            C, S = circ_full[(court, best)], scotus_pairs[(court, best)]
            cat = ("one_to_one" if C == 1 and S == 1 else
                   "one_to_many" if C > 1 and S == 1 else
                   "many_to_one" if C == 1 and S > 1 else "many_to_many")
        elif any((court, core(t)) in circ_core for t in tokens if core(t)):
            cat = "recoverable_by_cleaning"
            if oci_llm:
                counts["recoverable_needs_llm"] += 1
        else:
            cat = "unmatched"
            if oci_llm:
                counts["unmatched_needs_llm"] += 1
        counts[cat] += 1
        if cat in samples and len(samples[cat]) < SAMPLE_CAP:
            samples[cat].append([cid, court, sorted(tokens), oci_llm])

    counts["matched_exact_total"] = (
        counts["one_to_one"] + counts["one_to_many"]
        + counts["many_to_one"] + counts["many_to_many"])
    result = {"counts": counts, "min_core_digits": MIN_CORE, "samples": samples}
    print(json.dumps(counts, indent=2))
    out = os.path.join(OUT_DIR, "q8_match_cardinality.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print("wrote", out)


main()
