import os
import re
import logging
import requests
from pypdf import PdfReader


def extract_text_from_pdf(path):
    reader = PdfReader(path)
    return [page.extract_text() for page in reader.pages]


def download_pdf(url, save_dir='QPReport'):
    filename = url.split('/')[-1]
    local_path = os.path.join(save_dir, filename)

    if not os.path.exists(local_path):
        response = requests.get(url)
        if "pdf" in response.headers.get('Content-Type', ''):
            with open(local_path, 'wb') as f:
                f.write(response.content)
            logging.info(f"Saved to {local_path}")
    return local_path


def construct_url(docket_num):
    if "-" in docket_num:
        parts = docket_num.split("-")
        year, number = parts
        padded_number = number.zfill(5)  # pad to 5 digits
        return f"https://www.supremecourt.gov/qp/{year}-{padded_number}qp.pdf"
    else:
        return f"https://www.supremecourt.gov/qp/{docket_num}qp.pdf"


def extract_case_metadata(docket_num):
    results = {
            "docket_number": docket_num,
            "metadata_docket_number": None,
            "metadata_case_name": None,
            "lower_court_citation": None,
            "lower_court_docket_number": None
        }
    
    if "Orig" in docket_num:
        return results
    else:
        url = construct_url(docket_num)

    local_path = download_pdf(url)
    if not os.path.exists(local_path):
        return results
    
    pages = extract_text_from_pdf(local_path)
    text = "\n".join(pages)
    lines = text.splitlines()

    # -- docket number and case name
    docket_match = re.match(r"(\d{2}[-A-Za-z]?\d{1,4})\s+(.*)", lines[0].strip())
    docket_number = docket_match.group(1) if docket_match else None
    case_name = docket_match.group(2) if docket_match else None

    # -- decision below
    decision_match = re.search(r"DECISION BELOW:\s+(.+)", text, re.IGNORECASE)
    decision_below = decision_match.group(1).strip() if decision_match else None

    # -- lower court case number
    lower_case_match = re.search(r"LOWER COURT CASE NUMBER:\s+([\w\-./]+)", text, re.IGNORECASE)
    lower_court_case_number = lower_case_match.group(1).strip() if lower_case_match else None

    results["metadata_docket_number"] = docket_number
    results["metadata_case_name"] = case_name
    results["lower_court_citation"] = decision_below
    results["lower_court_docket_number"] = lower_court_case_number

    return results