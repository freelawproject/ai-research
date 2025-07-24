import os
import pandas as pd

from granted_utils import parse_granted_list_pdf
from report_utils import extract_case_metadata

def cases_to_df(cases):
    flat_cases = []
    for case in cases:
        flat_case = {
            "docket_number": case.get("docket_number"),
            "type": case.get("type"),
            "case_name": case.get("case_name"),
            "court_ids": "; ".join(case.get("court_ids", [])),
            "granted": case.get("dates", {}).get("granted_date"),
            "argument_date": case.get("dates", {}).get("argument_date"),
            "decided_date": case.get("dates", {}).get("decided_date"),
            "result": case.get("result")
        }
        flat_cases.append(flat_case)
    return pd.DataFrame(flat_cases)


def produce_granted_table(folder_path="granted/"):
    all_dfs = []
    filenames = sorted([f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))])
    for filename in filenames:
        term = f"Oct-{filename[:2]}"
        term_cases = parse_granted_list_pdf(f"{folder_path}{filename}")
        df = cases_to_df(term_cases)
        df["term"] = term
        all_dfs.append(df)

    return pd.concat(all_dfs, ignore_index=True)


def produce_final_table(folder_path="granted/"):
    granted_df = produce_granted_table(folder_path)

    terms_with_reports = ["Oct-18", "Oct-19", "Oct-20", "Oct-21", "Oct-22", "Oct-23", "Oct-24", "Oct-25"]
    filtered_df = granted_df[granted_df["term"].isin(terms_with_reports)]

    metadata_list = []
    for docket_num in filtered_df["docket_number"].unique():
        metadata = extract_case_metadata(docket_num)
        metadata_list.append(metadata)

    metadata_df = pd.DataFrame(metadata_list)
    final_df = pd.merge(granted_df, metadata_df, on="docket_number", how="left")

    return final_df