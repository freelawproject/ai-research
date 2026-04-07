"""Run the citator batch pipeline.

Usage:
    # Full pipeline: generate input → batch submit → wait → collect → postprocess
    python run_batch.py --data-dir ../experiments_04052026/data --court ca1

    # All courts
    python run_batch.py --data-dir ../experiments_04052026/data --court all

    # Limit records per court (useful for testing)
    python run_batch.py --data-dir ../experiments_04052026/data --court ca1 --num-records 5

    # Collect results from completed batch + postprocess
    python run_batch.py --data-dir ../experiments_04052026/data --court ca1 --collect-only

    # Re-run post-processing on existing parsed results
    python run_batch.py --data-dir ../experiments_04052026/data --court ca1 --postprocess-only

    # Re-run only the merge step
    python run_batch.py --data-dir ../experiments_04052026/data --court ca1 --merge-only
"""

import logging
import os

import pandas as pd

from utils.batch_utils import (
    load_cluster_ids,
    prepare_jsonl,
    upload_to_s3,
    submit_batch_job,
    wait_for_jobs,
    S3_INPUT_PREFIX,
)
from utils.parse_utils import process_batch
from utils.postprocess import (
    clean_treatments,
    deduplicate_cited_cases,
    flag_for_review,
    final_deduplicate,
    merge_to_labels,
)
from utils.reevaluate import reevaluate_flagged

logging.basicConfig(level=logging.INFO)

REEVAL_MODEL_ID = "moonshotai.kimi-k2.5"

ALL_COURTS = [
    "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7",
    "ca8", "ca9", "ca10", "ca11", "cadc", "cafc",
]


def generate_court_input(court, data_dir, num_records=None):
    """Generate input txt file with sampled cluster IDs for a given court."""
    citing_metadata_path = os.path.join(data_dir, "citing_metadata.csv")
    input_dir = os.path.join(data_dir, "input")

    citing_df = pd.read_csv(citing_metadata_path)
    court_df = citing_df[(citing_df["court"] == court) & (citing_df["source"] == "sampled")]

    if court_df.empty:
        available = sorted(citing_df[citing_df["source"] == "sampled"]["court"].unique())
        raise ValueError(f"No sampled opinions found for court '{court}'. Available courts: {available}")

    cluster_ids = sorted(court_df["cluster_id"].tolist())
    if num_records is not None:
        cluster_ids = cluster_ids[:num_records]

    txt_name = f"{court}.txt"
    txt_path = os.path.join(input_dir, txt_name)
    os.makedirs(input_dir, exist_ok=True)

    with open(txt_path, "w") as f:
        f.write(",".join(str(cid) for cid in cluster_ids))

    logging.info(f"Generated {txt_path} with {len(cluster_ids)} cluster IDs for court={court}")
    return txt_name


def prepare_and_submit(courts, data_dir, num_records=None, model_id=None, system_prompt=None):
    """Generate input for one or more courts, combine into a single JSONL, and submit one batch job."""
    input_dir = os.path.join(data_dir, "input")
    opinion_dir = os.path.join(input_dir, "opinion_texts")

    # Collect all cluster IDs across courts
    all_cluster_ids = []
    for court in courts:
        txt_name = generate_court_input(court, data_dir, num_records)
        txt_path = os.path.join(input_dir, txt_name)
        cluster_ids = load_cluster_ids(txt_path)
        logging.info(f"{court}: {len(cluster_ids)} cluster IDs")
        all_cluster_ids.extend(cluster_ids)

    logging.info(f"Total: {len(all_cluster_ids)} cluster IDs across {len(courts)} court(s)")

    # Prepare combined JSONL
    job_label = "+".join(courts)
    combined_output_dir = os.path.join(data_dir, "output")
    os.makedirs(combined_output_dir, exist_ok=True)
    jsonl_path = prepare_jsonl(
        job_label, all_cluster_ids, opinion_dir,
        output_dir=combined_output_dir, model_id=model_id, system_prompt=system_prompt,
    )

    # Upload to S3 and submit
    s3_key = f"{S3_INPUT_PREFIX}/{job_label}.jsonl"
    s3_input_uri = upload_to_s3(jsonl_path, s3_key)
    job_arn = submit_batch_job(job_label, s3_input_uri, model_id=model_id)
    return job_arn


