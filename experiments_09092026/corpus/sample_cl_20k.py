"""Sample ~20,000 published opinions with eyecite-seeded `html_with_citations`
from CLReplica for the citation extraction + coreference encoder (silver labels).

Runs inside the courtlistener container (Django shell):

    docker cp corpus/exclude_ids/exclude_cluster_ids.csv cl-django:/opt/courtlistener/
    docker cp corpus/sample_cl_20k.py cl-django:/opt/courtlistener/
    docker exec cl-django python manage.py shell -c "exec(open('sample_cl_20k.py').read())"
    docker cp cl-django:/opt/courtlistener/cl20k/ ./data/

Stratified by court group (scotus / federal appellate / federal district /
state supreme / state appellate) x decade (pre-1950 … 2020s) x length bin
(short / medium / long). Every cell gets TARGET_PER_CELL clusters; clusters
beyond a full cell go to an overflow pool that tops the total up to
TARGET_TOTAL at the end, so thin cells (old district courts…) don't leave the
run short.

Requirements per cluster: precedential status Published, `html_with_citations`
on every sub-opinion, >= MIN_CITE_SPANS eyecite citation spans, MIN..MAX text
chars, not in the exclude list (benchmark + every cluster the triage
experiment sampled — those are or will be human-verified and must stay out of
silver training).

Outputs (/opt/courtlistener/cl20k/):
    blocks/{group}_{decade}.jsonl.gz   one line per selected cluster:
                                       {cluster_id, court, court_group, year,
                                        date_filed, case_name, length_bin,
                                        text_length, n_cite_spans, pool,
                                        opinions:[{id, type, html}]}
    sample_metadata.csv                one row per selected cluster
    cell_fill.csv                      per cell: target, selected, overflow
    summary.json                       parameters + totals + timing
Resumable: a block whose .jsonl.gz exists is skipped (its rows are reloaded
into the totals); delete cl20k/ to start over.
"""

import csv
import gzip
import json
import random
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from cl.search.models import PRECEDENTIAL_STATUS, Court, Opinion, OpinionCluster

# ── Tunables ─────────────────────────────────────────────────────────────
SEED = 20260909
TARGET_TOTAL = 20_000
TARGET_PER_CELL = 150          # 5 groups x 9 decades x 3 bins = 135 cells -> 20,250 if all fill
OVERFLOW_PER_CELL = 80         # extra eligible clusters kept per cell to top up thin cells
CANDIDATE_CAP = 1_400          # random cluster ids scanned per (group, decade)
FETCH_BATCH = 50
MIN_TEXT_CHARS = 3_000
MAX_TEXT_CHARS = 150_000
MIN_CITE_SPANS = 3
OUT = Path("/opt/courtlistener/cl20k")
EXCLUDE_CSV = Path("/opt/courtlistener/exclude_cluster_ids.csv")

DECADES = [("pre1950", 1900, 1949), ("1950s", 1950, 1959), ("1960s", 1960, 1969),
           ("1970s", 1970, 1979), ("1980s", 1980, 1989), ("1990s", 1990, 1999),
           ("2000s", 2000, 2009), ("2010s", 2010, 2019), ("2020s", 2020, 2030)]
LENGTH_BINS = [("short", 0, 15_000), ("medium", 15_000, 50_000), ("long", 50_000, 10**9)]

