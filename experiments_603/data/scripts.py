import os
import json
from asgiref.sync import async_to_sync

from cl.search.models import Opinion, OpinionCluster

def process_citing_opinions():
  """
    Processes a list of citing opinion clusters by retrieving their metadata, associated citations,
    and full-text opinions from the database, and writes the raw opinion text to local files.

    The function performs the following steps:
    - Reads cluster IDs from the file `cluster_ids.txt`.
    - For each cluster ID:
        - Retrieves the corresponding `OpinionCluster` object and its metadata.
        - Constructs the full case URL and citation names.
        - Fetches related `Opinion` objects using `with_best_text()`.
        - Extracts metadata and best text from each opinion.
        - Writes the best text of each opinion to a `.txt` file in the `raw_citing_opinions/` directory.
        - Aggregates all relevant data into a dictionary structure.
    - Writes a summary of all processed data to `citing_opinions.json`.

    Output:
        - A directory of text files containing raw opinion text (one per opinion).
        - A single JSON file (`citing_opinions.json`) summarizing metadata for each citing opinion cluster.
    """
  all_results = {}
  filepath = "raw_citing_opinions/"
  
  with open("cluster_ids.txt", 'r') as f:
    for line in f:
      cluster_id = int(line.strip())

      cluster = OpinionCluster.objects.filter(id = cluster_id)[0]

      absolute_url = cluster.get_absolute_url()
      case_law_url = f"https://www.courtlistener.com{absolute_url}"

      case_name_short = cluster.case_name_short
      case_name = cluster.case_name
      case_name_full = cluster.case_name_full
      citations = cluster.citations.all()
      citation_names = [
              f'{citation.volume} {citation.reporter} {citation.page}'
              for citation in citations
          ]

      opinion_data = []

      opinions = Opinion.objects.filter(cluster_id = cluster_id).with_best_text()
      for opinion in opinions:
        opinion_metadata = {
                  "opinion_id": None,
                  "opinion_api": None,
                  "opinion_type": None,
                  "opinion_source": None,
                  "opinion_filename": None,
              }
        opinion_id = opinion.id
        opinion_api = f"https://www.courtlistener.com/api/rest/v4/opinions/{opinion_id}/"
        opinion_type = opinion.type
        opinion_filename = f"{cluster_id}_{opinion_type}.txt"
        destination = os.path.join(filepath, opinion_filename)

        opinion_source = opinion.best_text_source
        raw_opinion_text = opinion.best_text
        
        opinion_metadata["opinion_id"] = opinion_id
        opinion_metadata["opinion_api"] = opinion_api
        opinion_metadata["opinion_type"] = opinion_type
        opinion_metadata["opinion_filename"] = opinion_filename
        opinion_metadata["opinion_source"] = opinion_source

        opinion_data.append(opinion_metadata)

        with open(destination, "w") as file:
          file.write(raw_opinion_text)
        
      results = {
          "case_law_url": case_law_url,
          "case_name_short": case_name_short,
          "case_name": case_name,
          "case_name_full": case_name_full,
          "citation_names": citation_names,
          "opinion_data": opinion_data,
      }

      all_results[cluster_id] = results
      print(f"Processed cluster {cluster_id}")

    # Save results to a JSON file
    output_filename = f"citing_opinions.json"
    with open(output_filename, "w") as output_file:
      json.dump(all_results, output_file)


def get_cited_opinions():
  """
    Processes a list of citing opinion clusters to retrieve the opinions they cite.

    The function performs the following steps:
    - For each citing cluster from the file `citing_joined_cluster_ids.txt`:
        - Loads the corresponding `OpinionCluster` from the database.
        - Uses the asynchronous method `aauthorities_with_data()`
          to retrieve cited clusters (i.e., authorities).
        - For each cited cluster:
            - If not already processed, extracts metadata including case name variants,
              citation strings, and URL.
            - Records the `citation_depth` indicating how many times the cited cluster was cited.
        - Associates the citing cluster ID with a dictionary mapping cited cluster IDs to
          their citation depth.
    - Writes two output JSON files:
        - `cited_opinions.json`: mapping of citing cluster IDs to cited cluster IDs with citation depth.
        - `cited_clusters_metadata.json`: metadata for all unique cited clusters.

    Output:
        - A JSON file containing citation depth mappings (`cited_opinions.json`).
        - A JSON file containing metadata for each cited opinion cluster (`cited_clusters_metadata.json`).
    """
  results = {}
  cited_cluster_data = {}
  count = 0

  with open("cluster_ids.txt", 'r') as f:
    for line in f:
      citing_cluster_id = int(line.strip())
      citing_cluster = OpinionCluster.objects.filter(id = citing_cluster_id)[0]
      
      cited_cluster_tracker = {}
      authorities = async_to_sync(citing_cluster.aauthorities_with_data)()
      for cited_cluster in authorities:
        cited_cluster_id = cited_cluster.id
        
        if cited_cluster_id not in cited_cluster_data.keys():
          absolute_url = cited_cluster.get_absolute_url()
          cited_citations = cited_cluster.citations.all()
          cited_citation_names = [
              f'{citation.volume} {citation.reporter} {citation.page}'
              for citation in cited_citations
          ]

          cited_cluster_data[cited_cluster_id] = {
                                "cited_url": f"https://www.courtlistener.com{absolute_url}",
                                "cited_case_name_short": cited_cluster.case_name_short,
                                "cited_case_name": cited_cluster.case_name,
                                "cited_case_name_full": cited_cluster.case_name_full,
                                "cited_citations": cited_citation_names
                                }
        
        cited_cluster_tracker[cited_cluster_id] = cited_cluster.citation_depth

      results[citing_cluster_id] = cited_cluster_tracker

      count += 1
      print(f"Processed {count} citing clusters.")

  # Save results to a JSON file
  output_filename = f"cited_opinions.json"
  with open(output_filename, "w") as output_file:
    json.dump(results, output_file)
  
  output_filename = f"cited_clusters_metadata.json"
  with open(output_filename, "w") as output_file:
    json.dump(cited_cluster_data, output_file)