def postprocess(parsed_df, data_dir, reeval_model_id=REEVAL_MODEL_ID, resume=False):
    """Run the full post-processing pipeline on parsed batch results."""
    output_dir = os.path.join(data_dir, "output")
    labels_path = os.path.join(data_dir, "cited_metadata.csv")

    # ── Step 2: Clean treatments and deduplicate ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 2: Clean treatments and deduplicate")
    logging.info(f"{'='*60}")
    parsed_df = clean_treatments(parsed_df)
    deduped_df = deduplicate_cited_cases(parsed_df)
    deduped_df.to_csv(os.path.join(output_dir, "deduped_results.csv"), index=False)
    logging.info(f"Saved deduped results ({len(deduped_df)} rows)")

    # ── Step 3: Re-evaluate flagged results ──
    logging.info(f"\n{'='*60}")
    logging.info(f"Step 3: Re-evaluate flagged results — {reeval_model_id}")
    logging.info(f"{'='*60}")
    flagged_df, clean_df = flag_for_review(deduped_df)

    if not flagged_df.empty:
        reevaluated_df = reevaluate_flagged(
            flagged_df, model_id=reeval_model_id, output_dir=output_dir, resume=resume,
        )
        combined_df = pd.concat([clean_df, reevaluated_df], ignore_index=True)
    else:
        logging.info("No results flagged — skipping re-evaluation")
        combined_df = clean_df

    combined_df.to_csv(os.path.join(output_dir, "reevaluated_results.csv"), index=False)
    logging.info(f"Saved re-evaluated results ({len(combined_df)} rows)")

    # ── Step 4: Clean treatments and final deduplication ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 4: Clean treatments and final deduplication")
    logging.info(f"{'='*60}")
    combined_df = clean_treatments(combined_df)
    final_df = final_deduplicate(combined_df)
    final_df.to_csv(os.path.join(output_dir, "final_results.csv"), index=False)
    logging.info(f"Saved final results ({len(final_df)} rows)")

    # ── Step 5: Merge to authority labels ──
    logging.info(f"\n{'='*60}")
    logging.info("Step 5: Merge to authority labels")
    logging.info(f"{'='*60}")
    if os.path.exists(labels_path):
        labels_df = pd.read_csv(labels_path)
        eval_dir = os.path.join(data_dir, "eval")
        merge_to_labels(final_df, labels_df, eval_dir)
    else:
        logging.warning(f"Labels file not found at {labels_path} — skipping merge")

    logging.info(f"\n{'='*60}")
    logging.info("Post-processing complete")
    logging.info(f"{'='*60}")


def collect_only(courts, data_dir):
    """Download and parse batch results without post-processing."""
    output_dir = os.path.join(data_dir, "output")
    job_label = "+".join(courts)

    logging.info(f"\n{'='*60}")
    logging.info(f"Collecting batch results for {job_label}")
    logging.info(f"{'='*60}")
    parsed_df = process_batch(job_label, output_dir=output_dir)

    if parsed_df is None or parsed_df.empty:
        logging.warning("No results collected")
    else:
        logging.info(f"Collected {len(parsed_df)} parsed results")


def collect_and_postprocess(courts, data_dir, reeval_model_id=REEVAL_MODEL_ID, resume=False):
    """Download batch results and run post-processing."""
    output_dir = os.path.join(data_dir, "output")
    job_label = "+".join(courts)

    logging.info(f"\n{'='*60}")
    logging.info(f"Collecting batch results for {job_label}")
    logging.info(f"{'='*60}")
    parsed_df = process_batch(job_label, output_dir=output_dir)

    if parsed_df is None or parsed_df.empty:
        logging.warning(f"No results to post-process")
        return

    postprocess(parsed_df, data_dir, reeval_model_id, resume=resume)


