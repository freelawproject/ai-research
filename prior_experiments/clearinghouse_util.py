import pandas as pd
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

CLEARINGHOUSE = "<YOUR CLEARINGHOUSE TOKEN>"
MAX_WORKERS = 4
MAX_RETRIES = 3

def fetch_case_data(case_id, token, timeout=10, retry_delay=2):
    url = f"https://clearinghouse.net/api/v1/case?case_id={case_id}"
    headers = {
        'Authorization': f'Token {token}',
        'User-Agent': 'Chrome v22.2 Linux Ubuntu'
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            print(f"[Attempt {attempt}] Status: {response.status_code}")
            response.raise_for_status()  # raise error on bad status
            return json.loads(response.text)[0]
        except Exception as e:
            print(f"[Attempt {attempt}] Error: {e}")
            if attempt < MAX_RETRIES:
                delay = retry_delay ** (attempt + 1)
                print(f"Retrying in {delay} seconds")
                time.sleep(delay)
            else:
                return {'error': str(e), 'case_id': case_id}
                
def fetch_all_case_data(df, token=CLEARINGHOUSE, max_workers=MAX_WORKERS):
    results = [None] * len(df)

    def task(i, case_id):
        if i % 10 == 0:
            print(f"Fetching case {i}...")
        result = fetch_case_data(case_id, token)
        results[i] = result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(task, i, row['case_id'])
            for i, row in df.iterrows()
        ]
        for _ in as_completed(futures):
            pass  # just waiting for all to finish

    return results
