"""Run the citator pipeline on example cases with optional evaluation.

Usage:
    python run_example.py --input-dir ../data --output-dir ../experiments_04032026/data --txt-path ../experiments_04032026/example.txt
    python run_example.py --input-dir ../data --output-dir ../experiments_04032026/data --txt-path ../experiments_04032026/example.txt --evaluate
    python run_example.py --output-dir ../experiments_04032026/data --evaluate-only
"""

import logging
import os

import pandas as pd

from utils.predict import run as predict_run
from utils.postprocess import (
    clean_treatments,
    deduplicate_cited_cases,
    filter_non_case_citations,
    flag_for_review,
    final_deduplicate,
    merge_to_labels,
)
from utils.reevaluate import reevaluate_flagged

logging.basicConfig(level=logging.INFO)

MODEL_ID = "us.anthropic.claude-sonnet-4-6"
REEVAL_MODEL_ID = "moonshotai.kimi-k2.5"


def run(
    input_dir,
    output_dir,
    txt_path,
    opinion_dir=None,
    labels_path=None,
    reeval_model_id=REEVAL_MODEL_ID,
    evaluate=False,
    resume=False,
):
    results_dir = os.path.join(output_dir, "output")
    eval_dir = os.path.join(output_dir, "eval")
    if opinion_dir is None:
        opinion_dir = os.path.join(input_dir, "example_opinion_texts")
    # ── Step 1: Generate predictions ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 1: Generate predictions — Sonnet 4.6 ({MODEL_ID})")
    logging.info(f"{'='*60}")
    parsed_df = predict_run(
        txt_name=os.path.basename(txt_path),
        model_id=MODEL_ID,
        opinion_dir=opinion_dir,
        output_dir=results_dir,
        input_dir=os.path.dirname(txt_path),
        resume=resume,
    )

    # ── Step 2: Clean treatments and deduplicate ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 2: Clean treatments and deduplicate")
    logging.info(f"{'='*60}")
    parsed_df = filter_non_case_citations(parsed_df)
    parsed_df = clean_treatments(parsed_df)
    deduped_df = deduplicate_cited_cases(parsed_df)
    deduped_df.to_csv(os.path.join(results_dir, "deduped_results.csv"), index=False)
    logging.info(f"Saved deduped results ({len(deduped_df)} rows)")

    # ── Step 3: Re-evaluate flagged results ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 3: Re-evaluate flagged results — {reeval_model_id}")
    logging.info(f"{'='*60}")
    flagged_df, clean_df = flag_for_review(deduped_df)

    if not flagged_df.empty:
        reevaluated_df = reevaluate_flagged(
            flagged_df, model_id=reeval_model_id, output_dir=results_dir, resume=resume,
        )
        combined_df = pd.concat([clean_df, reevaluated_df], ignore_index=True)
    else:
        logging.info("No results flagged — skipping re-evaluation")
        combined_df = clean_df

    combined_df.to_csv(os.path.join(results_dir, "reevaluated_results.csv"), index=False)
    logging.info(f"Saved re-evaluated results ({len(combined_df)} rows)")

    # ── Step 4: Clean treatments and final deduplication ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 4: Clean treatments and final deduplication")
    logging.info(f"{'='*60}")
    combined_df = clean_treatments(combined_df)
    final_df = final_deduplicate(combined_df)
    final_df.to_csv(os.path.join(results_dir, "final_results.csv"), index=False)
    logging.info(f"Saved final results ({len(final_df)} rows)")

    # ── Step 5: Merge to labels ──
    if labels_path and os.path.exists(labels_path):
        logging.info(f"\n{'='*60}")
        logging.info("Step 5: Merge to labels")
        logging.info(f"{'='*60}")
        labels_df = pd.read_csv(labels_path)
        labels_df = labels_df[
            (labels_df["final_treatment"] != "PENDING")
            & (labels_df["final_treatment"] != "REMOVE")
            & (labels_df["final_treatment"] != "MANUAL")
        ]
        merge_to_labels(final_df, labels_df, eval_dir)

    # ── Step 6: Evaluate (optional) ──
    if evaluate and labels_path and os.path.exists(labels_path):
        logging.info(f"\n{'='*60}")
        logging.info("Step 6: Evaluate against expert labels")
        logging.info(f"{'='*60}")
        from utils.eval_utils import evaluate_example
        evaluate_example(
            output_dir=results_dir,
            eval_dir=eval_dir,
            labels_path=labels_path,
        )

    logging.info(f"\n{'='*60}")
    logging.info("Pipeline complete")
    logging.info(f"{'='*60}")


def evaluate_only(output_dir, labels_path):
    """Run only evaluation on existing final results."""
    results_dir = os.path.join(output_dir, "output")
    eval_dir = os.path.join(output_dir, "eval")

    final_path = os.path.join(results_dir, "final_results.csv")
    if not os.path.exists(final_path):
        raise FileNotFoundError(f"No final results found at {final_path} — run the full pipeline first")

    if not labels_path or not os.path.exists(labels_path):
        raise FileNotFoundError(f"Labels file required for evaluation: {labels_path}")

    # Merge to labels
    logging.info(f"\n{'='*60}")
    logging.info("Step 5: Merge to labels")
    logging.info(f"{'='*60}")
    final_df = pd.read_csv(final_path)
    labels_df = pd.read_csv(labels_path)
    labels_df = labels_df[
        (labels_df["final_treatment"] != "PENDING")
        & (labels_df["final_treatment"] != "REMOVE")
        & (labels_df["final_treatment"] != "MANUAL")
    ]
    merge_to_labels(final_df, labels_df, eval_dir)

    # Evaluate
    logging.info(f"\n{'='*60}")
    logging.info("Step 6: Evaluate against expert labels")
    logging.info(f"{'='*60}")
    from utils.eval_utils import evaluate_example
    evaluate_example(
        output_dir=results_dir,
        eval_dir=eval_dir,
        labels_path=labels_path,
    )

    logging.info(f"\n{'='*60}")
    logging.info("Evaluation complete")
    logging.info(f"{'='*60}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run citator pipeline on example cases")
    parser.add_argument("--input-dir", default="../data", help="Path to shared input data directory")
    parser.add_argument("--output-dir", required=True, help="Path to experiment data directory for results")
    parser.add_argument("--txt-path", default=None, help="Path to cluster ID list file (default: input_dir/example.txt)")
    parser.add_argument("--opinion-dir", default=None, help="Override opinion text directory")
    parser.add_argument("--labels", default="0410.csv", help="Benchmark label filename in data/benchmark_original/ (e.g., 0410.csv)")
    parser.add_argument("--evaluate", action="store_true", help="Run full pipeline + evaluation")
    parser.add_argument("--evaluate-only", action="store_true", help="Run only evaluation on existing results")
    parser.add_argument("--resume", action="store_true", help="Resume from previously saved incremental results")
    args = parser.parse_args()

    labels_path = os.path.join(args.input_dir, "benchmark_original", args.labels)

    if args.evaluate_only:
        evaluate_only(output_dir=args.output_dir, labels_path=labels_path)
    else:
        txt_path = args.txt_path or os.path.join(args.input_dir, "example.txt")
        run(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            txt_path=txt_path,
            opinion_dir=args.opinion_dir,
            labels_path=labels_path,
            evaluate=args.evaluate,
            resume=args.resume,
        )


if __name__ == "__main__":
    main()
