"""Real-time prediction module with incremental result saving.

Each prediction is saved to disk as soon as it returns, so partial results
survive if the pipeline is interrupted.
"""

import json
import logging
from json_repair import repair_json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd

from utils.bedrock_converse_utils import DEFAULT_MODEL_ID, converse_completion
from utils.instructions import citator
from utils.preprocess import split_opinion_to_pages

logger = logging.getLogger(__name__)

MAX_WORKERS = 5
WAIT_S = 2
SYSTEM_PROMPT = citator


def parse_cited_cases(cited_cases):
    if isinstance(cited_cases, str):
        try:
            data = json.loads(cited_cases)
        except json.JSONDecodeError:
            data = repair_json(cited_cases, return_objects=True)
    else:
        data = cited_cases

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("citedCases", data.get("reassessments", []))
    return [] if not data else [data]


def single_retry(func, wait_s=WAIT_S):
    def retry(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            time.sleep(wait_s)
            return func(*args, **kwargs)

    return retry


def load_cluster_ids(txt_path):
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()
    return [id.strip() for id in content.split(",") if id.strip()]


def load_opinion_text(citing_dir, cluster_id):
    file_path = os.path.join(citing_dir, f"{cluster_id}.txt")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@single_retry
def get_prediction(system_prompt, opinion_text, model_id):
    return converse_completion(system_prompt, opinion_text, model_id=model_id)


def _save_prediction_incremental(cluster_id, prediction, output_dir):
    """Save a single prediction to a per-cluster JSON file for crash recovery."""
    incremental_dir = os.path.join(output_dir, "incremental")
    os.makedirs(incremental_dir, exist_ok=True)
    path = os.path.join(incremental_dir, f"{cluster_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cluster_id": cluster_id, **prediction}, f)


def _load_incremental_predictions(output_dir):
    """Load any previously saved incremental predictions."""
    incremental_dir = os.path.join(output_dir, "incremental")
    predictions = {}
    if not os.path.isdir(incremental_dir):
        return predictions
    for fname in os.listdir(incremental_dir):
        if fname.endswith(".json"):
            path = os.path.join(incremental_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cid = data.pop("cluster_id", fname.replace(".json", ""))
                predictions[str(cid)] = data
            except Exception as e:
                logger.warning(f"Failed to load incremental result {path}: {e}")
    return predictions


def get_raw_results_df(predictions, output_dir):
    rows = []
    for cluster_id, info in predictions.items():
        rows.append(
            {
                "citing_cluster_id": cluster_id,
                "model": info.get("model", ""),
                "stop_reason": info.get("stop_reason", ""),
                "latency_ms": info.get("latency_ms", 0),
                "input_tokens": info.get("input_tokens", 0),
                "output_tokens": info.get("output_tokens", 0),
                "num_pages": info.get("num_pages", 1),
                "cited_cases": info.get("cited_cases", []),
            }
        )
    results_df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "raw_results.csv")
    results_df.to_csv(path, index=False)
    logger.info(f"Saved raw results ({len(results_df)} rows) to {path}")


def get_parsed_results_df(predictions, output_dir):
    rows = []
    for cluster_id, info in predictions.items():
        try:
            cited_cases = info.get("cited_cases", [])
            cited_cases = parse_cited_cases(cited_cases)
            for cited_info in cited_cases:
                rows.append(
                    {
                        "citing_cluster_id": cluster_id,
                        "mainCitationString": cited_info.get("mainCitationString", ""),
                        "caseName": cited_info.get("caseName", ""),
                        "actingCase": cited_info.get("actingCase", ""),
                        "caseHistory": cited_info.get("caseHistory", ""),
                        "treatment": cited_info.get("treatment", ""),
                        "opinionType": cited_info.get("opinionType", ""),
                        "quote": cited_info.get("quote", ""),
                        "rationale": cited_info.get("rationale", ""),
                    }
                )
        except Exception as e:
            logger.error(f"Error parsing results for cluster_id={cluster_id}: {e}")
            rows.append(
                {
                    "citing_cluster_id": cluster_id,
                    "mainCitationString": "",
                    "caseName": "",
                    "actingCase": "",
                    "caseHistory": "",
                    "treatment": "",
                    "opinionType": "",
                    "quote": "",
                    "rationale": "",
                }
            )
    results_df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "parsed_results.csv")
    results_df.to_csv(path, index=False)
    logger.info(f"Saved parsed results ({len(results_df)} rows) to {path}")
    return results_df


