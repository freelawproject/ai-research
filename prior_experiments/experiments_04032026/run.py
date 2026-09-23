import logging
import os

import pandas as pd

from utils.bedrock_label_utils import run as bedrock_run
from utils.postprocess import (
    deduplicate_cited_cases,
    flag_for_review,
    final_deduplicate,
    merge_to_labels,
)
from utils.reevaluate import reevaluate_flagged

logging.basicConfig(level=logging.INFO)

MODEL_ID = "us.anthropic.claude-sonnet-4-6"
REEVAL_MODEL_ID = "us.anthropic.claude-sonnet-4-6"  # Can be changed to "moonshotai.kimi-k2.5"
INSTRUCTIONS_VERSION = "v403"


LABELS_PATH = "data/cited_metadata.csv"


def run(txt_name="example.txt", labels_path=LABELS_PATH, reeval_model_id=REEVAL_MODEL_ID):
    # ── Step 1: Generate predictions (with page splitting for long opinions) ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 1: Generate predictions — Sonnet 4.6 ({MODEL_ID})")
    logging.info(f"{'='*60}")
    parsed_df = bedrock_run(txt_name=txt_name, model_id=MODEL_ID)

    output_dir = f"data/output/{INSTRUCTIONS_VERSION}/claude-sonnet-4-6"

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

    # ── Step 5: Merge to authority labels ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 5: Merge to authority labels")
    logging.info(f"{'='*60}")
    if os.path.exists(labels_path):
        labels_df = pd.read_csv(labels_path)
        eval_dir = f"data/eval/{INSTRUCTIONS_VERSION}/claude-sonnet-4-6"
        merge_to_labels(final_df, labels_df, eval_dir)
    else:
        logging.warning(f"Labels file not found at {labels_path} — skipping merge")

    logging.info(f"\n{'='*60}")
    logging.info("Pipeline complete")
    logging.info(f"{'='*60}")


if __name__ == "__main__":
    run()
