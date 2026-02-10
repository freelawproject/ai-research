import json

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.metrics import classification_report, confusion_matrix

from utils.claude_label_utils import predict
from utils.instructions import instructions_v819

MAX_RETRIES = 2
MODEL_ID = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
SYSTEM_PROMPT = instructions_v819
FILE_PATH = "data/raw_citing_opinions"


def load_metadata(metadata_path="data/citing_opinions.json"):
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
    
    citing_metadata = pd.DataFrame.from_dict(metadata, orient='index')
    citing_metadata = citing_metadata.reset_index().rename(columns={'index': 'citing_cluster_id'})
    
    citing_metadata["citing_filenames"] = citing_metadata["opinion_data"].apply(
    lambda lst: [d["opinion_filename"] for d in lst if "opinion_filename" in d]
)

    citing_metadata = citing_metadata[["citing_cluster_id", "citing_filenames"]]
    citing_metadata["citing_cluster_id"] = citing_metadata["citing_cluster_id"].astype(int)
    citing_metadata["citing_filenames"] = citing_metadata["citing_filenames"].astype(str)

    return citing_metadata


def prep_dataframe(csv_path="../experiments_813/0.annotated_round1.csv"):
    df = pd.read_csv(csv_path)
    df["cited_cluster_id"] = df["cited_cluster_id"].fillna(9900990099) # there are some records missing cited_cluster_ids as they are the lower court cases that we don't have the opinions for
    df["citing_cluster_id"] = df["citing_cluster_id"].astype(int)
    df["cited_cluster_id"] = df["cited_cluster_id"].astype(int)

    print("number of citing opinions: ", df["citing_cluster_id"].nunique())

    citing_metadata = load_metadata()
    df = pd.merge(df, citing_metadata, on="citing_cluster_id", how="left")

    return df


def prep_pred_data(dataframe):
    citing_dict = dataframe[['citing_cluster_id', 'citing_filenames']].drop_duplicates().set_index('citing_cluster_id')[['citing_filenames']].to_dict(orient='index')

    cited_dict = {}

    citations_df = dataframe[['citing_cluster_id', 'cited_cluster_id', 'cited_case_name_short', 'cited_case_name', 'cited_citations']]

    for _, row in citations_df.iterrows():
        citing = row['citing_cluster_id']
        
        entry = {
            "uniqueID": row['cited_cluster_id'],
            "caseName": row['cited_case_name'] if pd.notna(row['cited_case_name']) else "",
            "caseShortName": row['cited_case_name_short'] if pd.notna(row['cited_case_name_short']) else "",
            "caseCitations": row['cited_citations']
        }
        
        if citing not in cited_dict:
            cited_dict[citing] = []
        
        cited_dict[citing].append(entry)
    
    return citing_dict, cited_dict


