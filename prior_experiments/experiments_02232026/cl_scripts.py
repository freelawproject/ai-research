import csv
from datetime import date

from dateutil.relativedelta import relativedelta
from django.db.models import Count, F
from django.db.models.functions import Length

from cl.search.models import Opinion, OpinionCluster, OpinionsCited


def extract_cluster_data(
    output_path: str = "cluster_data.csv", chunk_size: int = 1000
) -> None:
    """Extract OpinionCluster data from the last 50 years to CSV.

    For each cluster, saves:
    - cluster_id
    - court (court ID from the parent docket)
    - num_authorities (distinct cited clusters, excluding self-citations)
    - opinion_text_length (sum of best_text lengths across all opinions)
    """
    cutoff_date = date.today() - relativedelta(years=50)

    clusters_qs = (
        OpinionCluster.objects.filter(date_filed__gte=cutoff_date)
        .select_related("docket__court")
        .order_by("pk")
    )

    total = clusters_qs.count()
    print(f"Found {total:,} clusters filed since {cutoff_date}.")

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cluster_id",
            "court",
            "num_authorities",
            "opinion_text_length",
        ])

        processed = 0
        last_pk = 0

        while True:
            chunk = list(clusters_qs.filter(pk__gt=last_pk)[:chunk_size])
            if not chunk:
                break

            last_pk = chunk[-1].pk
            chunk_ids = [c.pk for c in chunk]

            # Count distinct cited clusters per citing cluster,
            # excluding intra-cluster citations (self-references).
            authority_counts = dict(
                OpinionsCited.objects.filter(
                    citing_opinion__cluster_id__in=chunk_ids
                )
                .exclude(
                    cited_opinion__cluster_id=F(
                        "citing_opinion__cluster_id"
                    )
                )
                .values("citing_opinion__cluster_id")
                .annotate(
                    num=Count(
                        "cited_opinion__cluster_id", distinct=True
                    )
                )
                .values_list("citing_opinion__cluster_id", "num")
            )

            # Sum the length of each opinion's best text per cluster.
            text_lengths: dict[int, int] = {}
            for cluster_id, text_len in (
                Opinion.objects.filter(cluster_id__in=chunk_ids)
                .with_best_text()
                .annotate(text_len=Length("best_text"))
                .values_list("cluster_id", "text_len")
            ):
                text_lengths[cluster_id] = (
                    text_lengths.get(cluster_id, 0) + (text_len or 0)
                )

            for cluster in chunk:
                writer.writerow([
                    cluster.id,
                    cluster.docket.court_id,
                    authority_counts.get(cluster.id, 0),
                    text_lengths.get(cluster.id, 0),
                ])

            processed += len(chunk)
            print(f"Processed {processed:,}/{total:,} clusters...")

    print(f"Done. Output written to {output_path}")


def add_citation_counts(
    input_path: str = "cluster_data.csv",
    output_path: str = "cluster_data.csv",
    chunk_size: int = 1000,
) -> None:
    """Read cluster_data.csv and append a citation_count column.

    Looks up OpinionCluster.citation_count in bulk for each chunk of
    cluster IDs, then rewrites the CSV with the new column.
    """
    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Read {len(rows):,} rows from {input_path}.")

    cluster_ids = [int(row["cluster_id"]) for row in rows]

    # Bulk-fetch citation_count for all clusters in chunks.
    citation_counts: dict[int, int] = {}
    for i in range(0, len(cluster_ids), chunk_size):
        chunk_ids = cluster_ids[i : i + chunk_size]
        citation_counts.update(
            OpinionCluster.objects.filter(pk__in=chunk_ids)
            .values_list("pk", "citation_count")
        )
        print(
            f"Fetched citation counts for "
            f"{min(i + chunk_size, len(cluster_ids)):,}/{len(cluster_ids):,} clusters..."
        )

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cluster_id",
            "court",
            "num_authorities",
            "opinion_text_length",
            "citation_count",
        ])
        for row in rows:
            cid = int(row["cluster_id"])
            writer.writerow([
                row["cluster_id"],
                row["court"],
                row["num_authorities"],
                row["opinion_text_length"],
                citation_counts.get(cid, 0),
            ])

    print(f"Done. Output written to {output_path}")
