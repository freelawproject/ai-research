import os
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.metrics import classification_report, multilabel_confusion_matrix, confusion_matrix
from sklearn.exceptions import UndefinedMetricWarning

from utils.instructions import instructions_v318

pd.options.mode.chained_assignment = None  # suppress SettingWithCopyWarning
warnings.filterwarnings("ignore", category=UndefinedMetricWarning)



# Define severity mapping
severity_mapping = {
    "Reversed by": "Stop",
    "Reversed and remanded by": "Stop",
    "Vacated and remanded by": "Stop",
    "Vacated by": "Stop",
    "Overruled by": "Stop",
    "Abrogated by": "Stop",
    "Questioned by": "Stop",
    "Affirmed in part; Reversed in part by": "Warning",
    "Disapproved by": "Warning",
    "Limited by": "Warning",
    "Remanded by": "Caution",
    "Cert. granted by": "Caution",
    "Criticized by": "Caution",
    "Distinguished by": "Caution",
    "Declined to follow by": "Caution",
    "Ambiguous: Stop": "Stop",
    "Ambiguous: Warning": "Warning",
    "Ambiguous: Caution": "Caution",
    "Dismissed by": "Neutral",
    "Affirmed by": "Neutral",
    "Cert. denied by": "Neutral",
    "Cited by": "Neutral",
    "Unknown": "Neutral"
}

# Define direction mapping
direction_mapping = {
    "Reversed by": "Direct History",
    "Reversed and remanded by": "Direct History",
    "Vacated and remanded by": "Direct History",
    "Vacated by": "Direct History",
    "Overruled by": "Citing Reference",
    "Abrogated by": "Citing Reference",
    "Questioned by": "Citing Reference",
    "Affirmed in part; Reversed in part by": "Direct History",
    "Disapproved by": "Citing Reference",
    "Limited by": "Citing Reference",
    "Remanded by": "Direct History",
    "Cert. granted by": "Direct History",
    "Criticized by": "Citing Reference",
    "Distinguished by": "Citing Reference",
    "Declined to follow by": "Citing Reference",
    "Ambiguous: Stop": "Ambiguous",
    "Ambiguous: Warning": "Ambiguous",
    "Ambiguous: Caution": "Ambiguous",
    "Dismissed by": "Direct History",
    "Affirmed by": "Direct History",
    "Cert. denied by": "Direct History",
    "Cited by": "Citing Reference",
    "Unknown": "Citing Reference"
}

def show_eval_metrics(y_true, y_pred, title="Evaluation Metrics"):
    """Show classification report, FP/FN/TP by class, and confusion matrix."""
    print(f"\n=== {title} ===")
    print(classification_report(y_true, y_pred))

    print(f"\n--- FP, FN, TP by class ({title}) ---")
    labels = sorted(set(y_true.unique()) | set(y_pred.unique()))
    mcm = multilabel_confusion_matrix(y_true, y_pred, labels=labels)
    metrics = []
    for i, class_label in enumerate(labels):
        tn, fp, fn, tp = mcm[i].ravel()
        metrics.append({"Class Label": class_label, "FP": fp, "FN": fn, "TP": tp})
    print(pd.DataFrame(metrics).to_string(index=False))

    print(f"\n--- Confusion Matrix ({title}) ---")
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    plt.figure(figsize=(10, 8))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", xticklabels=labels, yticklabels=labels)
    plt.xlabel("Predicted Label")
    plt.ylabel("Actual Label")
    plt.xticks(rotation=45, ha="right")
    plt.yticks(rotation=45, va="top")
    plt.title(title)
    plt.tight_layout()
    plt.show()


def sample_and_eval(df, sample_frac=0.01, random_state=42):
    # evaluation metrics on matched results with treatment
    print("\n=== Evaluation Metrics on Matched Results with Treatment ===")

    # Keep all records except for when both "predicted_treatment" and "label_treatment" are "Cited by"
    # For "Cited by" records, keep only 0.01% of them
    df.reset_index(drop=True, inplace=True)
    cited_by_mask = (df["predicted_treatment"] == "Cited by") & (df["label_treatment"] == "Cited by")
    non_cited_by_records = df[~cited_by_mask]
    sampled_cited_by_records = df[cited_by_mask].sample(frac=sample_frac, random_state=random_state)
    df = pd.concat([non_cited_by_records, sampled_cited_by_records], ignore_index=True)

    # 1) Evaluation metrics by severity classes
    show_eval_metrics(
        df["label_severity"],
        df["predicted_severity"],
        title="Overall Severity"
    )

    # 2) Evaluation metrics by direction classes
    show_eval_metrics(
        df["label_direction"],
        df["predicted_direction"],
        title="Overall Direction"
    )

    # 3) Evaluation metrics for each treatment type
    show_eval_metrics(
        df["label_treatment"],
        df["predicted_treatment"],
        title="Treatment Evaluation"
    )


