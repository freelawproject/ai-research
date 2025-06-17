import logging
from typing import Any
import requests
import time

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
AUTH_TOKEN = "<YOUR API TOKEN>"
HEADERS = {
    'Authorization': f'Token {AUTH_TOKEN}'
}


def make_request(
        url: str,
        max_retries: int = 5,
        initial_wait: int = 5
) -> dict[str, Any]:
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=HEADERS)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = initial_wait * (2 ** attempt)
                logging.warning(f"Rate limit hit. Waiting for {wait_time} "
                                "seconds before retrying...")
                time.sleep(wait_time)
            elif response.status_code == 502:
                wait_time = initial_wait * (2 ** attempt)
                logging.warning(f"Error 502. Waiting for {wait_time} "
                                "seconds before retrying...")
                time.sleep(wait_time)
            else:
                logging.error(f"Error: Status code {response.status_code} "
                              f"for URL: {url}")
                return None
        except requests.RequestException as e:
            logging.error(f"Request failed: {e}")
            return None

    logging.error(f"Max retries reached for URL: {url}")
    return None


def get_opinions_in_cluster(court_id, docket_id, cluster_id):
    return make_request(
        BASE_URL + '/opinions/'
        f'?cluster__docket__id={docket_id}'
        f'&cluster__docket__court={court_id}'
        f'&cluster__id={cluster_id}'
    )


def get_case_details(opinion_id):
    opinion_obj = make_request(BASE_URL + f'/opinions/{opinion_id}')
    cluster_id = opinion_obj.get("cluster_id")
    cluster_obj = make_request(BASE_URL + f'/clusters/{cluster_id}')

    case_name_short = cluster_obj.get("case_name_short")
    case_name = cluster_obj.get("case_name")
    case_name_full = cluster_obj.get("case_name_full")
    citations = cluster_obj.get("citations")
    citation_names = [f'{citation.get("volume")} {citation.get("reporter")} {citation.get("page")}' for citation in citations]

    return case_name_short, case_name, case_name_full, cluster_id, citation_names

    