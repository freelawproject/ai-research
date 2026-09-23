import os
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.metrics import classification_report, multilabel_confusion_matrix, confusion_matrix
from sklearn.exceptions import UndefinedMetricWarning

from utils.ss_instructions import instructions_v225
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

def evaluate(dir="normal_recent_citing_ids", instructions_version="v225", label_version="v2"):
    if instructions_version == "v225":
        print(f"Instruction Length (char) for {instructions_version}: ", len(instructions_v225))
        output_dir = f"data/output/v225/{dir}"
        eval_dir = f"data/eval/v225/{dir}"
    elif instructions_version == "v318":
        print(f"Instruction Length (char) for {instructions_version}: ", len(instructions_v318))
        output_dir = f"data/output/v318/{dir}"
        eval_dir = f"data/eval/v318/{dir}"

    if label_version == "v1":
        labels = pd.read_csv("data/metadata_labels.csv")
    elif label_version == "v2":
        labels = pd.read_csv("data/revised_metadata_labels_317.csv")

    labels = labels[(labels["final_treatment"] != "PENDING") & (labels["final_treatment"] != "REMOVE") & (labels["final_treatment"] != "MANUAL")]
    os.makedirs(eval_dir, exist_ok=True)

    print("\n=== Overall Statistics ===")
    raw_df = pd.read_csv(f"{output_dir}/raw_results.csv")
    print("Total number of citing cases: ", len(raw_df))
    print("Total number of input tokens: ", raw_df["input_tokens"].sum())
    print("Total number of output tokens: ", raw_df["output_tokens"].sum())
    print("Total number of cache read input tokens: ", raw_df["cache_read_input_tokens"].sum())
    print("Total number of cache write input tokens: ", raw_df["cache_write_input_tokens"].sum())
    print("Total latency (ms): ", raw_df["latency_ms"].sum())

    print("\n=== Evaluation Statistics ===")
    parsed_df = pd.read_csv(f"{output_dir}/parsed_results.csv")
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
    matched_result = matched[['citing_cluster_id', 'mainCitationString', 'citedName', 'caseHistory', 'treatment', 'opinionType', 'quote', 'rationale',
        'confidence', 'cited_cluster_id', 'cited_citation_strings', 'cited_case_name', 'final_treatment', 'severity', 'direction', "court", "case_type"]]
    print("Number of results in both model & complete authorities: ", len(matched_result))
    matched_result.to_csv(f"{eval_dir}/matched_results.csv", index=False)

    # results in both model & complete authorities with expert annotated treatment
    matched_with_treatment = matched_result[~matched_result["final_treatment"].isna()]
    print("Number of results in both model & complete authorities with expert annotated treatment: ", len(matched_with_treatment))
    # Replace any NaN values in the "treatment" column with "Unknown"
    matched_with_treatment["treatment"] = matched_with_treatment["treatment"].fillna("Unknown")
    matched_with_treatment.rename(columns={"treatment": "predicted_treatment",
                                           "caseHistory": "predicted_direction",
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
    matched_with_treatment = matched_with_treatment[["citing_cluster_id", "mainCitationString", "cited_cluster_id", "cited_case_name", "court", "case_type",
                                                     "label_treatment", "label_severity", "label_direction",
                                                     "predicted_treatment", "predicted_severity", "predicted_direction",
                                                     "quote", "rationale", "confidence"]]
    matched_with_treatment.to_csv(f"{eval_dir}/matched_with_treatment.csv", index=False)

    # results from model not in complete authorities
    cl_missed = parsed_df[~parsed_df["mainCitationString"].isin(matched["mainCitationString"])]
    print("Number of results from model not in complete authorities: ", len(cl_missed))
    cl_missed.to_csv(f"{eval_dir}/cl_missed.csv", index=False)

    # results the model missed that are in the complete authorities
    model_missed = labels[~labels["cited_cluster_id"].isin(matched["cited_cluster_id"])]
    print("Number of results the model missed that are in the complete authorities: ", len(model_missed))
    model_missed.to_csv(f"{eval_dir}/model_missed.csv", index=False)

    sample_and_eval(matched_with_treatment)


def plot_bar(data, title, xlabel, ylabel):
    _, ax = plt.subplots(figsize=(10, 8))
    data.plot(kind='bar', title=title, ax=ax)
    for p in ax.patches:
        ax.annotate(f'{p.get_height()}', (p.get_x() + p.get_width() / 2., p.get_height()),
                    ha='center', va='bottom')
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis='x', rotation=45)
    for label in ax.get_xticklabels():
        label.set_ha("right")
    plt.tight_layout()
    plt.show()


def create_v1_benchmark(file1="normal_v0309.csv", file2="special_v0309.csv", output_file="v1_benchmark.csv", instructions_version="v225"):
    df1 = pd.read_csv(f"data/eval/{instructions_version}/triple_review/{file1}")
    df2 = pd.read_csv(f"data/eval/{instructions_version}/triple_review/{file2}")
    df = pd.concat([df1, df2], ignore_index=True)

    df = df[~df["final_treatment"].isnull()]
    df = df[~df["final_treatment"].isin(["PENDING", "REMOVE"])]
    df = df[~df["eval_treatment"].isin(["PENDING", "REMOVE"])]

    # Keep only select columns for evaluation
    df = df[["citing_cluster_id", "mainCitationString", "cited_cluster_id", "cited_case_name", "court", "case_type",
             "final_treatment", "final_severity", "final_direction", "eval_treatment", "eval_severity", "eval_direction",
             "quote", "rationale", "confidence"]]
    
    df.to_csv(f"data/eval/{instructions_version}/triple_review/{output_file}", index=False)

    ## Rename the columns to be consistent with evaluation function
    df.rename(columns={"final_treatment": "label_treatment",
                       "final_severity": "label_severity",
                       "final_direction": "label_direction",
                       "eval_treatment": "predicted_treatment",
                       "eval_severity": "predicted_severity",
                       "eval_direction": "predicted_direction"}, inplace=True)

    ## Print some statistics about the benchmark dataset
    print("--- Total number of records: ", len(df))

    print("--- Inspect missing values:")
    print(df.isnull().sum())

    print("--- Total number of unique citing_cluster_id: ", df["citing_cluster_id"].nunique())
    print("--- Total number of unique cited_cluster_id: ", df["cited_cluster_id"].nunique())

    plot_bar(df.groupby("court")["citing_cluster_id"].nunique(),
             "Number of Unique Citing Cluster IDs by Court", "Court", "Number of Unique Citing Cluster IDs")
    plot_bar(df.groupby("case_type")["citing_cluster_id"].nunique(),
             "Number of Unique Citing Cluster IDs by Case Type", "Case Type", "Number of Unique Citing Cluster IDs")
    plot_bar(df["label_treatment"].value_counts(),
             "Class Distribution for Label Treatment", "Label Treatment", "Frequency")
    plot_bar(df["predicted_treatment"].value_counts(),
             "Class Distribution for Predicted Treatment", "Predicted Treatment", "Frequency")

    sample_and_eval(df)


def evaluate_triple_review(filename="normal_v0309.csv", instructions_version="v225"):
    if instructions_version == "v225":
        print(f"Instruction Length (char) for {instructions_version}: ", len(instructions_v225))
        df = pd.read_csv(f"data/eval/v225/triple_review/{filename}")
    elif instructions_version == "v318":
        print(f"Instruction Length (char) for {instructions_version}: ", len(instructions_v318))
        df = pd.read_csv(f"data/eval/v318/triple_review/{filename}")

    # Remove records where the final_treatment & eval_treatment is "PENDING" or "REMOVE"
    df = df[~df["final_treatment"].isnull()]
    df = df[~df["final_treatment"].isin(["PENDING", "REMOVE", "MANUAL"])]
    df = df[~df["eval_treatment"].isin(["PENDING", "REMOVE", "MANUAL"])]

    # Keep only select columns for evaluation
    df = df[["citing_cluster_id", "mainCitationString", "cited_cluster_id", "cited_case_name", "court", "case_type",
             "final_treatment", "final_severity", "final_direction", "eval_treatment", "eval_severity", "eval_direction",
             "quote", "rationale", "confidence"]]

    ## Rename the columns to be consistent with evaluation function
    df.rename(columns={"final_treatment": "label_treatment",
                       "final_severity": "label_severity",
                       "final_direction": "label_direction",
                       "eval_treatment": "predicted_treatment",
                       "eval_severity": "predicted_severity",
                       "eval_direction": "predicted_direction"}, inplace=True)

    sample_and_eval(df)
