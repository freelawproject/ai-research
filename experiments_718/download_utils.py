import json
import time
from json.decoder import JSONDecodeError
from pathlib import Path

import requests


def download_scotus_docket(base_path, docket_number, year):
    # Determine the order of fetch attempts based on the year
    attempts = []

    if year < 2016:
        attempts = ["json", "htm"]
    elif 2016 <= year < 2018:
        attempts = ["json", "html", "htm"]
    else:  # year >= 2018
        attempts = ["json", "html"]

    # 1. Try JSON
    if "json" in attempts:
        json_url = f"https://www.supremecourt.gov/RSS/Cases/JSON/{docket_number}.json"
        try:
            response = requests.get(json_url)
            json_data = response.json()
            filepath = base_path / f"{docket_number}.json"
            filepath.write_text(json.dumps(json_data))
            return docket_number
        except (JSONDecodeError, ValueError):
            pass  # move on to the next type

    # 2. Try HTML
    if "html" in attempts:
        html_url = f"https://www.supremecourt.gov/search.aspx?filename=/docket/DocketFiles/html/Public/{docket_number}.html"
        response = requests.get(html_url)
        content = response.text
        if f"Docket for {docket_number}" in content:
            filepath = base_path / f"{docket_number}.html"
            filepath.write_text(content)
            return docket_number

    # 3. Try HTM
    if "htm" in attempts:
        htm_url = f"https://www.supremecourt.gov/search.aspx?filename=/docketfiles/{docket_number}.htm"
        response = requests.get(htm_url)
        content = response.text
        if f"Docket for {docket_number}" in content:
            filepath = base_path / f"{docket_number}.htm"
            filepath.write_text(content)
            return docket_number

    return None


def get_yearly_scotus_data(year, lower_range=1, upper_range=10000, sleep_time=0.5):
    yr = str(year)[2:4]
    base_path = Path(f"scotus_dockets/scraped/{yr}")
    base_path.mkdir(parents=True, exist_ok=True)

    valid_docket_numbers = []
    for i in range(lower_range, upper_range):
        if i % 1000 == 0:
            print(f"Processing docket number {i}...")

        time.sleep(sleep_time)
        docket_number = f"{yr}-{i}"
        result = download_scotus_docket(base_path, docket_number, year)

        if result:
            valid_docket_numbers.append(result)

    return {year: len(valid_docket_numbers)}
