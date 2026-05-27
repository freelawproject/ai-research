"""Fetch raw CL data for citator pipeline Phase 1 — one JSON per cluster.

Runs inside the courtlistener container. For each cluster_id, pulls every
Opinion record (id, type, html_with_citations) and all UnmatchedCitation rows
scoped to those opinions (id, citing_opinion_id, citation_string), and writes a
per-cluster JSON that `citator-pipeline/utils/assemble_tagged_text.py` consumes.

Only the fields Phase 1 needs are pulled. Parsed UnmatchedCitation components
(volume/reporter/page), status, court_id, year, type are intentionally omitted —
the safety net matches on `citation_string` directly.

Usage::

    docker cp data/cl_fetch_cluster_ids.csv cl-django:/opt/courtlistener/
    docker cp fetch_cl_data.py cl-django:/opt/courtlistener/
    docker exec cl-django python manage.py shell -c \\
        "exec(open('fetch_cl_data.py').read())"
    docker cp cl-django:/opt/courtlistener/cl_fetch/ ./data/

Input (in /opt/courtlistener):
    cl_fetch_cluster_ids.csv   one column `cluster_id`, one id per row

Output (in /opt/courtlistener/cl_fetch):
    {cluster_id}.json   {cluster_id, opinions[], unmatched_citations[]}

`html_with_citations` and `citation_string` are written verbatim (ensure_ascii
=False) so NBSPs / unicode survive for the downstream tree walk.
"""

import csv
import json
from pathlib import Path

from cl.citations.models import UnmatchedCitation
from cl.search.models import OpinionCluster


def run() -> None:
    base_dir = Path("/opt/courtlistener")
    output_dir = base_dir / "cl_fetch"
    output_dir.mkdir(exist_ok=True)

    # ── Step 1: Load cluster IDs ────────────────────────────────────────
    ids_csv_path = base_dir / "cl_fetch_cluster_ids.csv"
    cluster_ids: list[int] = []
    with open(ids_csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cluster_ids.append(int(row["cluster_id"]))
            except (TypeError, ValueError, KeyError):
                continue
    cluster_ids = sorted(set(cluster_ids))
    print(f"Loaded {len(cluster_ids)} cluster IDs from {ids_csv_path}")

    # ── Step 2: Pull opinions + unmatched citations per cluster ─────────
    missing_ids: list[int] = []
    written = 0
    total_opinions = 0
    total_unmatched = 0
    total_blank_html = 0

    for cid in cluster_ids:
        try:
            cluster = OpinionCluster.objects.prefetch_related(
                "sub_opinions"
            ).get(pk=cid)
        except OpinionCluster.DoesNotExist:
            missing_ids.append(cid)
            continue

        opinions: list[dict] = []
        opinion_ids: list[int] = []
        for op in cluster.sub_opinions.all():
            html = op.html_with_citations or ""
            if not html.strip():
                total_blank_html += 1
            opinions.append(
                {
                    "id": op.pk,
                    "type": op.type,
                    "html_with_citations": html,
                }
            )
            opinion_ids.append(op.pk)

        unmatched: list[dict] = []
        for uc in UnmatchedCitation.objects.filter(
            citing_opinion_id__in=opinion_ids
        ).only("id", "citing_opinion_id", "citation_string"):
            unmatched.append(
                {
                    "id": uc.pk,
                    "citing_opinion_id": uc.citing_opinion_id,
                    "citation_string": uc.citation_string,
                }
            )

        artifact = {
            "cluster_id": cid,
            "opinions": opinions,
            "unmatched_citations": unmatched,
        }
        (output_dir / f"{cid}.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2)
        )
        written += 1
        total_opinions += len(opinions)
        total_unmatched += len(unmatched)

    # ── Step 3: Report ──────────────────────────────────────────────────
    if missing_ids:
        missing_path = base_dir / "cl_fetch_missing_clusters.csv"
        with open(missing_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["cluster_id"])
            for cid in missing_ids:
                writer.writerow([cid])
        print(
            f"WARNING: {len(missing_ids)} cluster IDs not found in "
            f"OpinionCluster; wrote IDs to {missing_path}"
        )

    print()
    print("=== CL fetch summary ===")
    print(f"Requested clusters:        {len(cluster_ids):>8,}")
    print(f"Written cluster files:     {written:>8,}")
    print(f"Missing clusters:          {len(missing_ids):>8,}")
    print(f"Total opinion records:     {total_opinions:>8,}")
    print(f"  of which blank html:     {total_blank_html:>8,}")
    print(f"Total unmatched citations: {total_unmatched:>8,}")
    print()
    print(f"Output: {output_dir}")
    print("Done.")


run()
