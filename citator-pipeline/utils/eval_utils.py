import os
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.metrics import classification_report, multilabel_confusion_matrix, confusion_matrix
from sklearn.exceptions import UndefinedMetricWarning

from utils.instructions import citator
from utils.postprocess import (
    normalize_citation, _get_treatment_rank,
    severity_mapping, direction_mapping,
)

pd.options.mode.chained_assignment = None  # suppress SettingWithCopyWarning
warnings.filterwarnings("ignore", category=UndefinedMetricWarning)


# severity_mapping and direction_mapping are imported from postprocess to keep
# the canonical taxonomy in one place — see postprocess.py for definitions.

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


def evaluate_example(output_dir, eval_dir, labels_path):
    """Evaluate using expert-annotated labels.

    Reads final_results.csv (post-processed) from output_dir and merges
    against expert labels from labels_path.
    """
    all_labels = pd.read_csv(labels_path)

    print(f"Instruction Length (char): ", len(citator))
    os.makedirs(eval_dir, exist_ok=True)

    # Overall statistics
    print("\n=== Overall Statistics ===")
    raw_df = pd.read_csv(f"{output_dir}/raw_results.csv")
    print("Total number of citing cases: ", len(raw_df))
    print("Total number of input tokens: ", raw_df["input_tokens"].sum())
    print("Total number of output tokens: ", raw_df["output_tokens"].sum())
    if "latency_ms" in raw_df.columns:
        print("Total latency (ms): ", raw_df["latency_ms"].sum())

    # Load final (post-processed) results
    print("\n=== Evaluation Statistics ===")
    final_path = f"{output_dir}/final_results.csv"
    parsed_path = f"{output_dir}/parsed_results.csv"
    if os.path.exists(final_path):
        results_df = pd.read_csv(final_path)
        print(f"Using post-processed results: {final_path}")
    else:
        results_df = pd.read_csv(parsed_path)
        print(f"Using raw parsed results: {parsed_path}")

    results_df = results_df.drop_duplicates(subset=["citing_cluster_id", "mainCitationString"], keep="first")
    results_df["citing_cluster_id"] = results_df["citing_cluster_id"].astype(int)
    all_labels["citing_cluster_id"] = all_labels["citing_cluster_id"].astype(int)

    labels = all_labels[all_labels["citing_cluster_id"].isin(results_df["citing_cluster_id"])]

    merged = results_df.merge(labels, on="citing_cluster_id", how="left", suffixes=("_pred", "_label"))

    def _citations_match(r):
        citation = r.get("mainCitationString")
        label_citations = r.get("cited_citation_strings")
        if pd.isna(citation) or pd.isna(label_citations):
            return False
        return normalize_citation(citation) in normalize_citation(str(label_citations))

    mask = merged.apply(_citations_match, axis=1)
    matched = merged[mask]

    # Results in both model & expert labels (includes PENDING/REMOVE/MANUAL)
    # Deduplicate parallel citations that match the same label. Keep the most severe treatment.
    matched_results = matched[[
        "citing_cluster_id", "mainCitationString", "caseName", "caseHistory",
        "treatment", "opinionType", "quote", "rationale",
        "cited_cluster_id", "cited_citation_strings", "cited_case_name",
        "final_treatment", "severity", "direction",
    ]].copy()
    matched_results["_rank"] = matched_results["treatment"].apply(_get_treatment_rank)
    matched_results = (
        matched_results.sort_values("_rank")
        .drop_duplicates(subset=["citing_cluster_id", "cited_cluster_id"], keep="first")
        .drop(columns=["_rank"])
    )
    print("Number of results in both model & expert labels: ", len(matched_results))
    matched_results.to_csv(f"{eval_dir}/matched_results.csv", index=False)

    # Matched with treatment (exclude PENDING/REMOVE/MANUAL)
    matched_with_treatment = matched_results[
        matched_results["final_treatment"].notna()
        & matched_results["treatment"].notna()
        & ~matched_results["final_treatment"].isin(["PENDING", "REMOVE", "MANUAL"])
    ].copy()
    print("Number of results with both model and expert treatment: ", len(matched_with_treatment))

    matched_with_treatment.rename(columns={
        "treatment": "predicted_treatment",
        "final_treatment": "label_treatment",
        "severity": "label_severity",
        "direction": "label_direction",
    }, inplace=True)

    # Normalize "Cited as recognized by" → "Cited by"
    matched_with_treatment["predicted_treatment"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: "Cited by" if isinstance(x, str) and x.endswith("Cited as recognized by") else x
    )
    # Remove the extra "by" before "as recognized by" for better mapping to severity/direction
    matched_with_treatment["predicted_treatment"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: x.replace(" by as recognized by", " as recognized by")
        if isinstance(x, str) and " as recognized by" in x
        else x
    )

    # Add predicted severity and direction
    matched_with_treatment["predicted_severity"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: severity_mapping.get(x.replace(" as recognized", ""), None)
        if isinstance(x, str) and x.endswith("as recognized by")
        else severity_mapping.get(x, None)
    )
    matched_with_treatment["predicted_direction"] = matched_with_treatment["predicted_treatment"].apply(
        lambda x: "Related Reference" if isinstance(x, str) and x.endswith("as recognized by")
        else direction_mapping.get(x, None)
    )

    matched_with_treatment = matched_with_treatment[[
        "citing_cluster_id", "mainCitationString", "cited_cluster_id", "cited_case_name",
        "label_treatment", "label_severity", "label_direction",
        "predicted_treatment", "predicted_severity", "predicted_direction",
        "quote", "rationale",
    ]]
    matched_with_treatment.to_csv(f"{eval_dir}/matched_with_treatment.csv", index=False)

    # Model predictions not in expert labels
    parsed_keys = results_df[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    matched_keys = matched[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    cl_missed_keys = parsed_keys.merge(
        matched_keys, on=["citing_cluster_id", "mainCitationString"], how="left", indicator=True
    )
    cl_missed_keys = cl_missed_keys[cl_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    cl_missed = results_df.merge(cl_missed_keys, on=["citing_cluster_id", "mainCitationString"], how="inner")
    print("Number of results from model not in expert labels: ", len(cl_missed))
    cl_missed.to_csv(f"{eval_dir}/cl_missed.csv", index=False)

    # Expert labels missed by model (using all labels including PENDING/REMOVE/MANUAL)
    label_keys = labels[["citing_cluster_id", "cited_cluster_id"]].drop_duplicates()
    matched_label_keys = matched[["citing_cluster_id", "cited_cluster_id"]].drop_duplicates()
    model_missed_keys = label_keys.merge(
        matched_label_keys, on=["citing_cluster_id", "cited_cluster_id"], how="left", indicator=True
    )
    model_missed_keys = model_missed_keys[model_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    model_missed = labels.merge(model_missed_keys, on=["citing_cluster_id", "cited_cluster_id"], how="inner")
    print("Number of expert labels missed by model: ", len(model_missed))
    model_missed.to_csv(f"{eval_dir}/model_missed.csv", index=False)

    if not matched_with_treatment.empty:
        sample_and_eval(matched_with_treatment)
    else:
        print("No matched results with treatment to evaluate")
