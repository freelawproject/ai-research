import logging
import os
import time
from typing import Any

import requests

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
AUTH_TOKEN = "<YOUR_AUTH_TOKEN>"
HEADERS = {"Authorization": f"Token {AUTH_TOKEN}"}

HTMLS = [
    "html_with_citations",
    "html_columbia",
    "html_lawbox",
    "xml_harvard",
    "html_anon_2020",
    "html",
]
SOURCES = HTMLS + ["plain_text"]


### Code block credit to Pau Arnal from cicerai pau@cicerai.com
def make_request(
    url: str, max_retries: int = 5, initial_wait: int = 5
) -> dict[str, Any]:

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=HEADERS)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = initial_wait * (2**attempt)
                logging.warning(
                    f"Rate limit hit. Waiting for {wait_time} "
                    "seconds before retrying..."
                )
                time.sleep(wait_time)
            elif response.status_code == 502:
                wait_time = initial_wait * (2**attempt)
                logging.warning(
                    f"Error 502. Waiting for {wait_time} " "seconds before retrying..."
                )
                time.sleep(wait_time)
            else:
                logging.error(
                    f"Error: Status code {response.status_code} " f"for URL: {url}"
                )
                return None
        except requests.RequestException as e:
            logging.error(f"Request failed: {e}")
            return None

    logging.error(f"Max retries reached for URL: {url}")
    return None


def get_opinions_in_cluster(cluster_id, filepath):
    cluster_obj = make_request(BASE_URL + f"/clusters/{cluster_id}")

    absolute_url = cluster_obj.get("absolute_url")
    case_law_url = f"https://www.courtlistener.com{absolute_url}"

    case_name_short = cluster_obj.get("case_name_short")
    case_name = cluster_obj.get("case_name")
    case_name_full = cluster_obj.get("case_name_full")

    citations = cluster_obj.get("citations")
    citation_names = [
        f'{citation.get("volume")} {citation.get("reporter")} {citation.get("page")}'
        for citation in citations
    ]

    opinions = []

    opinion_urls = cluster_obj.get("sub_opinions")
    for opinion_url in opinion_urls:

        opinion_metadata = {
            "opinion_id": None,
            "opinion_api": None,
            "opinion_type": None,
            "opinion_source": None,
            "opinion_filename": None,
        }

        opinion_obj = make_request(opinion_url)

        opinion_id = opinion_obj.get("id")
        opinion_type = opinion_obj.get("type")

        filename = f"{cluster_id}_{opinion_type}.txt"
        destination = os.path.join(filepath, filename)

        opinion_metadata["opinion_id"] = opinion_id
        opinion_metadata["opinion_api"] = opinion_url
        opinion_metadata["opinion_type"] = opinion_type
        opinion_metadata["opinion_filename"] = filename

        for source in SOURCES:
            raw_opinion = opinion_obj.get(source)
            if raw_opinion:
                with open(destination, "w") as f:
                    f.write(raw_opinion)
                opinion_metadata["opinion_source"] = source
                break

        opinions.append(opinion_metadata)

    results = {
        "case_law_url": case_law_url,
        "case_name_short": case_name_short,
        "case_name": case_name,
        "case_name_full": case_name_full,
        "citation_names": citation_names,
        "opinions": opinions,
    }

    return results
