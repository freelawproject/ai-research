import os
import json
import random

random.seed(42)

from cl.search.models import Docket, OpinionCluster, Opinion, OpinionsCited


COURT_IDS = ['ala',
 'alaska',
 'ariz',
 'ark',
 'cal',
 'colo',
 'conn',
 'del',
 'dc',
 'fla',
 'ga',
 'haw',
 'idaho',
 'ill',
 'ind',
 'iowa',
 'kan',
 'ky',
 'la',
 'me',
 'md',
 'mass',
 'mich',
 'minn',
 'miss',
 'mo',
 'mont',
 'neb',
 'nev',
 'nh',
 'nj',
 'nm',
 'ny',
 'nc',
 'nd',
 'ohio',
 'okla',
 'or',
 'pa',
 'ri',
 'sc',
 'sd',
 'tenn',
 'tex',
 'texcrimapp',
 'utah',
 'vt',
 'va',
 'wash',
 'wva',
 'wis',
 'wyo',
 'alacrimapp',
 'alacivapp',
 'alaskactapp',
 'arizctapp',
 'arkctapp',
 'calctapp',
 'calappdeptsuper',
 'coloctapp',
 'connappct',
 'connsuperct',
 'delch',
 'delsuperct',
 'fladistctapp',
 'gactapp',
 'hawapp',
 'idahoctapp',
 'illappct',
 'indctapp',
 'iowactapp',
 'kanctapp',
 'kyctapp',
 'lactapp',
 'mdctspecapp',
 'massappct',
 'masssuperct',
 'massdistct',
 'michctapp',
 'minnctapp',
 'missctapp',
 'moctapp',
 'nebctapp',
 'nevapp',
 'njsuperctappdiv',
 'nmctapp',
 'nyappdiv',
 'nyappterm',
 'ncctapp',
 'ncsuperct',
 'ndctapp',
 'ohioctapp',
 'oklacivapp',
 'oklacrimapp',
 'orctapp',
 'pasuperct',
 'pacommwct',
 'risuperct',
 'scctapp',
 'tennctapp',
 'tenncrimapp',
 'texapp',
 'texjpml',
 'utahctapp',
 'vtsuperct',
 'vactapp',
 'washctapp',
 'wvactapp',
 'wisctapp',
 'mesuperct',
 'nhsuperct',
 'nysupct',
 'nycountyct',
 'nydistct',
 'nyjustct']


def get_ids(sample_size=200, court_ids=COURT_IDS):

    # Sample 200 dockets from the specified courts
    docket_ids = list(
        Docket.objects.filter(court__in=court_ids).values_list("id", flat=True)
    )
    sampled_dockets = random.sample(docket_ids, sample_size)
    print(f"target dockets sampled: {len(sampled_dockets)}")

    # Retrieve the cluster_ids for the sampled dockets
    sampled_cluster_ids = list(
        set(
            OpinionCluster.objects.filter(docket_id__in=sampled_dockets).values_list(
                "id", flat=True
            )
        )
    )
    print(f"target clusters retrieved: {len(sampled_cluster_ids)}")

    # Retrieve the opinion_ids for the opinions in the sampled clusters
    sampled_opinion_ids = list(
        Opinion.objects.filter(cluster_id__in=sampled_cluster_ids).values_list(
            "id", flat=True
        )
    )
    print("target opinions retrieved")

    # Retrieve the citing_opinion_ids for the sampled opinions
    citing_opinion_ids = list(
        OpinionsCited.objects.filter(cited_opinion__in=sampled_opinion_ids).values_list(
            "citing_opinion_id", flat=True
        )
    )
    print("citing opinions retrieved")

    # Retrieve the cluster_ids for the citing opinions
    citing_cluster_ids = list(
        set(
            Opinion.objects.filter(id__in=citing_opinion_ids).values_list(
                "cluster_id", flat=True
            )
        )
    )
    print(f"citing clusters retrieved: {len(citing_cluster_ids)}")

    # Save the target and citing cluster IDs to text files
    with open("target_ids.txt", "w") as f:
        for cluster_id in sampled_cluster_ids:
            f.write(f"{cluster_id}\n")

    with open("citing_ids.txt", "w") as f:
        for cluster_id in citing_cluster_ids:
            f.write(f"{cluster_id}\n")


