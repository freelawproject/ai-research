import logging
import os

import pandas as pd

from utils.bedrock_label_utils import run as bedrock_run
from utils.postprocess import (
    clean_treatments,
    deduplicate_cited_cases,
    flag_for_review,
    final_deduplicate,
    merge_to_labels,
)
from utils.reevaluate import reevaluate_flagged

logging.basicConfig(level=logging.INFO)

MODEL_ID = "us.anthropic.claude-sonnet-4-6"
REEVAL_MODEL_ID = "moonshotai.kimi-k2.5"
INSTRUCTIONS_VERSION = "v404"

CITING_METADATA_PATH = "data/citing_metadata.csv"
LABELS_PATH = "data/cited_metadata.csv"
INPUT_DIR = "data/input"


def generate_court_input(court, citing_metadata_path=CITING_METADATA_PATH, input_dir=INPUT_DIR):
    """Generate input txt file with cluster IDs for a given circuit court.

    Args:
        court: Circuit court identifier (e.g., "ca1", "ca2", "cadc", "cafc").
        citing_metadata_path: Path to citing_metadata.csv from experiments_04022026.
        input_dir: Directory to write the output txt file.

    Returns:
        The filename of the generated input file (e.g., "ca1.txt").
    """
    citing_df = pd.read_csv(citing_metadata_path)
    court_df = citing_df[(citing_df["court"] == court) & (citing_df["source"] == "sampled")]

    if court_df.empty:
        available = sorted(citing_df[citing_df["source"] == "sampled"]["court"].unique())
        raise ValueError(f"No sampled opinions found for court '{court}'. Available courts: {available}")

    cluster_ids = sorted(court_df["cluster_id"].tolist())
    txt_name = f"{court}.txt"
    txt_path = os.path.join(input_dir, txt_name)
    os.makedirs(input_dir, exist_ok=True)

    with open(txt_path, "w") as f:
        f.write(",".join(str(cid) for cid in cluster_ids))

    logging.info(f"Generated {txt_path} with {len(cluster_ids)} cluster IDs for court={court}")
    return txt_name


def run(court="ca1", labels_path=LABELS_PATH, reeval_model_id=REEVAL_MODEL_ID):
    # ── Step 0: Generate input file for the target court ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 0: Generate input file for court={court}")
    logging.info(f"{'='*60}")
    txt_name = generate_court_input(court)

    # ── Step 1: Generate predictions (with page splitting for long opinions) ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 1: Generate predictions — Sonnet 4.6 ({MODEL_ID})")
    logging.info(f"{'='*60}")
    parsed_df = bedrock_run(
        txt_name=txt_name,
        court=court,
        model_id=MODEL_ID,
        instructions_version=INSTRUCTIONS_VERSION,
    )

    output_dir = f"data/output/{court}/{INSTRUCTIONS_VERSION}/claude-sonnet-4-6"

    # ── Step 1b: Clean up treatment formatting errors ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 1b: Clean treatment formatting errors")
    logging.info(f"{'='*60}")
    parsed_df = clean_treatments(parsed_df)

    # ── Step 2: Deduplicate cited cases (keep most negative treatment) ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 2: Deduplicate cited cases (most negative treatment)")
    logging.info(f"{'='*60}")
    deduped_df = deduplicate_cited_cases(parsed_df)
    deduped_df.to_csv(os.path.join(output_dir, "deduped_results.csv"), index=False)
    logging.info(f"Saved deduped results ({len(deduped_df)} rows)")

    # ── Step 3: Re-evaluate flagged results ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 3: Re-evaluate flagged results — {reeval_model_id}")
    logging.info(f"{'='*60}")
    flagged_df, clean_df = flag_for_review(deduped_df)

    if not flagged_df.empty:
        reevaluated_df = reevaluate_flagged(flagged_df, model_id=reeval_model_id, output_dir=output_dir)
        combined_df = pd.concat([clean_df, reevaluated_df], ignore_index=True)
    else:
        logging.info("No results flagged — skipping re-evaluation")
        combined_df = clean_df

    combined_df.to_csv(os.path.join(output_dir, "reevaluated_results.csv"), index=False)
    logging.info(f"Saved re-evaluated results ({len(combined_df)} rows)")

    # ── Step 4: Final deduplication (after re-evaluation may introduce new treatments) ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 4: Final deduplication")
    logging.info(f"{'='*60}")
    final_df = final_deduplicate(combined_df)
    final_df.to_csv(os.path.join(output_dir, "final_results.csv"), index=False)
    logging.info(f"Saved final results ({len(final_df)} rows)")

    # ── Step 5: Merge to authority labels (for expert review, not evaluation) ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 5: Merge to authority labels")
    logging.info(f"{'='*60}")
    if os.path.exists(labels_path):
        labels_df = pd.read_csv(labels_path)
        eval_dir = f"data/eval/{court}/{INSTRUCTIONS_VERSION}/claude-sonnet-4-6"
        merge_to_labels(final_df, labels_df, eval_dir)
    else:
        logging.warning(f"Labels file not found at {labels_path} — skipping merge")

    logging.info(f"\n{'='*60}")
    logging.info("Pipeline complete")
    logging.info(f"{'='*60}")


def merge_only(court="ca1", labels_path=LABELS_PATH):
    """Run only Step 5: Merge final results to authority labels."""
    output_dir = f"data/output/{court}/{INSTRUCTIONS_VERSION}/claude-sonnet-4-6"
    final_path = os.path.join(output_dir, "final_results.csv")

    if not os.path.exists(final_path):
        raise FileNotFoundError(f"No final results found at {final_path} — run the full pipeline first")

    final_df = pd.read_csv(final_path)
    logging.info(f"Loaded final results ({len(final_df)} rows) from {final_path}")

    if os.path.exists(labels_path):
        labels_df = pd.read_csv(labels_path)
        eval_dir = f"data/eval/{court}/{INSTRUCTIONS_VERSION}/claude-sonnet-4-6"
        merge_to_labels(final_df, labels_df, eval_dir)
    else:
        logging.warning(f"Labels file not found at {labels_path} — skipping merge")

    logging.info("Merge complete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run citator pipeline for a circuit court")
    parser.add_argument("--court", default="ca1", help="Circuit court ID (e.g., ca1, ca2, cadc, cafc)")
    parser.add_argument("--merge-only", action="store_true", help="Run only Step 5 (merge to labels)")
    args = parser.parse_args()

    if args.merge_only:
        merge_only(court=args.court)
    else:
        run(court=args.court)
