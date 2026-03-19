import csv
import json
import os
import re

from django.db.models import Count, F

from cl.citations.models import UnmatchedCitation
from cl.search.models import Citation, Opinion, OpinionCluster, OpinionsCited


def extract_citing_opinions(
    ids_path: str = "citing_ids.txt",
    output_dir: str = "citing",
    csv_path: str = "citing_data.csv",
    chunk_size: int = 100,
) -> None:
    """Extract opinion texts for cluster IDs listed in citing_ids.txt.

    For each cluster_id, strips HTML from the best opinion text and saves it
    to output_dir/{cluster_id}.txt, then writes a summary CSV with:
    - cluster_id
    - year_filed (year extracted from date_filed)
    - court
    - num_authorities (distinct cited clusters, excluding self-citations)
    - citation_count
    - text_length (characters after HTML removal)
    - authorities (JSON dict: {cited_cluster_id: {citations, num_cited, case_name}})
    """
    os.makedirs(output_dir, exist_ok=True)

    with open(ids_path) as f:
        content = f.read()

    cluster_ids = [int(s.strip()) for s in content.split(",") if s.strip()]
    print(f"Found {len(cluster_ids):,} cluster IDs in {ids_path}.")

    tag_re = re.compile(r"<[^>]+>")
    citation_span_re = re.compile(
        r'<span\s[^>]*class="[^"]*\bcitation\b[^"]*"[^>]*>(.*?)</span>',
        re.DOTALL,
    )
    non_authority_tag_re = re.compile(r"<(?!/?targetCase>)[^>]+>")

    def _citation_to_authority(m: re.Match[str]) -> str:
        return f"<targetCase>{tag_re.sub('', m.group(1))}</targetCase>"

    with open(csv_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([
            "cluster_id",
            "year_filed",
            "court",
            "num_authorities",
            "citation_count",
            "text_length",
            "authorities",
        ])

        processed = 0
        for i in range(0, len(cluster_ids), chunk_size):
            chunk_ids = cluster_ids[i : i + chunk_size]

            clusters = {
                c.pk: c
                for c in OpinionCluster.objects.filter(pk__in=chunk_ids)
                .select_related("docket__court")
            }

            # Count citation links per (citing_cluster, cited_cluster) pair,
            # excluding intra-cluster self-citations.
            cited_pairs = (
                OpinionsCited.objects.filter(
                    citing_opinion__cluster_id__in=chunk_ids
                )
                .exclude(
                    cited_opinion__cluster_id=F("citing_opinion__cluster_id")
                )
                .values(
                    "citing_opinion__cluster_id",
                    "cited_opinion__cluster_id",
                )
                .annotate(num_cited=Count("id"))
            )

            # Build per-citing-cluster authority list with counts.
            citing_to_cited: dict[int, dict[int, int]] = {}
            all_cited_ids: set[int] = set()
            for row in cited_pairs:
                citing_cid = row["citing_opinion__cluster_id"]
                cited_cid = row["cited_opinion__cluster_id"]
                citing_to_cited.setdefault(citing_cid, {})[cited_cid] = row[
                    "num_cited"
                ]
                all_cited_ids.add(cited_cid)

            authority_counts = {
                cid: len(cited) for cid, cited in citing_to_cited.items()
            }

            # Fetch citation strings for every cited cluster in one query.
            cited_citations: dict[int, list[str]] = {}
            for cid, volume, reporter, page in Citation.objects.filter(
                cluster_id__in=all_cited_ids
            ).values_list("cluster_id", "volume", "reporter", "page"):
                cited_citations.setdefault(cid, []).append(
                    f"{volume} {reporter.replace(' ', '')} {page}"
                )

            # Fetch case names for cited clusters.
            cited_case_names: dict[int, str] = dict(
                OpinionCluster.objects.filter(pk__in=all_cited_ids)
                .values_list("pk", "case_name")
            )

            cluster_texts: dict[int, str] = {}
            for opinion in Opinion.objects.filter(
                cluster_id__in=chunk_ids
            ).with_best_text():
                cluster_texts[opinion.cluster_id] = cluster_texts.get(
                    opinion.cluster_id, ""
                ) + (opinion.best_text or "")

            for cluster_id in chunk_ids:
                cluster = clusters.get(cluster_id)
                if cluster is None:
                    print(f"Warning: cluster {cluster_id} not found, skipping.")
                    continue

                raw_text = cluster_texts.get(cluster_id, "")
                raw_text = citation_span_re.sub(_citation_to_authority, raw_text)
                clean_text = non_authority_tag_re.sub("", raw_text)

                txt_path = os.path.join(output_dir, f"{cluster_id}.txt")
                with open(txt_path, "w") as txt_file:
                    txt_file.write(clean_text)

                authorities = {
                    cited_cid: {
                        "citations": cited_citations.get(cited_cid, []),
                        "num_cited": num,
                        "case_name": cited_case_names.get(cited_cid, ""),
                    }
                    for cited_cid, num in citing_to_cited.get(
                        cluster_id, {}
                    ).items()
                }

                writer.writerow([
                    cluster_id,
                    cluster.date_filed.year if cluster.date_filed else "",
                    cluster.docket.court_id,
                    authority_counts.get(cluster_id, 0),
                    cluster.citation_count,
                    len(clean_text),
                    json.dumps(authorities),
                ])

            processed += len(chunk_ids)
            print(f"Processed {processed:,}/{len(cluster_ids):,} clusters...")

    print(f"Done. Texts saved to {output_dir}/, metadata to {csv_path}")


def export_unmatched_citations(
    ids_path: str = "citing_ids.txt",
    csv_path: str = "unmatched_citations.csv",
    chunk_size: int = 500,
) -> None:
    """Query UnmatchedCitation for each citing cluster ID and save to CSV.

    Reads cluster IDs from ids_path, fetches all UnmatchedCitation records
    linked to those clusters, and writes one row per citing_id (cluster_id)
    with aggregated unmatched citation data.
    """
    with open(ids_path) as f:
        content = f.read()

    cluster_ids = [int(s.strip()) for s in content.split(",") if s.strip()]
    print(f"Found {len(cluster_ids):,} cluster IDs in {ids_path}.")

    # Fetch all unmatched citations for the given clusters in chunks.
    rows: dict[int, list[dict[str, str | int | None]]] = {}
    for i in range(0, len(cluster_ids), chunk_size):
        chunk_ids = cluster_ids[i : i + chunk_size]
        unmatched_qs = UnmatchedCitation.objects.filter(
            citing_opinion__cluster_id__in=chunk_ids,
        ).values_list(
            "citing_opinion__cluster_id",
            "volume",
            "reporter",
            "page",
            "type",
            "status",
            "citation_string",
            "court_id",
            "year",
        )
        for (
            cid,
            volume,
            reporter,
            page,
            cite_type,
            status,
            citation_string,
            court_id,
            year,
        ) in unmatched_qs:
            rows.setdefault(cid, []).append({
                "citation_string": f"{volume} {reporter.replace(' ', '')} {page}",
                "court_id": court_id,
                "year": year,
            })

    with open(csv_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([
            "citing_cluster_id",
            "unmatched_count",
            "unmatched_citations",
        ])
        for cluster_id in cluster_ids:
            citations = rows.get(cluster_id, [])
            writer.writerow([
                cluster_id,
                len(citations),
                json.dumps(citations),
            ])

    print(
        f"Done. Wrote {len(cluster_ids):,} rows to {csv_path} "
        f"({sum(len(v) for v in rows.values()):,} total unmatched citations)."
    )
