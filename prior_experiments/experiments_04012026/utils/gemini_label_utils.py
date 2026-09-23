import json
import logging
from json_repair import repair_json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from utils.gemini_utils import DEFAULT_MODEL_ID, gemini_completion
from utils.instructions import instructions_v318

logging.basicConfig(level=logging.INFO)

MAX_WORKERS = 5
WAIT_S = 2
SYSTEM_PROMPT = instructions_v318

INPUT_DIR = "data/input"
OUTPUT_DIR = "data/output"


def parse_cited_cases(cited_cases):
    if isinstance(cited_cases, str):
        try:
            return json.loads(cited_cases)
        except json.JSONDecodeError:
            return repair_json(cited_cases, return_objects=True)
    return cited_cases if cited_cases else []


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
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error(f"Error reading opinion for cluster_id={cluster_id}: {e}")
        return ""


@single_retry
def get_prediction(system_prompt, opinion_text, model_id):
    return gemini_completion(system_prompt, opinion_text, model_id=model_id)


def get_raw_results_df(predictions, output_dir=OUTPUT_DIR):
    rows = []
    for cluster_id, info in predictions.items():
        rows.append(
            {
                "citing_cluster_id": cluster_id,
                "model": info.get("model", ""),
                "stop_reason": info.get("stop_reason", ""),
                "input_tokens": info.get("input_tokens", 0),
                "output_tokens": info.get("output_tokens", 0),
                "cited_cases": info.get("cited_cases", []),
            }
        )
    results_df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "raw_results.csv")
    results_df.to_csv(path, index=False)
    logging.info(f"Saved raw results ({len(results_df)} rows) to {path}")


def get_parsed_results_df(predictions, output_dir=OUTPUT_DIR):
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
            logging.error(f"Error parsing results for cluster_id={cluster_id}: {e}")
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
    logging.info(f"Saved parsed results ({len(results_df)} rows) to {path}")


def process_single_prediction(cluster_id, citing_dir, system_prompt, model_id):
    logging.info(f"Starting cluster_id={cluster_id}")
    opinion_text = load_opinion_text(citing_dir, cluster_id)
    opinion_text = opinion_text.replace("targetCase", "citedCase")
    prediction = get_prediction(system_prompt, opinion_text, model_id=model_id)
    return cluster_id, prediction


def predict(
    cluster_ids,
    citing_dir,
    system_prompt=SYSTEM_PROMPT,
    model_id=DEFAULT_MODEL_ID,
    output_dir=OUTPUT_DIR,
    max_workers=MAX_WORKERS,
):
    output = {}
    processed = 0
    total = len(cluster_ids)
    logging.info(f"Starting predictions for {total} cluster_ids with {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(
                process_single_prediction, cluster_id, citing_dir, system_prompt, model_id
            ): cluster_id
            for cluster_id in cluster_ids
        }

        for future in as_completed(future_to_id):
            cluster_id = future_to_id[future]
            try:
                cluster_id, prediction = future.result()
                output[cluster_id] = prediction
                processed += 1
                logging.info(f"Completed {processed}/{total}: cluster_id={cluster_id}")
            except Exception as e:
                processed += 1
                logging.error(f"Failed {processed}/{total}: cluster_id={cluster_id}: {e}")

    get_raw_results_df(output, output_dir)
    get_parsed_results_df(output, output_dir)


def run(
    txt_name="example.txt",
    system_prompt=SYSTEM_PROMPT,
    model_id=DEFAULT_MODEL_ID,
    output_dir=OUTPUT_DIR,
    max_workers=MAX_WORKERS,
):
    txt_path = os.path.join(INPUT_DIR, txt_name)
    citing_dir = os.path.join(INPUT_DIR, "citing")
    cluster_ids = load_cluster_ids(txt_path)
    logging.info(f"Loaded {len(cluster_ids)} cluster_ids from {txt_path}")

    output_dir = os.path.join(output_dir, "v318", model_id)
    os.makedirs(output_dir, exist_ok=True)
    return predict(cluster_ids, citing_dir, system_prompt, model_id, output_dir, max_workers)
