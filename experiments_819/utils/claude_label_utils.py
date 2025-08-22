import ast
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from utils.converse_utils import converse_completion

logging.basicConfig(level=logging.INFO)

import warnings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

MAX_WORKERS = 5
WAIT_S = 2


def single_retry(func, wait_s=WAIT_S):

    def retry(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            time.sleep(wait_s)
            return func(*args, **kwargs)

    return retry


def get_passages(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
    return content


@single_retry
def get_prediction(model_id, system_prompt, citing_opinion, target_opinions):
    try:
        completion = converse_completion(model_id, system_prompt, citing_opinion, target_opinions)
        return completion
    except Exception as e:
        logging.warning(f"model completion failed: {e}")
        return dict()


def get_opinion_prefix(opinion_type):
    prefixes = {
        "010combined": "**Combined Opinion**",
        "020lead": "**Lead Opinion**",
        "030concurrence": "**Concurrence Opinion**",
        "035concurrenceinpart": "**Concurrence In Part Opinion**",
        "040dissent": "**Dissent Opinion**",
    }
    return prefixes.get(opinion_type, "**Opinion**")


def get_raw_results_df(predictions):
    raw_results = []
    for citing_cluster_id, info in predictions.items():
        try:
            raw_results.append(
                {
                    "citing_cluster_id": citing_cluster_id,
                    "model": info.get("model", ""),
                    "input_tokens": info.get("input_tokens", ""),
                    "output_tokens": info.get("output_tokens", ""),
                    "raw_results": info.get("raw_results", []),
                }
            )
        except Exception as e:
            logging.error(f"Error getting raw results df: {e}")
            raw_results.append(
                {
                    "citing_cluster_id": citing_cluster_id,
                    "model": "",
                    "input_tokens": "",
                    "output_tokens": "",
                    "raw_results": [],
                }
            )
    return pd.DataFrame(raw_results)


def get_parsed_results_df(predictions):
    parsed_results = []
    for citing_cluster_id, info in predictions.items():
        try:
            for cited_info in info.get("raw_results", []):
                parsed_results.append(
                    {
                        "citing_cluster_id": citing_cluster_id,
                        "cited_cluster_id": cited_info.get("uniqueID", ""),
                        "targetName": cited_info.get("targetName", ""),
                        "targetHistory": cited_info.get("targetHistory", ""),
                        "targetTreatment": cited_info.get("targetTreatment", ""),
                        "quote": cited_info.get("quote", ""),
                        "rationale": cited_info.get("rationale", ""),
                    }
                )
        except Exception as e:
            logging.error(
                f"Error getting parsed results df for citing_cluster_id={citing_cluster_id}: {e}"
            )
            parsed_results.append(
                {
                    "citing_cluster_id": citing_cluster_id,
                    "cited_cluster_id": "",
                    "targetName": "",
                    "targetHistory": "",
                    "targetTreatment": "",
                    "quote": "",
                    "rationale": "",
                }
            )
    return pd.DataFrame(parsed_results)


def process_single_prediction(
    citing_id, citing_metadata, cited_dict, model_id, system_prompt, data_folder
):
    citing_filenames = ast.literal_eval(citing_metadata.get("citing_filenames", []))
    target_opinions = cited_dict.get(citing_id, {})

    citing_opinion = ""
    if len(citing_filenames) > 1:
        citing_filenames = [f for f in citing_filenames if "010combined" not in f]

    for filename in citing_filenames:
        _, opinion_type = filename.replace(".txt", "").split("_")
        opinion_prefix = get_opinion_prefix(opinion_type)

        citing_file_path = os.path.join(data_folder, filename)
        raw_opinion = get_passages(citing_file_path)
        soup = BeautifulSoup(raw_opinion, "html.parser")
        clean_opinion = soup.get_text(separator=" ", strip=True)
        citing_opinion += f"{opinion_prefix}:\n" + clean_opinion.replace("'", "").replace('"', '') + "\n"
    
    prediction = get_prediction(model_id, system_prompt, citing_opinion, target_opinions)
    return citing_id, prediction


def predict(
    citing_dict,
    cited_dict,
    model_id,
    system_prompt,
    data_folder,
    max_workers=MAX_WORKERS,
):
    output = {}
    processed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_citing_id = {
            executor.submit(
                process_single_prediction,
                citing_id,
                citing_metadata,
                cited_dict,
                model_id,
                system_prompt,
                data_folder,
            ): citing_id
            for citing_id, citing_metadata in citing_dict.items()
        }

        for future in as_completed(future_to_citing_id):
            citing_id = future_to_citing_id[future]
            try:
                citing_id, prediction = future.result()
                output[citing_id] = prediction
                processed += 1
                logging.info(f"Processed {processed}: citing_id {citing_id}")
            except Exception as e:
                print(f"Error processing citing_id {citing_id}: {e}")

    raw_results_df = get_raw_results_df(output)
    parsed_results_df = get_parsed_results_df(output)

    return raw_results_df, parsed_results_df
