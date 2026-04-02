"""Script to query the CourtListener Django database for cluster metadata,
opinion texts, authorities, and unmatched citations.
"""

import csv
import re
from pathlib import Path

from cl.citations.models import UnmatchedCitation
from cl.search.models import Court, Opinion, OpinionCluster, OpinionsCited

# Regex to match citation spans:
#   <span class="citation" ...><a href="...">LINK TEXT</a></span>
# Also handles the "multiple-matches" variant.
CITATION_SPAN_RE = re.compile(
    r'<span\s+class="citation[^"]*"[^>]*>'  # opening <span>
    r'\s*<a\s[^>]*>'                         # opening <a>
    r'(.*?)'                                 # captured link text
    r'</a>\s*</span>',                       # closing </a></span>
    re.DOTALL,
)


def process_opinion_html(html: str) -> str:
    """Replace citation link spans with <citedCase> tags, then strip
    all remaining HTML tags."""
    # Step 1: Replace citation spans with <citedCase> wrappers
    text = CITATION_SPAN_RE.sub(r"<citedCase>\1</citedCase>", html)
    # Step 2: Strip all remaining HTML tags (but not <citedCase>)
    text = re.sub(r"<(?!/?citedCase>)[^>]+>", "", text)
    return text


def run() -> None:
    base_dir = Path("/opt/courtlistener")
    output_dir = base_dir / "opinion_texts"
    output_dir.mkdir(exist_ok=True)

    # ── Step 1: Read ids.csv ─────────────────────────────────────────
    ids_csv_path = base_dir / "ids.csv"
    with open(ids_csv_path) as f:
        existing_ids = [int(line.strip()) for line in f if line.strip()]

    print(f"Loaded {len(existing_ids)} cluster IDs from ids.csv")

    # ── Step 1: Court, case name, year for each cluster_id ───────────
    clusters = (
        OpinionCluster.objects.filter(pk__in=existing_ids)
        .select_related("docket__court")
        .only("case_name", "date_filed", "docket__court__id")
    )
    print("\n=== Step 1: Cluster info from ids.csv ===")
    for c in clusters:
        print(
            f"  ID={c.pk}  Court={c.docket.court_id}  "
            f"Case={c.case_name}  Year={c.date_filed.year}"
        )

    # ── Step 2: Sample 50 clusters per circuit court ─────────────────
    target_court_ids = ["ca1", "ca10", "ca11", "ca2", "ca3", "ca4", "ca5",
    "ca6", "ca7", "ca8", "ca9", "cadc", "cafc"]
    circuit_courts = Court.objects.filter(id__in=target_court_ids)
    circuit_court_ids = set(circuit_courts.values_list("id", flat=True))
    print(f"\nCircuit courts found: {sorted(circuit_court_ids)}")

    sampled_ids: list[int] = []
    for court in circuit_courts:
        sample = list(
            OpinionCluster.objects.filter(
                docket__court=court,
                sub_opinions__cited_opinions__isnull=False,
            )
            .exclude(pk__in=existing_ids)
            .distinct()
            .order_by("?")[:50]
            .values_list("pk", flat=True)
        )
        sampled_ids.extend(sample)
        print(f"  Sampled {len(sample)} clusters from {court.id}")

    print(f"Total sampled: {len(sampled_ids)}")

    # ── Step 3: Filter ids.csv to circuit courts only ────────────────
    circuit_existing_ids = list(
        OpinionCluster.objects.filter(
            pk__in=existing_ids,
            docket__court_id__in=target_court_ids,
        ).values_list("pk", flat=True)
    )
    print(
        f"\nCircuit court clusters from ids.csv: {len(circuit_existing_ids)} "
        f"out of {len(existing_ids)}"
    )

    # ── Step 4: Combined set ─────────────────────────────────────────
    circuit_existing_set = set(circuit_existing_ids)
    all_ids = list(circuit_existing_set | set(sampled_ids))
    print(f"\nTotal combined cluster IDs: {len(all_ids)}")

    # ── 4a: Citing metadata CSV ──────────────────────────────────────
    citing_meta_path = base_dir / "citing_metadata.csv"
    authority_meta_path = base_dir / "authority_metadata.csv"
    unmatched_meta_path = base_dir / "unmatched_metadata.csv"
    cited_meta_path = base_dir / "cited_metadata.csv"

    citing_rows: list[dict] = []
    authority_rows: list[dict] = []
    unmatched_rows: list[dict] = []

    # Process in batches to avoid memory issues
    batch_size = 100
    for batch_start in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[batch_start : batch_start + batch_size]

        clusters_qs = (
            OpinionCluster.objects.filter(pk__in=batch_ids)
            .select_related("docket__court")
            .prefetch_related("sub_opinions", "citations")
        )

        for cluster in clusters_qs:
            court_id = cluster.docket.court_id
            case_name = cluster.case_name
            year = cluster.date_filed.year

            # Get opinions with best text
            opinions = Opinion.objects.filter(
                cluster=cluster
            ).with_best_text()

            opinion_text = ""
            for op in opinions:
                if op.best_text:
                    opinion_text += (
                        process_opinion_html(op.best_text) + "\n"
                    )

            text_length = len(opinion_text)

            # Save opinion text to file
            text_path = output_dir / f"{cluster.pk}.txt"
            with open(text_path, "w") as f:
                f.write(opinion_text)

            # Count authorities and unmatched citations
            sub_opinion_ids = list(
                cluster.sub_opinions.values_list("pk", flat=True)
            )

            authority_count = OpinionsCited.objects.filter(
                citing_opinion_id__in=sub_opinion_ids
            ).count()

            unmatched_count = UnmatchedCitation.objects.filter(
                citing_opinion_id__in=sub_opinion_ids
            ).count()

            source = "existing" if cluster.pk in circuit_existing_set else "sampled"

            citing_rows.append(
                {
                    "cluster_id": cluster.pk,
                    "source": source,
                    "court": court_id,
                    "case_name": case_name,
                    "year": year,
                    "num_authorities": authority_count,
                    "num_unmatched_citations": unmatched_count,
                    "opinion_text_length": text_length,
                }
            )

            # ── 4b: Authority metadata ───────────────────────────────
            authority_links = (
                OpinionsCited.objects.filter(
                    citing_opinion_id__in=sub_opinion_ids
                )
                .select_related(
                    "cited_opinion__cluster__docket__court",
                )
                .prefetch_related("cited_opinion__cluster__citations")
            )

            for link in authority_links:
                cited_cluster = link.cited_opinion.cluster
                cited_citations = ", ".join(
                    str(c) for c in cited_cluster.citations.all()
                )
                authority_rows.append(
                    {
                        "unique_id": f"{cluster.pk}-{cited_cluster.pk}",
                        "citing_cluster_id": cluster.pk,
                        "cited_cluster_id": cited_cluster.pk,
                        "cited_case_name": cited_cluster.case_name,
                        "cited_case_citations": cited_citations,
                    }
                )

            # ── 4c: Unmatched citation metadata ──────────────────────
            unmatched_cites = UnmatchedCitation.objects.filter(
                citing_opinion_id__in=sub_opinion_ids
            )

            for idx, uc in enumerate(unmatched_cites):
                unmatched_rows.append(
                    {
                        "unique_id": f"{cluster.pk}-unmatched-{idx}",
                        "citing_cluster_id": cluster.pk,
                        "citation_string": uc.citation_string,
                    }
                )

        print(
            f"  Processed batch {batch_start // batch_size + 1}"
            f"/{(len(all_ids) + batch_size - 1) // batch_size}"
        )

    # Write citing_metadata.csv
    with open(citing_meta_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "cluster_id",
                "source",
                "court",
                "case_name",
                "year",
                "num_authorities",
                "num_unmatched_citations",
                "opinion_text_length",
            ],
        )
        writer.writeheader()
        writer.writerows(citing_rows)
    print(f"\nWrote {len(citing_rows)} rows to {citing_meta_path}")

    # Write authority_metadata.csv
    with open(authority_meta_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "unique_id",
                "citing_cluster_id",
                "cited_cluster_id",
                "cited_case_name",
                "cited_case_citations",
            ],
        )
        writer.writeheader()
        writer.writerows(authority_rows)
    print(f"Wrote {len(authority_rows)} rows to {authority_meta_path}")

    # Write unmatched_metadata.csv
    with open(unmatched_meta_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "unique_id",
                "citing_cluster_id",
                "citation_string",
            ],
        )
        writer.writeheader()
        writer.writerows(unmatched_rows)
    print(f"Wrote {len(unmatched_rows)} rows to {unmatched_meta_path}")

    # ── Step 5: Combine authority + unmatched into cited_metadata.csv ─
    with open(cited_meta_path, "w", newline="") as f:
        fieldnames = [
            "unique_id",
            "citing_cluster_id",
            "type",
            "cited_cluster_id",
            "cited_case_name",
            "cited_case_citations",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in authority_rows:
            writer.writerow(
                {
                    "unique_id": row["unique_id"],
                    "citing_cluster_id": row["citing_cluster_id"],
                    "type": "authority",
                    "cited_cluster_id": row["cited_cluster_id"],
                    "cited_case_name": row["cited_case_name"],
                    "cited_case_citations": row["cited_case_citations"],
                }
            )

        for row in unmatched_rows:
            writer.writerow(
                {
                    "unique_id": row["unique_id"],
                    "citing_cluster_id": row["citing_cluster_id"],
                    "type": "unmatched",
                    "cited_cluster_id": "",
                    "cited_case_name": "",
                    "cited_case_citations": row["citation_string"],
                }
            )

    total_cited = len(authority_rows) + len(unmatched_rows)
    print(f"Wrote {total_cited} rows to {cited_meta_path}")

    print("\nDone!")
