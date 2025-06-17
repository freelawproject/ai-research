import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gpt_utils import gpt_completion

logging.basicConfig(level=logging.INFO)

MAX_WORKERS = 5
WAIT_S = 2
DATA_FOLDER = "data/excerpts_v0/"


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


def extract_json(raw_results):
    json_match = re.search(r"```json\n(.*?)\n```", raw_results, re.DOTALL)

    if not json_match:
        json_match = re.search(r"(\{.*\})", raw_results, re.DOTALL)

    if not json_match:
        logging.error(
            f"No valid JSON block found in response text. Response: {raw_results}"
        )
        return dict()

    json_str = json_match.group(1).strip()

    try:
        parsed_data = json.loads(json_str.replace("\n", " "))
    except json.JSONDecodeError as e:
        logging.error(f"JSON parsing error: {e}. Response json str: {json_str}")
        return dict()

    return parsed_data


def parse_completion(completion):
    raw_results = completion.get("raw_results")

    try:
        parsed_results = json.loads(raw_results.replace("\n", " "))
    except (json.JSONDecodeError, TypeError) as e:
        parsed_results = extract_json(raw_results)

    overruled = parsed_results.get("overruled")
    confidence = parsed_results.get("confidence")
    rationale = parsed_results.get("rationale")

    output = {
        "model": completion.get("model"),
        "input_tokens": completion.get("input_tokens"),
        "output_tokens": completion.get("output_tokens"),
        "prediction": overruled,
        "confidence": confidence,
        "rationale": rationale,
        "raw_results": raw_results,
    }

    return output


@single_retry
def get_prediction(model_id, system_prompt, excerpts):
    try:
        completion = gpt_completion(model_id, system_prompt, excerpts)
        parsed_completion = parse_completion(completion)
        return parsed_completion
    except Exception as e:
        logging.warning(f"model completion failed: {e}")
        return dict()


def process_prediction(row, folder_path, model_id, system_prompt):
    filename = row["filename"]
    file_path = os.path.join(folder_path, filename)

    excerpts = get_passages(file_path)
    prediction = get_prediction(model_id, system_prompt, excerpts)

    return {filename: prediction}


def predict(
    df, model_id, system_prompt, folder_path=DATA_FOLDER, max_workers=MAX_WORKERS
):
    predictions = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(
                process_prediction, row, folder_path, model_id, system_prompt
            ): index
            for index, row in df.iterrows()
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                result = future.result()
                predictions.append(result)
                logging.info(f"Completed: {index}")
            except Exception as e:
                logging.error(f"Error processing index {index}: {e}")

    return predictions