def process_citing_opinions(chunk_size=100):
    all_results = {}
    filepath = "raw_citing_opinions/"

    with open("citing_ids.txt", "r") as f:
        citing_ids = [line.strip() for line in f]

    chunks = [
        citing_ids[i : i + chunk_size] for i in range(0, len(citing_ids), chunk_size)
    ]

    for i, chunk in enumerate(chunks):
        print(f"Processing chunk {i + 1}/{len(chunks)}")
        clusters = OpinionCluster.objects.filter(id__in=chunk).select_related("docket")

        for cluster in clusters:
            cluster_id = cluster.id

            court = Docket.objects.get(id=cluster.docket_id).court

            absolute_url = cluster.get_absolute_url()
            case_law_url = f"https://www.courtlistener.com{absolute_url}"

            opinion_data = []

            opinions = Opinion.objects.filter(cluster_id=cluster_id).with_best_text()

            cited_opinion_ids = list(
                OpinionsCited.objects.filter(
                    citing_opinion__in=list(opinions.values_list("id", flat=True))
                ).values_list("cited_opinion_id", flat=True)
            )

            cited_cluster_ids = list(
                Opinion.objects.filter(id__in=cited_opinion_ids).values_list(
                    "cluster_id", flat=True
                )
            )

            for opinion in opinions:
                opinion_metadata = {
                    "opinion_id": None,
                    "opinion_api": None,
                    "opinion_type": None,
                    "opinion_source": None,
                    "opinion_filename": None,
                }
                opinion_id = opinion.id
                opinion_api = (
                    f"https://www.courtlistener.com/api/rest/v4/opinions/{opinion_id}/"
                )
                opinion_type = opinion.type
                opinion_filename = f"{cluster_id}_{opinion_type}.txt"
                destination = os.path.join(filepath, opinion_filename)

                opinion_source = opinion.best_text_source
                raw_opinion_text = opinion.best_text

                opinion_metadata["opinion_id"] = opinion_id
                opinion_metadata["opinion_url"] = opinion_api
                opinion_metadata["opinion_type"] = opinion_type
                opinion_metadata["opinion_filename"] = opinion_filename
                opinion_metadata["opinion_source"] = opinion_source

                opinion_data.append(opinion_metadata)

                with open(destination, "w") as file:
                    file.write(raw_opinion_text)

            results = {
                "citing_url": case_law_url,
                "citing_court_id": court.id,
                "citing_court_name": court.full_name,
                "opinion_data": opinion_data,
                "cited_cluster_ids": cited_cluster_ids,
            }

            all_results[cluster_id] = results

    # Save results to a JSON file
    output_filename = f"citing_opinions.json"
    with open(output_filename, "w") as output_file:
        json.dump(all_results, output_file)


def get_cited_opinions(chunk_size=100):

    cited_cluster_data = {}

    with open("cited_ids.txt", "r") as f:
        cited_ids = [line.strip() for line in f]

        chunks = [
            cited_ids[i : i + chunk_size] for i in range(0, len(cited_ids), chunk_size)
        ]

        for i, chunk in enumerate(chunks):
            print(f"Processing chunk {i + 1}/{len(chunks)}")

            cited_clusters = (
                OpinionCluster.objects.filter(id__in=chunk)
                .select_related("docket")
                .prefetch_related("citations")
            )

            for cited_cluster in cited_clusters:
                court = Docket.objects.get(id=cited_cluster.docket_id).court
                absolute_url = cited_cluster.get_absolute_url()
                cited_citations = cited_cluster.citations.all()
                cited_citation_names = [
                    f"{citation.volume} {citation.reporter} {citation.page}"
                    for citation in cited_citations
                ]

                cited_cluster_data[cited_cluster.id] = {
                    "cited_url": f"https://www.courtlistener.com{absolute_url}",
                    "cited_court_id": court.id,
                    "cited_court_name": court.full_name,
                    "cited_case_name_short": cited_cluster.case_name_short,
                    "cited_case_name": cited_cluster.case_name,
                    "cited_case_name_full": cited_cluster.case_name_full,
                    "cited_citations": cited_citation_names,
                }

    output_filename = f"cited_opinions.json"
    with open(output_filename, "w") as output_file:
        json.dump(cited_cluster_data, output_file)
