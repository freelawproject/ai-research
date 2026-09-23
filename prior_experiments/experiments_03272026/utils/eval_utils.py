import os
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.metrics import classification_report, multilabel_confusion_matrix, confusion_matrix
from sklearn.exceptions import UndefinedMetricWarning

from utils.instructions import instructions_v331

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



def evaluate_binary(model_dir="claude-sonnet-4-6", txt_name="example"):
    """Evaluate binary flagging predictions against revised_labels_317_examples_binary.csv."""
    labels = pd.read_csv("data/revised_labels_317_examples_binary.csv")

    output_dir = f"data/output/{model_dir}/{txt_name}"
    eval_dir = f"data/eval/{model_dir}/{txt_name}"
    os.makedirs(eval_dir, exist_ok=True)

    # Overall stats
    print("\n=== Overall Statistics ===")
    raw_df = pd.read_csv(f"{output_dir}/raw_results.csv")
    print("Total number of citing cases: ", len(raw_df))
    print("Total input tokens: ", raw_df["input_tokens"].sum())
    print("Total output tokens: ", raw_df["output_tokens"].sum())
    #print("Total latency (ms): ", raw_df["latency_ms"].sum())

    # Load and deduplicate predictions
    parsed_df = pd.read_csv(f"{output_dir}/parsed_results.csv")
    parsed_df = parsed_df.drop_duplicates(subset=["citing_cluster_id", "mainCitationString"], keep="first")

    # Scope labels to citing cases present in predictions
    labels = labels[labels["citing_id"].isin(parsed_df["citing_cluster_id"])]

    # Merge predictions with labels on citing_cluster_id
    merged = parsed_df.merge(labels, left_on="citing_cluster_id", right_on="citing_id", how="left", suffixes=("_pred", "_label"))

    # Match: mainCitationString must appear in cited_citation_strings
    mask = merged.apply(
        lambda r: pd.notna(r["mainCitationString"])
        and pd.notna(r["cited_citation_strings"])
        and r["mainCitationString"] in r["cited_citation_strings"],
        axis=1,
    )
    matched = merged[mask]

    # Build matched results
    matched_results = matched[[
        "citing_cluster_id", "mainCitationString", "caseName", "actingCase",
        "direction_pred", "isFlagged", "quote", "rationale",
        "cited_id", "cited_citation_strings",
        "final_treatment", "severity", "direction_label", "binary_treatment",
    ]].copy()
    print("\n=== Evaluation Statistics ===")
    print("Matched predictions with labels: ", len(matched_results))
    matched_results.to_csv(f"{eval_dir}/matched_results.csv", index=False)

    # Normalize isFlagged to boolean
    matched_results["predicted_flag"] = matched_results["isFlagged"].apply(
        lambda x: True if str(x).strip().lower() == "true" else False
    )
    matched_results["label_flag"] = matched_results["binary_treatment"].apply(
        lambda x: True if str(x).strip().upper() == "TRUE" else False
    )

    # Binary flagging evaluation
    print("\n=== Binary Flagging Evaluation ===")
    show_eval_metrics(
        matched_results["label_flag"].map({True: "Flagged", False: "Not Flagged"}),
        matched_results["predicted_flag"].map({True: "Flagged", False: "Not Flagged"}),
        title="Binary Flagging (isFlagged)",
    )

    # Direction evaluation (on matched results that have both directions)
    direction_mask = matched_results["direction_pred"].notna() & matched_results["direction_label"].notna()
    if direction_mask.any():
        print("\n=== Direction Evaluation ===")
        show_eval_metrics(
            matched_results.loc[direction_mask, "direction_label"],
            matched_results.loc[direction_mask, "direction_pred"],
            title="Direction",
        )

    # Save detailed evaluation results
    eval_output = matched_results[[
        "citing_cluster_id", "mainCitationString", "cited_id",
        "label_flag", "predicted_flag", "direction_label", "direction_pred",
        "final_treatment", "severity", "quote", "rationale",
    ]].copy()
    eval_output.to_csv(f"{eval_dir}/eval_results.csv", index=False)

    # Mismatches for error analysis
    mismatches = eval_output[eval_output["label_flag"] != eval_output["predicted_flag"]]
    print(f"\nTotal mismatches: {len(mismatches)}")
    mismatches.to_csv(f"{eval_dir}/mismatches.csv", index=False)

    # Model predictions not in labels
    parsed_keys = parsed_df[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    matched_keys = matched[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    cl_missed_keys = parsed_keys.merge(matched_keys, on=["citing_cluster_id", "mainCitationString"], how="left", indicator=True)
    cl_missed_keys = cl_missed_keys[cl_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    cl_missed = parsed_df.merge(cl_missed_keys, on=["citing_cluster_id", "mainCitationString"], how="inner")
    print("Predictions not in labels: ", len(cl_missed))
    cl_missed.to_csv(f"{eval_dir}/cl_missed.csv", index=False)

    # Labels the model missed
    label_keys = labels[["citing_id", "cited_id"]].drop_duplicates()
    matched_label_keys = matched[["citing_id", "cited_id"]].drop_duplicates()
    model_missed_keys = label_keys.merge(matched_label_keys, on=["citing_id", "cited_id"], how="left", indicator=True)
    model_missed_keys = model_missed_keys[model_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    model_missed = labels.merge(model_missed_keys, on=["citing_id", "cited_id"], how="inner")
    print("Labels missed by model: ", len(model_missed))
    model_missed.to_csv(f"{eval_dir}/model_missed.csv", index=False)