def make_predictions(raw_output_path="predictions/claude_raw_results.csv", 
                     parsed_output_path="predictions/claude_parsed_results_df.csv",
                     final_output_path="predictions/claude_prediction_df.csv",
                     max_retries=MAX_RETRIES):
    input_tokens = 0
    output_tokens = 0

    # Prep initial data
    df = prep_dataframe()
    citing_dict, cited_dict = prep_pred_data(df)
    
    # First prediction
    raw_results_df, parsed_results_df = predict(
        citing_dict, cited_dict, model_id=MODEL_ID, 
        system_prompt=SYSTEM_PROMPT, data_folder=FILE_PATH
    )

    raw_results_df.to_csv(raw_output_path, index=False)
    parsed_results_df.to_csv(parsed_output_path, index=False)

    parsed_results_df["citing_cluster_id"] = parsed_results_df["citing_cluster_id"].replace('', 0).astype(int)
    parsed_results_df["cited_cluster_id"] = parsed_results_df["cited_cluster_id"].replace('', 0).astype(int)

    input_tokens += raw_results_df["input_tokens"].replace('', 0).astype(int).sum()
    output_tokens += raw_results_df["output_tokens"].replace('', 0).astype(int).sum()

    # Initialize accumulators
    final_raw_results_df = raw_results_df.copy()
    final_parsed_results_df = parsed_results_df.copy()

    # Merge predictions with original dataframe
    prediction_df = pd.merge(df, final_parsed_results_df, 
                             on=["citing_cluster_id", "cited_cluster_id"], 
                             how="left")

    # Retry loop for missed predictions
    retries = 0
    while retries < max_retries:
        missed_df = prediction_df[prediction_df["targetTreatment"].isnull()]
        if missed_df.empty:
            break  # All predictions filled, stop retrying
        
        print(f"Retry {retries+1} on {missed_df["citing_cluster_id"].nunique()} opinions.")
        citing_dict, cited_dict = prep_pred_data(missed_df)
        rerun_raw_results_df, rerun_parsed_results_df = predict(
            citing_dict, cited_dict, model_id=MODEL_ID, 
            system_prompt=SYSTEM_PROMPT, data_folder=FILE_PATH
        )

        raw_results_df.to_csv("predictions/claude_raw_results_rerun.csv", index=False)
        parsed_results_df.to_csv("predictions/claude_rerun_results_rerun.csv", index=False)

        input_tokens += rerun_raw_results_df["input_tokens"].replace('', 0).astype(int).sum()
        output_tokens += rerun_raw_results_df["output_tokens"].replace('', 0).astype(int).sum()

        # Append rerun results
        final_raw_results_df = pd.concat([final_raw_results_df, rerun_raw_results_df], ignore_index=True)
        final_parsed_results_df = pd.concat([final_parsed_results_df, rerun_parsed_results_df], ignore_index=True)

        # Ensure int type
        final_parsed_results_df["citing_cluster_id"] = final_parsed_results_df["citing_cluster_id"].replace('', 0).astype(int)
        final_parsed_results_df["cited_cluster_id"] = final_parsed_results_df["cited_cluster_id"].replace('', 0).astype(int)

        # Re-merge to update prediction_df
        prediction_df = pd.merge(df, final_parsed_results_df, 
                                 on=["citing_cluster_id", "cited_cluster_id"], 
                                 how="left")

        retries += 1

    # Save final outputs
    final_raw_results_df.to_csv(raw_output_path, index=False)
    final_parsed_results_df.to_csv(parsed_output_path, index=False)
    prediction_df.to_csv(final_output_path, index=False)

    print(f"Finished with {retries} retries. Total input tokens: {input_tokens}, output tokens: {output_tokens}")

    return final_raw_results_df, final_parsed_results_df, prediction_df


# --- grouping dictionaries ---
CASE_HISTORY_GROUPS = {
    "Direct History": {
        "Reversed by",
        "Reversed and remanded by",
        "Vacated by",
        "Vacated and remanded by",
        "Affirmed in part; Reversed in part by",
        "Remanded by",
        "Dismissed by",
        "Affirmed by",
    },
    "Citing Reference": {
        "Overruled by",
        "Abrogated by",
        "Questioned by",
        "Disapproved by",
        "Limited by",
        "Criticized by",
        "Distinguished by",
        "Declined to follow by",
        "Cited by",
    },
    "Related Reference": {
        "Reversed as recognized by",
        "Reversed and remanded as recognized by",
        "Vacated as recognized by",
        "Vacated and remanded as recognized by",
        "Affirmed in part; Reversed in part as recognized by",
        "Remanded as recognized by",
        "Dismissed as recognized by",
        "Affirmed as recognized by",
        "Overruled as recognized by",
        "Abrogated as recognized by",
        "Questioned as recognized by",
        "Disapproved as recognized by",
        "Limited as recognized by",
        "Criticized as recognized by",
        "Distinguished as recognized by",
        "Declined to follow as recognized by",
        "Cited as recognized by",
    },
}