def postprocess_only(data_dir, reeval_model_id=REEVAL_MODEL_ID, resume=False):
    """Run only post-processing on already-parsed batch results."""
    output_dir = os.path.join(data_dir, "output")
    parsed_path = os.path.join(output_dir, "parsed_results.csv")

    if not os.path.exists(parsed_path):
        raise FileNotFoundError(f"No parsed results found at {parsed_path}")

    parsed_df = pd.read_csv(parsed_path)
    logging.info(f"Loaded parsed results ({len(parsed_df)} rows) from {parsed_path}")
    postprocess(parsed_df, data_dir, reeval_model_id, resume=resume)


def merge_only(data_dir):
    """Run only Step 5: Merge final results to authority labels."""
    output_dir = os.path.join(data_dir, "output")
    final_path = os.path.join(output_dir, "final_results.csv")
    labels_path = os.path.join(data_dir, "cited_metadata.csv")

    if not os.path.exists(final_path):
        raise FileNotFoundError(f"No final results found at {final_path}")

    final_df = pd.read_csv(final_path)
    logging.info(f"Loaded final results ({len(final_df)} rows) from {final_path}")

    if os.path.exists(labels_path):
        labels_df = pd.read_csv(labels_path)
        eval_dir = os.path.join(data_dir, "eval")
        merge_to_labels(final_df, labels_df, eval_dir)
    else:
        logging.warning(f"Labels file not found at {labels_path}")

    logging.info("Merge complete")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run citator batch pipeline")
    parser.add_argument("--data-dir", required=True, help="Path to experiment data directory")
    parser.add_argument(
        "--court", required=True,
        help="Comma-separated court IDs (e.g., ca1, 'ca1,ca2,ca3'). Use 'all' for all courts.",
    )
    parser.add_argument(
        "--num-records", type=int, default=None,
        help="Limit number of records per court (for testing).",
    )
    parser.add_argument("--collect-only", action="store_true", help="Download results + postprocess")
    parser.add_argument("--postprocess-only", action="store_true", help="Run postprocessing on existing parsed results")
    parser.add_argument("--merge-only", action="store_true", help="Run only merge step")
    parser.add_argument("--resume", action="store_true", help="Resume from previously saved incremental results")
    parser.add_argument("--test-mode", action="store_true", help="Test mode: use Haiku with a trivial prompt, skip postprocessing")
    args = parser.parse_args()

    courts = ALL_COURTS if args.court == "all" else [c.strip() for c in args.court.split(",")]

    # Test mode overrides
    test_model = "us.anthropic.claude-haiku-4-5-20251001-v1:0" if args.test_mode else None
    test_prompt = "Reply with a short JSON: {\"id\": <the number in the input>}" if args.test_mode else None

    if args.merge_only:
        merge_only(args.data_dir)
    elif args.postprocess_only:
        postprocess_only(args.data_dir, resume=args.resume)
    elif args.collect_only:
        if args.test_mode:
            collect_only(courts, args.data_dir)
        else:
            collect_and_postprocess(courts, args.data_dir, resume=args.resume)
    else:
        # Submit all courts as a single batch job
        job_arn = prepare_and_submit(
            courts, args.data_dir, args.num_records,
            model_id=test_model, system_prompt=test_prompt,
        )

        logging.info(f"Submitted batch job for {','.join(courts)}, waiting for completion...")
        results = wait_for_jobs([job_arn])

        status = results.get(job_arn, {})
        logging.info(f"Batch job: {status.get('status', 'unknown')}")

        if status.get("status") == "Completed":
            if args.test_mode:
                collect_only(courts, args.data_dir)
            else:
                collect_and_postprocess(courts, args.data_dir, resume=args.resume)
        else:
            logging.error(f"Batch job failed: {status.get('message', '')}")


if __name__ == "__main__":
    main()
