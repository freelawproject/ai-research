"""Round 2: sample ~10,000 NEW published opinions from CLReplica for GPT-seeded
training, oversampling opinions with negative-treatment language near a
citation (the triage sample's "keyword" rule), excluding every cluster touched
so far (benchmark, triage annotator, round-1 20K, earlier pools).

Runs inside the courtlistener container (Django shell):

    docker cp corpus/exclude_ids/exclude_cluster_ids_round2.csv cl-django:/opt/courtlistener/
    docker cp corpus/sample_cl_20k_round2.py cl-django:/opt/courtlistener/
    docker exec cl-django python manage.py shell -c "exec(open('sample_cl_20k_round2.py').read())"
    docker cp cl-django:/opt/courtlistener/cl20k_r2/ ./data/

Design (same cells as round 1: court group x decade x length bin = 135 cells,
KEYWORD_PER_CELL + CONTROL_PER_CELL clusters each; overflow pool tops the total up to TARGET_TOTAL = 10,000):

  * keyword vs control, as in triage/sampling/sample_clreplica.py: an opinion is
    "keyword" when at least one STRONG citing-reference term (overrule,
    abrogate, distinguish, decline to follow, not persuasive, ...) occurs
    within NEAR_CITATION_CHARS of an eyecite citation span; direct-history
    words (reverse, affirm, remand) do not count — every appellate opinion has
    a disposition. Each cell takes KEYWORD_PER_CELL keyword + CONTROL_PER_CELL
    control clusters (80/20; the triage set was 5:1).
  * eligibility as round 1: Published, `html_with_citations` on every
    sub-opinion, >= MIN_CITE_SPANS spans, MIN..MAX text chars, not excluded.
  * the exclude list (corpus/exclude_ids/exclude_cluster_ids_round2.csv, 20,613 ids) is the
    union of: the 1,353-id round-1 exclusion (benchmark + April pool), the
    19,260 round-1 sampled clusters, the 422 triage annotator clusters and the
    383 benchmark clusters. No overlap with anything trained or evaluated on.

Outputs (/opt/courtlistener/cl20k_r2/), same shapes as round 1 so
build_dataset.py and the seeding runner work unchanged:
    blocks/{group}_{decade}.jsonl.gz   {cluster_id, court, court_group, year, date_filed,
                                        case_name, decade, length_bin, text_length,
                                        n_cite_spans, n_opinions, opinion_types, pool,
                                        kind (keyword|control), n_strong_near, strong_terms,
                                        opinions:[{id, type, html}]}
    sample_metadata.csv, cell_fill.csv, summary.json
Resumable per block (delete cl20k_r2/ to start over). Round 1 scanned 63K
candidates in 78 min; keyword opinions are a minority of eligible ones, so
expect this run to scan 3-5x more — several hours; run it detached.
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
SEED = 20260911
TARGET_TOTAL = 10_000
KEYWORD_PER_CELL = 60          # 135 cells x 75 = 10,125 if every cell fills
CONTROL_PER_CELL = 15
OVERFLOW_PER_CELL = 40         # extra eligible clusters kept per cell (keyword first) to top up thin cells
CANDIDATE_CAP = 3_500          # random cluster ids scanned per (group, decade); raise if keyword cells stay thin
FETCH_BATCH = 50
MIN_TEXT_CHARS = 3_000
MAX_TEXT_CHARS = 150_000
MIN_CITE_SPANS = 3
NEAR_CITATION_CHARS = 300      # a strong term this close to a citation span = negative signal (triage rule)
OUT = Path("/opt/courtlistener/cl20k_r2")
EXCLUDE_CSV = Path("/opt/courtlistener/exclude_cluster_ids_round2.csv")

DECADES = [("pre1950", 1900, 1949), ("1950s", 1950, 1959), ("1960s", 1960, 1969),
           ("1970s", 1970, 1979), ("1980s", 1980, 1989), ("1990s", 1990, 1999),
           ("2000s", 2000, 2009), ("2010s", 2010, 2019), ("2020s", 2020, 2030)]
LENGTH_BINS = [("short", 0, 15_000), ("medium", 15_000, 50_000), ("long", 50_000, 10**9)]

# Citing-reference treatment language (verbatim from triage/sampling/sample_clreplica.py).
STRONG = {
    "overrule": r"\boverrul\w*",
    "abrogate": r"\babrogat\w*",
    "disapprove": r"\bdisapprov\w*",
    "distinguish": r"\bdistinguish\w*",
    "decline_to_follow": r"\bdeclin\w*\s+to\s+(?:follow|extend|apply|adopt)\b",
    "limit": r"\blimit\w*\s+(?:to\s+its\s+facts|the\s+(?:holding|reach|scope)|\w+\s+to\s+(?:its|their)\s+facts)",
    "criticize": r"\bcriticiz\w*",
    "call_into_question": r"\bcall\w*\s+into\s+question\b",
    "cast_doubt": r"\bcast\w*\s+doubt\b",
    "no_longer_good_law": r"\bno\s+longer\s+(?:good\s+law|controlling|viable|valid)\b",
    "not_persuasive": r"\b(?:not|un)persuasive\b",
    "reject_reasoning": r"\breject\w*\s+(?:the|that|this|its)\s+(?:reasoning|approach|holding|analysis|rationale|rule)\b",
    "inapposite": r"\binapposite\b",
    "superseded": r"\bsupersed\w*",
    "repudiate": r"\brepudiat\w*",
    "disavow": r"\bdisavow\w*",
    "undermine": r"\bundermin\w*",
    "do_not_follow": r"\b(?:we\s+)?(?:do|did|will|need)\s+not\s+(?:follow|adopt|extend|apply)\b",
    "questioned": r"\bquestion\w*\s+(?:the\s+)?(?:validity|continued\s+vitality|soundness|correctness)\b",
}
STRONG_RX = {k: re.compile(p, re.I | re.S) for k, p in STRONG.items()}
CITE_SPAN_RE = re.compile(r'<span\s+class="citation[^"]*"[^>]*>(.*?)</span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
CITE_MARK = "⟦CITE⟧"


def court_groups():
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


def to_scan_text(html):
    """Citation spans -> marker + text; other tags stripped."""
    t = CITE_SPAN_RE.sub(lambda m: CITE_MARK + TAG_RE.sub("", m.group(1)), html)
    return TAG_RE.sub("", t)


def strong_near(text):
    """{term: hits within NEAR_CITATION_CHARS of a citation marker} (only terms with >=1 near hit)."""
    marks = [m.start() for m in re.finditer(re.escape(CITE_MARK), text)]
    out = {}
    for name, rx in STRONG_RX.items():
        near, j = 0, 0
        for h in (m.start() for m in rx.finditer(text)):
            while j < len(marks) and marks[j] < h - NEAR_CITATION_CHARS:
                j += 1
            if (j < len(marks) and abs(marks[j] - h) <= NEAR_CITATION_CHARS) or \
               (j > 0 and abs(marks[j - 1] - h) <= NEAR_CITATION_CHARS):
                near += 1
        if near:
            out[name] = near
    return out


def stats_for(ops):
    """(plain chars, n spans, scan text) over the writings the dataset uses (sub-opinions, else combined)."""
    seps = [o for o in ops if not str(o.type).startswith("010")]
    use = seps or ops
    scan = "\n".join(to_scan_text(o.html_with_citations) for o in use)
    n_spans = sum(len(CITE_SPAN_RE.findall(o.html_with_citations)) for o in use)
    return len(scan.replace(CITE_MARK, "")), n_spans, scan


def length_bin(n):
    for name, lo, hi in LENGTH_BINS:
        if lo <= n < hi:
            return name
    return LENGTH_BINS[-1][0]


META_FIELDS = ["cluster_id", "court", "court_group", "year", "date_filed", "case_name", "decade",
               "length_bin", "text_length", "n_cite_spans", "n_opinions", "opinion_types", "pool",
               "kind", "n_strong_near", "strong_terms"]


def write_reports(meta_rows, scanned, t0, final):
    with open(OUT / "sample_metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=META_FIELDS)
        w.writeheader()
        w.writerows(meta_rows)
    cells = defaultdict(Counter)
    for r in meta_rows:
        cells[(r["court_group"], r["decade"], r["length_bin"])][(r["pool"], r["kind"])] += 1
    with open(OUT / "cell_fill.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["court_group", "decade", "length_bin", "keyword_target", "keyword_selected",
                    "control_target", "control_selected", "overflow", "topup", "scanned"])
        for (g, d, lb), c in sorted(cells.items()):
            w.writerow([g, d, lb, KEYWORD_PER_CELL, c[("selected", "keyword")], CONTROL_PER_CELL,
                        c[("selected", "control")], c[("overflow", "keyword")] + c[("overflow", "control")],
                        c[("topup", "keyword")] + c[("topup", "control")], scanned[(g, d)]])
    pools = Counter(r["pool"] for r in meta_rows)
    kinds = Counter(r["kind"] for r in meta_rows if r["pool"] in ("selected", "topup"))
    terms = Counter()
    for r in meta_rows:
        if r["pool"] in ("selected", "topup") and r["strong_terms"]:
            terms.update(r["strong_terms"].split("|"))
    with open(OUT / "summary.json", "w", encoding="utf-8") as fh:
        json.dump({"seed": SEED, "target_total": TARGET_TOTAL, "keyword_per_cell": KEYWORD_PER_CELL,
                   "control_per_cell": CONTROL_PER_CELL, "overflow_per_cell": OVERFLOW_PER_CELL,
                   "candidate_cap": CANDIDATE_CAP, "near_citation_chars": NEAR_CITATION_CHARS,
                   "min_text_chars": MIN_TEXT_CHARS, "max_text_chars": MAX_TEXT_CHARS, "min_cite_spans": MIN_CITE_SPANS,
                   "pools": dict(pools), "kinds_in_training_set": dict(kinds),
                   "in_training_set": pools["selected"] + pools["topup"], "strong_term_coverage": dict(terms.most_common()),
                   "scanned": sum(scanned.values()), "elapsed_s": round(time.time() - t0), "final": final,
                   "written": datetime.now(timezone.utc).isoformat()},
                  fh, indent=1)


def main():
    random.seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "blocks").mkdir(exist_ok=True)
    exclude = set()
    if EXCLUDE_CSV.exists():
        with open(EXCLUDE_CSV, encoding="utf-8") as fh:
            exclude = {int(r["cluster_id"]) for r in csv.DictReader(fh)}
    print(f"exclude list: {len(exclude)} clusters", flush=True)
    groups = court_groups()
    for g, ids in groups.items():
        print(f"  {g}: {len(ids)} courts")

    meta_rows, scanned, t0 = [], Counter(), time.time()
    for group, court_ids in groups.items():
        for dec_name, lo, hi in DECADES:
            block_path = OUT / "blocks" / f"{group}_{dec_name}.jsonl.gz"
            if block_path.exists():
                with gzip.open(block_path, "rt", encoding="utf-8") as f:
                    for ln in f:
                        r = json.loads(ln)
                        meta_rows.append({k: r[k] for k in META_FIELDS if k in r})
                print(f"[{group}/{dec_name}] exists — reloaded", flush=True)
                continue
            cell = {lb: {"keyword": [], "control": [], "overflow": []} for lb, _, _ in LENGTH_BINS}

            def need(lb):
                d = cell[lb]
                return (len(d["keyword"]) < KEYWORD_PER_CELL or len(d["control"]) < CONTROL_PER_CELL
                        or len(d["overflow"]) < OVERFLOW_PER_CELL)

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
                if not any(need(lb) for lb in cell):
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
                    n_chars, n_spans, scan = stats_for(ops)
                    if n_chars < MIN_TEXT_CHARS or n_chars > MAX_TEXT_CHARS or n_spans < MIN_CITE_SPANS:
                        continue
                    lb = length_bin(n_chars)
                    hits = strong_near(scan)
                    kind = "keyword" if hits else "control"
                    d = cell[lb]
                    target = KEYWORD_PER_CELL if kind == "keyword" else CONTROL_PER_CELL
                    if len(d[kind]) < target:
                        pool = "selected"
                        d[kind].append(cid)
                    elif len(d["overflow"]) < OVERFLOW_PER_CELL and (kind == "keyword" or len(d["overflow"]) < OVERFLOW_PER_CELL // 3):
                        pool = "overflow"      # keyword-first overflow: controls take at most a third of it
                        d["overflow"].append(cid)
                    else:
                        continue
                    c = clusters[cid]
                    row = {"cluster_id": cid, "court": c.docket.court_id, "court_group": group,
                           "year": c.date_filed.year, "date_filed": c.date_filed.isoformat(),
                           "case_name": c.case_name, "decade": dec_name, "length_bin": lb,
                           "text_length": n_chars, "n_cite_spans": n_spans, "n_opinions": len(ops),
                           "opinion_types": "|".join(str(o.type) for o in ops), "pool": pool,
                           "kind": kind, "n_strong_near": sum(hits.values()),
                           "strong_terms": "|".join(sorted(hits))}
                    block_rows.append({**row, "opinions": [{"id": o.id, "type": o.type, "html": o.html_with_citations} for o in ops],
                                       "fetched": datetime.now(timezone.utc).isoformat()})
                    meta_rows.append(row)
            with gzip.open(block_path, "wt", encoding="utf-8") as f:
                for r in block_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            fill = {lb: (len(d["keyword"]), len(d["control"]), len(d["overflow"])) for lb, d in cell.items()}
            print(f"[{group}/{dec_name}] wrote {len(block_rows)} fill(kw,ctl,ovf) {fill} scanned {scanned[(group, dec_name)]} "
                  f"elapsed {time.time() - t0:.0f}s", flush=True)
            write_reports(meta_rows, scanned, t0, final=False)

    # ── top up from overflow to TARGET_TOTAL (keyword rows first) ──
    selected = [r for r in meta_rows if r["pool"] == "selected"]
    overflow = [r for r in meta_rows if r["pool"] == "overflow"]
    short = TARGET_TOTAL - len(selected)
    if short > 0 and overflow:
        random.shuffle(overflow)
        overflow.sort(key=lambda r: 0 if r["kind"] == "keyword" else 1)
        for r in overflow[:short]:
            r["pool"] = "topup"
    write_reports(meta_rows, scanned, t0, final=True)
    kinds = Counter(r["kind"] for r in meta_rows if r["pool"] in ("selected", "topup"))
    print(f"\nDONE: {kinds['keyword'] + kinds['control']} in training set ({dict(kinds)}); "
          f"scanned {sum(scanned.values())} in {time.time() - t0:.0f}s -> {OUT}", flush=True)


main()