CITE_SPAN_RE = re.compile(r'<span\s+class="citation[^"]*"[^>]*>(.*?)</span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def court_groups():
    """{group: [court ids]} from Court.jurisdiction."""
    by = defaultdict(list)
    for c in Court.objects.all().only("id", "jurisdiction"):
        if c.id == "scotus":
            by["scotus"].append(c.id)
        elif c.jurisdiction == Court.FEDERAL_APPELLATE:
            by["fed_appellate"].append(c.id)
        elif c.jurisdiction == Court.FEDERAL_DISTRICT:
            by["fed_district"].append(c.id)
        elif c.jurisdiction == Court.STATE_SUPREME:
            by["state_supreme"].append(c.id)
        elif c.jurisdiction == Court.STATE_APPELLATE:
            by["state_appellate"].append(c.id)
    return {g: by[g] for g in ("scotus", "fed_appellate", "fed_district", "state_supreme", "state_appellate")}


def text_stats(ops):
    """(plain-text length, number of eyecite citation spans) over the writings
    the dataset will use (separate sub-opinions, else the combined copy)."""
    seps = [o for o in ops if not str(o.type).startswith("010")]
    use = seps or ops
    n_spans = sum(len(CITE_SPAN_RE.findall(o.html_with_citations)) for o in use)
    n_chars = sum(len(TAG_RE.sub("", o.html_with_citations)) for o in use)
    return n_chars, n_spans


def length_bin(n):
    for name, lo, hi in LENGTH_BINS:
        if lo <= n < hi:
            return name
    return LENGTH_BINS[-1][0]


def main():
    random.seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "blocks").mkdir(exist_ok=True)
    exclude = set()
    if EXCLUDE_CSV.exists():
        with open(EXCLUDE_CSV, encoding="utf-8") as fh:
            exclude = {int(r["cluster_id"]) for r in csv.DictReader(fh)}
    print(f"exclude list: {len(exclude)} clusters")
    groups = court_groups()
    for g, ids in groups.items():
        print(f"  {g}: {len(ids)} courts")

    meta_rows = []          # every selected + overflow row (pool column says which)
    t0 = time.time()
    scanned = Counter()

    for group, court_ids in groups.items():
        for dec_name, lo, hi in DECADES:
            block_path = OUT / "blocks" / f"{group}_{dec_name}.jsonl.gz"
            if block_path.exists():
                with gzip.open(block_path, "rt", encoding="utf-8") as f:
                    for ln in f:
                        r = json.loads(ln)
                        meta_rows.append({k: r[k] for k in META_FIELDS if k in r})
                print(f"[{group}/{dec_name}] exists — reloaded")
                continue
            per_bin = {lb: {"selected": [], "overflow": []} for lb, _, _ in LENGTH_BINS}

            def open_bins():
                return [lb for lb, d in per_bin.items()
                        if len(d["selected"]) < TARGET_PER_CELL or len(d["overflow"]) < OVERFLOW_PER_CELL]

            qs = (OpinionCluster.objects.filter(
                    docket__court_id__in=court_ids,
                    date_filed__gte=date(lo, 1, 1), date_filed__lte=date(hi, 12, 31),
                    precedential_status=PRECEDENTIAL_STATUS.PUBLISHED)
                  .exclude(pk__in=exclude)
                  .order_by("?").values_list("pk", flat=True))
            cand, seen = [], set()
            for pk in qs[:CANDIDATE_CAP]:
                if pk not in seen:
                    seen.add(pk)
                    cand.append(pk)
            print(f"\n[{group}/{dec_name}] {len(cand)} candidates", flush=True)
            block_rows = []
            for i in range(0, len(cand), FETCH_BATCH):
                if not open_bins():
                    break
                batch = cand[i:i + FETCH_BATCH]
                clusters = {c.pk: c for c in OpinionCluster.objects.filter(pk__in=batch)
                            .select_related("docket__court").only("case_name", "date_filed", "docket__court__id")}
                ops_by = defaultdict(list)
                for op in Opinion.objects.filter(cluster_id__in=batch).only("id", "type", "cluster_id", "html_with_citations"):
                    ops_by[op.cluster_id].append(op)
                for cid in batch:
                    scanned[(group, dec_name)] += 1
                    ops = ops_by.get(cid, [])
                    if not ops or any(not (o.html_with_citations or "").strip() for o in ops):
                        continue
                    ops.sort(key=lambda o: str(o.type))
                    n_chars, n_spans = text_stats(ops)
                    if n_chars < MIN_TEXT_CHARS or n_chars > MAX_TEXT_CHARS or n_spans < MIN_CITE_SPANS:
                        continue
                    lb = length_bin(n_chars)
                    d = per_bin[lb]
                    if len(d["selected"]) < TARGET_PER_CELL:
                        pool = "selected"
                    elif len(d["overflow"]) < OVERFLOW_PER_CELL:
                        pool = "overflow"
                    else:
                        continue
                    d[pool].append(cid)
                    c = clusters[cid]
                    row = {"cluster_id": cid, "court": c.docket.court_id, "court_group": group,
                           "year": c.date_filed.year, "date_filed": c.date_filed.isoformat(),
                           "case_name": c.case_name, "decade": dec_name, "length_bin": lb,
                           "text_length": n_chars, "n_cite_spans": n_spans, "n_opinions": len(ops),
                           "opinion_types": "|".join(str(o.type) for o in ops), "pool": pool}
                    block_rows.append({**row, "opinions": [{"id": o.id, "type": o.type, "html": o.html_with_citations} for o in ops],
                                       "fetched": datetime.now(timezone.utc).isoformat()})
                    meta_rows.append(row)
            with gzip.open(block_path, "wt", encoding="utf-8") as f:
                for r in block_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            fill = {lb: (len(d["selected"]), len(d["overflow"])) for lb, d in per_bin.items()}
            n_sel = sum(len(d["selected"]) for d in per_bin.values())
            print(f"[{group}/{dec_name}] wrote {len(block_rows)} (selected {n_sel}) fill {fill} "
                  f"scanned {scanned[(group, dec_name)]} elapsed {time.time() - t0:.0f}s", flush=True)
            write_reports(meta_rows, scanned, t0, final=False)

    # ── top up from overflow to TARGET_TOTAL ──
    selected = [r for r in meta_rows if r["pool"] == "selected"]
    overflow = [r for r in meta_rows if r["pool"] == "overflow"]
    short = TARGET_TOTAL - len(selected)
    if short > 0 and overflow:
        random.shuffle(overflow)
        for r in overflow[:short]:
            r["pool"] = "topup"
    write_reports(meta_rows, scanned, t0, final=True)