SEVERITY_GROUPS = {
    "Stop": {
        "Reversed by",
        "Reversed and remanded by",
        "Vacated by",
        "Vacated and remanded by",
        "Overruled by",
        "Abrogated by",
        "Questioned by",
        "Reversed as recognized by",
        "Reversed and remanded as recognized by",
        "Vacated as recognized by",
        "Vacated and remanded as recognized by",
        "Overruled as recognized by",
        "Abrogated as recognized by",
        "Questioned as recognized by",
    },
    "Warning": {
        "Affirmed in part; Reversed in part by",
        "Disapproved by",
        "Limited by",
        "Affirmed in part; Reversed in part as recognized by",
        "Disapproved as recognized by",
        "Limited as recognized by",
    },
    "Caution": {
        "Remanded by",
        "Criticized by",
        "Distinguished by",
        "Declined to follow by",
        "Remanded as recognized by",
        "Criticized as recognized by",
        "Distinguished as recognized by",
        "Declined to follow as recognized by",
    },
    "Neutral": {
        "Dismissed by",
        "Affirmed by",
        "Cited by",
        "Dismissed as recognized by",
        "Affirmed as recognized by",
        "Cited as recognized by",
    },
}


def map_group(label, group_dict):
    """Map fine-grained label to group name using group_dict."""
    for group, values in group_dict.items():
        if label in values:
            return group
    return "Other"


def run_eval(eval_df):
    # Drop failed predictions
    print("num failed predictions: ", eval_df["targetTreatment"].isnull().sum())
    eval_df = eval_df[~eval_df["targetTreatment"].isnull()]

    # --- Fine-grained baseline eval ---
    print("\n=== Fine-Grained Label Evaluation ===")
    y_true = eval_df["expert_label"]
    y_pred = eval_df["targetTreatment"]

    labels = sorted(list(set(y_true) | set(y_pred)))
    print(classification_report(y_true, y_pred))
    cm = confusion_matrix(y_true, y_pred, labels=labels)

    plt.figure(figsize=(12, 12))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=labels, yticklabels=labels)
    plt.xlabel("Predicted Label")
    plt.ylabel("Actual Label")
    plt.title("Confusion Matrix (Fine-grained)")
    plt.show()

    # --- Case History grouped eval ---
    print("\n=== Grouped Evaluation: Case History ===")
    y_true_hist = eval_df["expert_label"].map(lambda x: map_group(x, CASE_HISTORY_GROUPS))
    y_pred_hist = eval_df["targetTreatment"].map(lambda x: map_group(x, CASE_HISTORY_GROUPS))

    labels_hist = sorted(list(set(y_true_hist) | set(y_pred_hist)))
    print(classification_report(y_true_hist, y_pred_hist))
    cm_hist = confusion_matrix(y_true_hist, y_pred_hist, labels=labels_hist)

    plt.figure(figsize=(8, 6))
    sns.heatmap(cm_hist, annot=True, fmt="d", cmap="Blues",
                xticklabels=labels_hist, yticklabels=labels_hist)
    plt.xlabel("Predicted Group")
    plt.ylabel("Actual Group")
    plt.title("Confusion Matrix (Case History Groups)")
    plt.show()

    # --- Severity grouped eval ---
    print("\n=== Grouped Evaluation: Severity ===")
    y_true_sev = eval_df["expert_label"].map(lambda x: map_group(x, SEVERITY_GROUPS))
    y_pred_sev = eval_df["targetTreatment"].map(lambda x: map_group(x, SEVERITY_GROUPS))

    labels_sev = sorted(list(set(y_true_sev) | set(y_pred_sev)))
    print(classification_report(y_true_sev, y_pred_sev))
    cm_sev = confusion_matrix(y_true_sev, y_pred_sev, labels=labels_sev)

    plt.figure(figsize=(8, 6))
    sns.heatmap(cm_sev, annot=True, fmt="d", cmap="Blues",
                xticklabels=labels_sev, yticklabels=labels_sev)
    plt.xlabel("Predicted Group")
    plt.ylabel("Actual Group")
    plt.title("Confusion Matrix (Severity Groups)")
    plt.show()