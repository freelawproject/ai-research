import logging
from typing import Any

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
AUTH_TOKEN = "<YOUR AUTHENTICATION TOKEN>"
HEADERS = {
    'Authorization': f'Token {AUTH_TOKEN}'
}


def make_request(
        url: str,
        max_retries: int = 5,
        initial_wait: int = 5
) -> dict[str, Any]:
    import requests
    import time
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


def search_case(case_name):
    return make_request(BASE_URL + f'/search/?q={case_name}')['results'][0]


def get_opinions_in_cluster(court_id, docket_id, cluster_id):
    return make_request(
        BASE_URL + '/opinions/'
        f'?cluster__docket__id={docket_id}'
        f'&cluster__docket__court={court_id}'
        f'&cluster__id={cluster_id}'
    )


def get_opinions_cited_by(
        citing_id,
        min_depth=3,
        max_opinions=10,
        min_opinions=4,
):
    answer = make_request(
        BASE_URL + f'/opinions-cited/?citing_opinion={citing_id}'
    )
    results = answer['results']
    while answer['next']:
        answer = make_request(
            answer['next']
        )
        results.extend(answer['results'])
    results.sort(key=lambda res: (-res['depth'], res['id']))
    answer = []
    for res in results:
        if (
            len(answer) < min_opinions
            or res['depth'] > min_depth and len(answer) < max_opinions
            or res['depth'] >= answer[-1]['depth']
        ):
            answer.append(res)
        else:
            break

    return answer


def opinion_case_name(opinion_id):
    opinion_obj = make_request(BASE_URL + f'/opinions/{opinion_id}')
    if not opinion_obj:
        return 'no opinion'
    cluster_obj = make_request(BASE_URL +
                               f"/clusters/{opinion_obj['cluster_id']}")
    if not cluster_obj:
        return 'no cluster'
    return (
        f"{cluster_obj['case_name']} "
        f"({cluster_obj['date_filed'].split('-')[0]})"
    )


def get_opinion(opinion_id):
    return make_request(BASE_URL + f'/opinions/{opinion_id}')