def process_single_prediction(cluster_id, citing_dir, system_prompt, model_id, output_dir=None):
    """Process a single opinion, splitting into pages if too long."""
    logger.info(f"Starting cluster_id={cluster_id}")
    opinion_text = load_opinion_text(citing_dir, cluster_id)

    pages = split_opinion_to_pages(opinion_text)
    num_pages = len(pages)

    # Save pages to disk if split occurred
    if num_pages > 1 and output_dir:
        pages_dir = os.path.join(output_dir, "pages", str(cluster_id))
        os.makedirs(pages_dir, exist_ok=True)
        for i, page in enumerate(pages):
            page_path = os.path.join(pages_dir, f"page_{i+1}.txt")
            with open(page_path, "w", encoding="utf-8") as f:
                f.write(page)
        logger.info(f"cluster_id={cluster_id}: saved {num_pages} pages to {pages_dir}")

    if num_pages == 1:
        prediction = get_prediction(system_prompt, opinion_text, model_id=model_id)
        prediction["num_pages"] = 1
        return cluster_id, prediction

    # Process multiple pages and combine results
    logger.info(f"cluster_id={cluster_id}: split into {num_pages} pages")
    all_cited_cases = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0
    model_name = ""
    stop_reasons = []

    for i, page in enumerate(pages):
        logger.info(f"cluster_id={cluster_id}: processing page {i+1}/{num_pages}")
        prediction = get_prediction(system_prompt, page, model_id=model_id)
        model_name = prediction.get("model", model_name)
        stop_reasons.append(prediction.get("stop_reason", ""))
        total_input_tokens += prediction.get("input_tokens", 0)
        total_output_tokens += prediction.get("output_tokens", 0)
        total_latency_ms += prediction.get("latency_ms", 0)
        page_cases = prediction.get("cited_cases", [])
        if isinstance(page_cases, str):
            page_cases = parse_cited_cases(page_cases)
        all_cited_cases.extend(page_cases)

    combined = {
        "model": model_name,
        "stop_reason": "; ".join(stop_reasons),
        "latency_ms": total_latency_ms,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "num_pages": num_pages,
        "cited_cases": all_cited_cases,
    }
    return cluster_id, combined


def predict(
    cluster_ids,
    citing_dir,
    system_prompt=SYSTEM_PROMPT,
    model_id=DEFAULT_MODEL_ID,
    output_dir="data/output",
    max_workers=MAX_WORKERS,
    resume=False,
):
    # Resume from any previously saved incremental results
    output = {}
    if resume:
        output = _load_incremental_predictions(output_dir)
    already_done = set(output.keys())
    remaining = [cid for cid in cluster_ids if str(cid) not in already_done]

    if already_done:
        logger.info(f"Resuming: {len(already_done)} already completed, {len(remaining)} remaining")

    processed = len(already_done)
    total = len(cluster_ids)
    save_lock = Lock()

    logger.info(f"Starting predictions for {len(remaining)} cluster_ids with {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(
                process_single_prediction, cluster_id, citing_dir, system_prompt, model_id, output_dir
            ): cluster_id
            for cluster_id in remaining
        }

        for future in as_completed(future_to_id):
            cluster_id = future_to_id[future]
            try:
                cluster_id, prediction = future.result()
                output[str(cluster_id)] = prediction
                processed += 1

                # Save immediately to disk
                with save_lock:
                    _save_prediction_incremental(cluster_id, prediction, output_dir)

                logger.info(f"Completed {processed}/{total}: cluster_id={cluster_id}")
            except Exception as e:
                processed += 1
                logger.error(f"Failed {processed}/{total}: cluster_id={cluster_id}: {e}")

    get_raw_results_df(output, output_dir)
    parsed_df = get_parsed_results_df(output, output_dir)
    return parsed_df


def run(
    txt_name,
    system_prompt=SYSTEM_PROMPT,
    model_id=DEFAULT_MODEL_ID,
    opinion_dir="data/input/opinion_texts",
    output_dir="data/output",
    input_dir="data/input",
    max_workers=MAX_WORKERS,
    resume=False,
):
    txt_path = os.path.join(input_dir, txt_name)
    citing_dir = opinion_dir
    cluster_ids = load_cluster_ids(txt_path)
    logger.info(f"Loaded {len(cluster_ids)} cluster_ids from {txt_path}")

    os.makedirs(output_dir, exist_ok=True)
    return predict(cluster_ids, citing_dir, system_prompt, model_id, output_dir, max_workers, resume=resume)
