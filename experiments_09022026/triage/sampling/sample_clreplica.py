"""Sample citing opinions for the triage experiment from CLReplica.

Targets opinions that contain treatment-indicative language NEAR a citation,
stratified by court group x decade x length, plus a small control quota of
opinions with no such language. Exports each selected cluster's raw
`html_with_citations` (all sub-opinions) in the citator-benchmark
`data/opinion_html/{cluster_id}.json` shape, so the benchmark viewer's
Grouping annotator can be used as-is for the human citation-extraction /
coreference correction pass.

Runs inside the courtlistener container (Django shell). Rachel runs it.

    docker cp inputs/triage_exclude_cluster_ids.csv cl-django:/opt/courtlistener/
    docker cp sample_clreplica.py cl-django:/opt/courtlistener/
    docker exec cl-django python manage.py shell -c "exec(open('sample_clreplica.py').read())"
    docker cp cl-django:/opt/courtlistener/triage_sample/ ./data/

Inputs (in /opt/courtlistener):
    triage_exclude_cluster_ids.csv   `cluster_id` column; benchmark citing
                                     clusters + the 0402 sampled pool. Never
                                     selected.

Outputs (in /opt/courtlistener/triage_sample/):
    opinion_html/{cluster_id}.json   {cluster_id, opinions:[{id, type, source,
                                     html}], origin, fetched} -- verbatim
                                     html_with_citations per sub-opinion
    sample_metadata.csv              one row per selected cluster: court,
                                     court_group, year, decade, length bin,
                                     text_length, n_opinions, opinion_types,
                                     n_cited_clusters, selection (keyword |
                                     control), strong/dh/signal hit counts
    term_hits.csv                    long: cluster_id, term_group, term,
                                     hits, hits_near_citation
    authority_metadata.csv           one row per (citing cluster, cited
                                     cluster) from OpinionsCited, with cited
                                     case name + citations
    cell_fill.csv                    per (court_group, decade, length_bin):
                                     target vs selected, keyword and control
    summary.json                     run parameters + totals

Tunables are the constants directly below. Re-running reloads the previous
run's sample_metadata.csv / term_hits.csv so earlier picks keep their cells and
only the shortfall is filled; delete triage_sample/ to start over.
"""

import csv
import json
import random
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from django.db.models import Q

from cl.search.models import (
    PRECEDENTIAL_STATUS,
    Court,
    Opinion,
    OpinionCluster,
    OpinionsCited,
)

# ── Tunables ─────────────────────────────────────────────────────────────
SEED = 20260902
TARGET_KEYWORD_PER_CELL = 5   # cells = court_group x decade x length_bin (54)
TARGET_CONTROL_PER_CELL = 1   # opinions with NO strong term near a citation
CANDIDATE_CAP_PER_GROUP_DECADE = 800   # max clusters scanned per (group, decade)
FETCH_BATCH = 50
NEAR_CITATION_CHARS = 300     # term must sit within this many chars of a citation
MIN_TEXT_CHARS = 2_000        # skip stubs
START_YEAR = 1950

CIRCUITS = ["ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca8", "ca9",
            "ca10", "ca11", "cadc", "cafc"]
DECADES = [("le1970s", 1950, 1979), ("1980s", 1980, 1989), ("1990s", 1990, 1999),
           ("2000s", 2000, 2009), ("2010s", 2010, 2019), ("2020s", 2020, 2030)]
LENGTH_BINS = [("short", 0, 20_000), ("medium", 20_000, 60_000),
               ("long", 60_000, 10**9)]

# Treatment-indicative language. STRONG = citing-reference treatments (what
# the triage gate must learn); DH = procedural direct-history language
# (present in most appellate opinions, tracked but not required); SIGNAL =
# citation signals that often precede a negative treatment.
TERMS = {
    "strong": {
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
    },
    "dh": {
        "reverse": r"\brevers\w*",
        "vacate": r"\bvacat\w*",
        "affirm": r"\baffirm\w*",
        "remand": r"\bremand\w*",
        "cert": r"\bcert(?:iorari|\.)?\s+(?:denied|granted)\b",
        "modify": r"\bmodif\w*",
        "dismiss": r"\bdismiss\w*",
    },
    "signal": {
        "but_see": r"\bbut\s+see\b",
        "contra": r"\bcontra\b",
        "cf": r"\bcf\.",
        "compare_with": r"\bcompare\b.{0,200}\bwith\b",
    },
}
COMPILED = {g: {k: re.compile(p, re.I | re.S) for k, p in d.items()} for g, d in TERMS.items()}