META_FIELDS = ["cluster_id", "court", "court_group", "year", "date_filed", "case_name", "decade",
               "length_bin", "text_length", "n_cite_spans", "n_opinions", "opinion_types", "pool"]


def write_reports(meta_rows, scanned, t0, final):
    with open(OUT / "sample_metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=META_FIELDS)
        w.writeheader()
        w.writerows(meta_rows)
    cells = defaultdict(Counter)
    for r in meta_rows:
        cells[(r["court_group"], r["decade"], r["length_bin"])][r["pool"]] += 1
    with open(OUT / "cell_fill.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["court_group", "decade", "length_bin", "target", "selected", "topup", "overflow"])
        for (g, d, lb), c in sorted(cells.items()):
            w.writerow([g, d, lb, TARGET_PER_CELL, c["selected"], c["topup"], c["overflow"]])
    pools = Counter(r["pool"] for r in meta_rows)
    summary = {"seed": SEED, "target_total": TARGET_TOTAL, "target_per_cell": TARGET_PER_CELL,
               "overflow_per_cell": OVERFLOW_PER_CELL, "candidate_cap": CANDIDATE_CAP,
               "min_text_chars": MIN_TEXT_CHARS, "max_text_chars": MAX_TEXT_CHARS, "min_cite_spans": MIN_CITE_SPANS,
               "pools": dict(pools), "in_training_set": pools["selected"] + pools["topup"],
               "scanned": sum(scanned.values()), "elapsed_s": round(time.time() - t0), "final": final,
               "written": datetime.now(timezone.utc).isoformat()}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    if final:
        print(json.dumps(summary, indent=1))
        print(f"\nDONE. Copy back with:\n  docker cp cl-django:{OUT}/ ./data/")


main()