def evaluate(dir, instructions_version="v318"):
    labels = pd.read_csv("data/revised_metadata_labels_317.csv")
    labels = labels[(labels["final_treatment"] != "PENDING") & (labels["final_treatment"] != "REMOVE") & (labels["final_treatment"] != "MANUAL")]

    print(f"Instruction Length (char) for {instructions_version}: ", len(instructions_v318))

    output_dir = f"data/output/{instructions_version}/{dir}"
    eval_dir = f"data/eval/{instructions_version}/{dir}"
    os.makedirs(eval_dir, exist_ok=True)

    print("\n=== Overall Statistics ===")
    raw_df = pd.read_csv(f"{output_dir}/raw_results.csv")
    print("Total number of citing cases: ", len(raw_df))
    print("Total number of input tokens: ", raw_df["input_tokens"].sum())
    print("Total number of output tokens: ", raw_df["output_tokens"].sum())
    if "latency_ms" in raw_df.columns:
        print("Total latency (ms): ", raw_df["latency_ms"].sum())

    print("\n=== Evaluation Statistics ===")
    parsed_df = pd.read_csv(f"{output_dir}/parsed_results.csv")
    ## Remove any duplicates in parsed_df based on citing_cluster_id and mainCitationString, keep the first occurrence
    parsed_df = parsed_df.drop_duplicates(subset=["citing_cluster_id", "mainCitationString"], keep="first")

    labels = labels[labels["citing_cluster_id"].isin(parsed_df["citing_cluster_id"])]

    merged = parsed_df.merge(labels, on='citing_cluster_id', how='left', suffixes=('_pred', '_label'))

    mask = merged.apply(
        lambda r: pd.notna(r['mainCitationString'])
                and pd.notna(r['cited_citation_strings'])
                and r['mainCitationString'] in r['cited_citation_strings'],
        axis=1
    )
    matched = merged[mask]

    # results in both model & complete authorities
    matched_results = matched[['citing_cluster_id', 'mainCitationString', 'caseName', 'caseHistory', 'treatment', 'opinionType', 'quote', 'rationale',
        'cited_cluster_id', 'cited_citation_strings', 'cited_case_name', 'final_treatment', 'severity', 'direction', "court", "case_type"]]
    print("Number of results in both model & complete authorities: ", len(matched_results))
    matched_results.to_csv(f"{eval_dir}/matched_results.csv", index=False)

    # results in both model & complete authorities with expert annotated treatment
    matched_with_treatment = matched_results[~matched_results["final_treatment"].isna()]
    matched_with_treatment = matched_with_treatment[~matched_with_treatment["treatment"].isna()]

    print("Number of results in both model & complete authorities with expert annotated treatment: ", len(matched_with_treatment))
    matched_with_treatment.rename(columns={"treatment": "predicted_treatment",
                                           "final_treatment": "label_treatment",
                                           "severity": "label_severity",
                                           "direction": "label_direction"}, inplace=True)

    # Replace all "Cited as recognized by" in "predicted_treatment" column with "Cited by"
    matched_with_treatment["predicted_treatment"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: "Cited by" if isinstance(x, str) and x.endswith("Cited as recognized by") else x
    )

    # Add metadata severity to the matched results
    matched_with_treatment["predicted_severity"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: severity_mapping.get(x.replace(" as recognized", ""), None)
        if x.endswith("as recognized by")
        else severity_mapping.get(x, None)
    )
    # Add metadata direction to the matched results
    matched_with_treatment["predicted_direction"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: "Related Reference" if x.endswith("as recognized by") else direction_mapping.get(x, None)
    )
    matched_with_treatment = matched_with_treatment[["citing_cluster_id", "mainCitationString", "cited_cluster_id", "cited_case_name", "court", "case_type",
                                                     "label_treatment", "label_severity", "label_direction",
                                                     "predicted_treatment", "predicted_severity", "predicted_direction",
                                                     "quote", "rationale"]]
    matched_with_treatment.to_csv(f"{eval_dir}/matched_with_treatment.csv", index=False)

    # results from model not in complete authorities (per citing_cluster_id)
    parsed_keys = parsed_df[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    matched_keys = matched[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    cl_missed_keys = parsed_keys.merge(matched_keys, on=["citing_cluster_id", "mainCitationString"], how="left", indicator=True)
    cl_missed_keys = cl_missed_keys[cl_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    cl_missed = parsed_df.merge(cl_missed_keys, on=["citing_cluster_id", "mainCitationString"], how="inner")
    print("Number of results from model not in complete authorities: ", len(cl_missed))
    cl_missed.to_csv(f"{eval_dir}/cl_missed.csv", index=False)

    # results the model missed that are in the complete authorities (per citing_cluster_id)
    label_keys = labels[["citing_cluster_id", "cited_cluster_id"]].drop_duplicates()
    matched_label_keys = matched[["citing_cluster_id", "cited_cluster_id"]].drop_duplicates()
    model_missed_keys = label_keys.merge(matched_label_keys, on=["citing_cluster_id", "cited_cluster_id"], how="left", indicator=True)
    model_missed_keys = model_missed_keys[model_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    model_missed = labels.merge(model_missed_keys, on=["citing_cluster_id", "cited_cluster_id"], how="inner")
    print("Number of results the model missed that are in the complete authorities: ", len(model_missed))
    model_missed.to_csv(f"{eval_dir}/model_missed.csv", index=False)

    sample_and_eval(matched_with_treatment)