CITE_SPAN_RE = re.compile(r'<span\s+class="citation[^"]*"[^>]*>(.*?)</span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
CITE_MARK = "⟦CITE⟧"

# ── Helpers ──────────────────────────────────────────────────────────────


def to_scan_text(html: str) -> str:
    """Replace citation spans with a marker + their text, strip other tags."""
    t = CITE_SPAN_RE.sub(lambda m: CITE_MARK + TAG_RE.sub("", m.group(1)), html)
    return TAG_RE.sub("", t)


def scan_terms(text: str) -> dict:
    """Per term: total hits and hits within NEAR_CITATION_CHARS of a citation."""
    marks = [m.start() for m in re.finditer(re.escape(CITE_MARK), text)]
    out = {}
    for group, terms in COMPILED.items():
        for name, rx in terms.items():
            hits = [m.start() for m in rx.finditer(text)]
            if not hits:
                continue
            near = 0
            j = 0
            for h in hits:
                while j < len(marks) and marks[j] < h - NEAR_CITATION_CHARS:
                    j += 1
                if j < len(marks) and abs(marks[j] - h) <= NEAR_CITATION_CHARS:
                    near += 1
                elif j > 0 and abs(marks[j - 1] - h) <= NEAR_CITATION_CHARS:
                    near += 1
            out[(group, name)] = (len(hits), near)
    return out


def length_bin(n: int) -> str:
    for name, lo, hi in LENGTH_BINS:
        if lo <= n < hi:
            return name
    return LENGTH_BINS[-1][0]


def decade_of(year: int) -> str:
    for name, lo, hi in DECADES:
        if lo <= year <= hi:
            return name
    return "other"


def court_groups() -> dict:
    state_ids = list(
        Court.objects.filter(jurisdiction=Court.STATE_SUPREME, in_use=True)
        .values_list("id", flat=True)
    )
    return {"scotus": ["scotus"], "circuit": CIRCUITS, "state_high": state_ids}


# ── Main ─────────────────────────────────────────────────────────────────


def run() -> None:
    random.seed(SEED)
    base = Path("/opt/courtlistener")
    out = base / "triage_sample"
    html_dir = out / "opinion_html"
    html_dir.mkdir(parents=True, exist_ok=True)

    exclude: set[int] = set()
    with open(base / "triage_exclude_cluster_ids.csv", newline="") as f:
        for row in csv.DictReader(f):
            try:
                exclude.add(int(row["cluster_id"]))
            except (TypeError, ValueError, KeyError):
                pass
    already = {int(p.stem) for p in html_dir.glob("*.json")}
    print(f"Excluding {len(exclude)} cluster ids; {len(already)} already exported")

    # cell -> {"keyword": [...ids], "control": [...ids]}
    selected: dict[tuple, dict] = defaultdict(lambda: {"keyword": [], "control": []})
    meta_rows: list[dict] = []
    term_rows: list[dict] = []
    prev_meta = out / "sample_metadata.csv"
    if prev_meta.exists():
        with open(prev_meta, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["cluster_id"] = int(r["cluster_id"])
                meta_rows.append(r)
                selected[(r["court_group"], r["decade"], r["length_bin"])][r["selection"]].append(r["cluster_id"])
        prev_terms = out / "term_hits.csv"
        if prev_terms.exists():
            with open(prev_terms, newline="", encoding="utf-8") as f:
                term_rows.extend(csv.DictReader(f))
        exclude |= {r["cluster_id"] for r in meta_rows}
        print(f"Resuming: {len(meta_rows)} picks loaded from the previous run")

    groups = court_groups()
    print({g: len(v) for g, v in groups.items()})

    scanned = Counter()
    t0 = time.time()

    for group, court_ids in groups.items():
        for dec_name, lo, hi in DECADES:
            cells_open = lambda: any(  # noqa: E731
                len(selected[(group, dec_name, lb)]["keyword"]) < TARGET_KEYWORD_PER_CELL
                or len(selected[(group, dec_name, lb)]["control"]) < TARGET_CONTROL_PER_CELL
                for lb, _, _ in LENGTH_BINS
            )
            qs = (
                OpinionCluster.objects.filter(
                    docket__court_id__in=court_ids,
                    date_filed__gte=date(max(lo, START_YEAR), 1, 1),
                    date_filed__lte=date(hi, 12, 31),
                    precedential_status=PRECEDENTIAL_STATUS.PUBLISHED,
                    sub_opinions__cited_opinions__isnull=False,
                )
                .exclude(pk__in=exclude)
                .distinct()
                .order_by("?")
                .values_list("pk", flat=True)
            )
            cand = list(qs[:CANDIDATE_CAP_PER_GROUP_DECADE])
            print(f"\n[{group} / {dec_name}] {len(cand)} candidates")
            for i in range(0, len(cand), FETCH_BATCH):
                if not cells_open():
                    break
                batch = cand[i:i + FETCH_BATCH]
                clusters = {
                    c.pk: c for c in OpinionCluster.objects.filter(pk__in=batch)
                    .select_related("docket__court")
                    .only("case_name", "date_filed", "docket__court__id")
                }
                ops_by_cluster: dict[int, list] = defaultdict(list)
                for op in Opinion.objects.filter(cluster_id__in=batch).only(
                    "id", "type", "cluster_id", "html_with_citations"
                ):
                    ops_by_cluster[op.cluster_id].append(op)
                for cid in batch:
                    scanned[(group, dec_name)] += 1
                    ops = ops_by_cluster.get(cid, [])
                    if not ops or any(not (o.html_with_citations or "").strip() for o in ops):
                        continue  # require html_with_citations on every sub-opinion
                    ops.sort(key=lambda o: o.type)
                    scan = "\n".join(to_scan_text(o.html_with_citations) for o in ops)
                    n_chars = len(scan.replace(CITE_MARK, ""))
                    if n_chars < MIN_TEXT_CHARS:
                        continue
                    lb = length_bin(n_chars)
                    cell = (group, dec_name, lb)
                    hits = scan_terms(scan)
                    strong_near = sum(v[1] for (g, _), v in hits.items() if g == "strong")
                    dh_near = sum(v[1] for (g, _), v in hits.items() if g == "dh")
                    sig_near = sum(v[1] for (g, _), v in hits.items() if g == "signal")
                    # Control = no citing-reference language near a citation.
                    # Direct-history words (reverse, affirm, remand...) are NOT
                    # part of the filter: every appellate opinion has a
                    # disposition, so excluding them would leave no controls.
                    kind = "keyword" if strong_near >= 1 else "control"
                    target = TARGET_KEYWORD_PER_CELL if kind == "keyword" else TARGET_CONTROL_PER_CELL
                    if len(selected[cell][kind]) >= target:
                        continue
                    selected[cell][kind].append(cid)
                    c = clusters[cid]
                    n_cited = (
                        OpinionsCited.objects.filter(citing_opinion__cluster_id=cid)
                        .values("cited_opinion__cluster_id").distinct().count()
                    )
                    if cid not in already:
                        payload = {
                            "cluster_id": cid,
                            "opinions": [
                                {"id": o.id, "type": o.type,
                                 "source": "html_with_citations",
                                 "html": o.html_with_citations}
                                for o in ops
                            ],
                            "origin": "clreplica",
                            "fetched": datetime.now(timezone.utc).isoformat(),
                        }
                        (html_dir / f"{cid}.json").write_text(
                            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                        )
                    meta_rows.append({
                        "cluster_id": cid, "court": c.docket.court_id,
                        "court_group": group, "case_name": c.case_name,
                        "year": c.date_filed.year, "date_filed": c.date_filed.isoformat(),
                        "decade": dec_name, "length_bin": lb, "text_length": n_chars,
                        "n_opinions": len(ops),
                        "opinion_types": "|".join(o.type for o in ops),
                        "n_cited_clusters": n_cited, "selection": kind,
                        "strong_hits_near": strong_near, "dh_hits_near": dh_near,
                        "signal_hits_near": sig_near,
                        "strong_terms": "|".join(sorted(t for (g, t), v in hits.items()
                                                        if g == "strong" and v[1] > 0)),
                    })
                    for (g, t), (n, near) in sorted(hits.items()):
                        term_rows.append({"cluster_id": cid, "term_group": g, "term": t,
                                          "hits": n, "hits_near_citation": near})
                filled = {lb: (len(selected[(group, dec_name, lb)]["keyword"]),
                               len(selected[(group, dec_name, lb)]["control"]))
                          for lb, _, _ in LENGTH_BINS}
                print(f"  scanned {scanned[(group, dec_name)]:4d}  fill {filled}  "
                      f"{time.time() - t0:6.0f}s")

    # ── Authorities for every selected cluster ───────────────────────────
    sel_ids = [r["cluster_id"] for r in meta_rows]
    auth_rows: list[dict] = []
    for i in range(0, len(sel_ids), 100):
        chunk = sel_ids[i:i + 100]
        pairs = (
            OpinionsCited.objects.filter(citing_opinion__cluster_id__in=chunk)
            .values_list("citing_opinion__cluster_id", "cited_opinion__cluster_id",
                         "cited_opinion__cluster__case_name")
            .distinct()
        )
        cited_ids = {p[1] for p in pairs}
        cites = defaultdict(list)
        for cl in OpinionCluster.objects.filter(pk__in=cited_ids).prefetch_related("citations"):
            cites[cl.pk] = [str(x) for x in cl.citations.all()]
        for citing, cited, name in pairs:
            if citing == cited:
                continue
            auth_rows.append({"citing_cluster_id": citing, "cited_cluster_id": cited,
                              "cited_case_name": name,
                              "cited_case_citations": "; ".join(cites.get(cited, []))})

    # ── Write CSVs ───────────────────────────────────────────────────────
    def write(name, rows, fields):
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    write("sample_metadata.csv", meta_rows, list(meta_rows[0].keys()) if meta_rows else ["cluster_id"])
    write("term_hits.csv", term_rows, ["cluster_id", "term_group", "term", "hits", "hits_near_citation"])
    write("authority_metadata.csv", auth_rows,
          ["citing_cluster_id", "cited_cluster_id", "cited_case_name", "cited_case_citations"])
    fill_rows = []
    for group in groups:
        for dec_name, _, _ in DECADES:
            for lb, _, _ in LENGTH_BINS:
                s = selected[(group, dec_name, lb)]
                fill_rows.append({"court_group": group, "decade": dec_name, "length_bin": lb,
                                  "keyword_target": TARGET_KEYWORD_PER_CELL,
                                  "keyword_selected": len(s["keyword"]),
                                  "control_target": TARGET_CONTROL_PER_CELL,
                                  "control_selected": len(s["control"]),
                                  "candidates_scanned": scanned[(group, dec_name)]})
    write("cell_fill.csv", fill_rows, list(fill_rows[0].keys()))

    summary = {
        "seed": SEED, "target_keyword_per_cell": TARGET_KEYWORD_PER_CELL,
        "target_control_per_cell": TARGET_CONTROL_PER_CELL,
        "candidate_cap_per_group_decade": CANDIDATE_CAP_PER_GROUP_DECADE,
        "near_citation_chars": NEAR_CITATION_CHARS, "start_year": START_YEAR,
        "n_excluded": len(exclude), "n_selected": len(meta_rows),
        "n_keyword": sum(r["selection"] == "keyword" for r in meta_rows),
        "n_control": sum(r["selection"] == "control" for r in meta_rows),
        "n_authority_pairs": len(auth_rows),
        "total_text_chars": sum(r["text_length"] for r in meta_rows),
        "by_group": dict(Counter(r["court_group"] for r in meta_rows)),
        "by_decade": dict(Counter(r["decade"] for r in meta_rows)),
        "by_length": dict(Counter(r["length_bin"] for r in meta_rows)),
        "strong_term_opinions": dict(Counter(
            t for r in meta_rows for t in r["strong_terms"].split("|") if t).most_common()),
        "unfilled_cells": [f"{r['court_group']}/{r['decade']}/{r['length_bin']}"
                           for r in fill_rows
                           if r["keyword_selected"] < r["keyword_target"]
                           or r["control_selected"] < r["control_target"]],
        "elapsed_s": round(time.time() - t0),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"{k}: {v}")


run()
