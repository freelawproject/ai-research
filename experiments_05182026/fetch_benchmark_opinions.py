"""Fetch opinion text + metadata for the 0410 benchmark's citing clusters.

Reads a flat list of citing cluster IDs from `data/benchmark_cluster_ids.csv`,
pulls cleaned opinion text and per-cluster metadata from CourtListener, and
writes the files run_batch.py expects. Runs inside the courtlistener container.

Usage::

    docker cp data/benchmark_cluster_ids.csv cl-django:/opt/courtlistener/
    docker cp fetch_benchmark_opinions.py cl-django:/opt/courtlistener/
    docker exec cl-django python manage.py shell -c \\
        "exec(open('fetch_benchmark_opinions.py').read())"
    docker cp cl-django:/opt/courtlistener/opinion_texts/ ./data/
    docker cp cl-django:/opt/courtlistener/citing_metadata.csv ./data/

Outputs (in /opt/courtlistener):
    opinion_texts/{cluster_id}.txt  cleaned opinion text, one per cluster
    citing_metadata.csv             cluster_id, source, court, case_name, year,
                                    num_authorities, num_unmatched_citations,
                                    opinion_text_length

`source` is set to "sampled" on every row to satisfy run_batch.py's source filter.
"""

import csv
import re
from pathlib import Path

from cl.citations.models import UnmatchedCitation
from cl.search.models import Opinion, OpinionCluster, OpinionsCited

CITATION_SPAN_RE = re.compile(
    r'<span\s+class="citation[^"]*"[^>]*>'
    r"\s*<a\s[^>]*>"
    r"(.*?)"
    r"</a>\s*</span>",
    re.DOTALL,
)


def process_opinion_html(html: str) -> str:
    """Replace citation link spans with <citedCase> tags, then strip
    all remaining HTML tags."""
    text = CITATION_SPAN_RE.sub(r"<citedCase>\1</citedCase>", html)
    text = re.sub(r"<(?!/?citedCase>)[^>]+>", "", text)
    return text


def get_cluster_text(cluster: OpinionCluster) -> str:
    """Concat best_text across all sub-opinions of a cluster, cleaned."""
    opinions = Opinion.objects.filter(cluster=cluster).with_best_text()
    parts: list[str] = []
    for op in opinions:
        if op.best_text:
            parts.append(process_opinion_html(op.best_text))
    return "\n".join(parts)


def run() -> None:
    base_dir = Path("/opt/courtlistener")
    output_dir = base_dir / "opinion_texts"
    output_dir.mkdir(exist_ok=True)

    # ── Step 1: Load benchmark cluster IDs ──────────────────────────────
    ids_csv_path = base_dir / "benchmark_cluster_ids.csv"
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

    # ── Step 2: Pull text + metadata per cluster, batched ───────────────
    metadata_rows: list[dict] = []
    missing_ids: list[int] = []

    batch_size = 100
    for batch_start in range(0, len(cluster_ids), batch_size):
        batch_ids = cluster_ids[batch_start : batch_start + batch_size]

        clusters_qs = (
            OpinionCluster.objects.filter(pk__in=batch_ids)
            .select_related("docket__court")
            .prefetch_related("sub_opinions")
        )

        found_in_batch: set[int] = set()
        for cluster in clusters_qs:
            cid = cluster.pk
            found_in_batch.add(cid)

            text = get_cluster_text(cluster)
            text_length = len(text)
            (output_dir / f"{cid}.txt").write_text(text)

            sub_opinion_ids = list(
                cluster.sub_opinions.values_list("pk", flat=True)
            )

            # num_authorities: distinct cited clusters via OpinionsCited
            num_authorities = (
                OpinionsCited.objects.filter(
                    citing_opinion_id__in=sub_opinion_ids
                )
                .values("cited_opinion__cluster_id")
                .distinct()
                .count()
            )

            # num_unmatched_citations: citations CL couldn't resolve to a cluster
            num_unmatched = UnmatchedCitation.objects.filter(
                citing_opinion_id__in=sub_opinion_ids
            ).count()

            metadata_rows.append(
                {
                    "cluster_id": cid,
                    "source": "sampled",
                    "court": cluster.docket.court_id or "",
                    "case_name": cluster.case_name or "",
                    "year": cluster.date_filed.year if cluster.date_filed else "",
                    "num_authorities": num_authorities,
                    "num_unmatched_citations": num_unmatched,
                    "opinion_text_length": text_length,
                }
            )

        for cid in batch_ids:
            if cid not in found_in_batch:
                missing_ids.append(cid)

        n_done = min(batch_start + batch_size, len(cluster_ids))
        n_total = len(cluster_ids)
        print(
            f"  Processed {n_done}/{n_total} clusters "
            f"(batch {batch_start // batch_size + 1}/"
            f"{(n_total + batch_size - 1) // batch_size})"
        )

    # ── Step 3: Write citing_metadata.csv ───────────────────────────────
    metadata_path = base_dir / "citing_metadata.csv"
    metadata_fields = [
        "cluster_id",
        "source",
        "court",
        "case_name",
        "year",
        "num_authorities",
        "num_unmatched_citations",
        "opinion_text_length",
    ]
    with open(metadata_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=metadata_fields)
        writer.writeheader()
        writer.writerows(metadata_rows)
    print(f"\nWrote {len(metadata_rows)} rows to {metadata_path}")

    # ── Step 4: Report missing clusters (if any) ────────────────────────
    if missing_ids:
        missing_path = base_dir / "benchmark_missing_clusters.csv"
        with open(missing_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["cluster_id"])
            for cid in missing_ids:
                writer.writerow([cid])
        print(
            f"WARNING: {len(missing_ids)} cluster IDs not found in OpinionCluster; "
            f"wrote IDs to {missing_path}"
        )

    # ── Step 5: Print top-line summary ──────────────────────────────────
    total_text = sum(r["opinion_text_length"] for r in metadata_rows)
    total_auth = sum(r["num_authorities"] for r in metadata_rows)
    total_unmatched = sum(r["num_unmatched_citations"] for r in metadata_rows)
    print()
    print("=== Benchmark fetch summary ===")
    print(f"Requested clusters:         {len(cluster_ids):>8,}")
    print(f"Fetched clusters:           {len(metadata_rows):>8,}")
    print(f"Missing clusters:           {len(missing_ids):>8,}")
    print(f"Total opinion text chars:   {total_text:>14,}")
    print(f"Approx tokens (chars / 4):  {total_text // 4:>14,}")
    print(f"Total authorities:          {total_auth:>14,}")
    print(f"Total unmatched citations:  {total_unmatched:>14,}")
    print()
    print("Done.")


run()
