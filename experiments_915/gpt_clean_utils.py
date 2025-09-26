import json
import logging
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed


from gpt_utils import gpt_completion

logging.basicConfig(level=logging.INFO)


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


def parse_completion(completion):
    raw_results = completion.get("raw_results") # {"docket_numbers":[{"unique_id":"101","cleaned_nums":["80","184"]},{"unique_id":"102","cleaned_nums":["940, Misc."]}]}
    output = {
        "model": completion.get("model"),
        "input_tokens": completion.get("input_tokens"),
        "cached_tokens": completion.get("cached_tokens"),
        "output_tokens": completion.get("output_tokens"),
        "raw_results": raw_results,
        "parsed_results": json.loads(raw_results),
    }
    return output


@single_retry
def get_prediction(user_message, system_prompt, model_id):
    try:
        completion = gpt_completion(user_message, system_prompt=system_prompt, model_id=model_id)
        parsed_completion = parse_completion(completion)
        return parsed_completion
    except Exception as e:
        logging.warning(f"model completion failed: {e}")
        return dict()


def parse_output(prediction):
    try:
        parsed_results = prediction.get("parsed_results", {})
        records = {}
        for item in parsed_results.get("docket_numbers", []):
            docket_id = item.get("unique_id", "")
            docket_num = [s.upper() for s in sorted(list(set(item.get("cleaned_nums", []))))]
            records[int(docket_id)] = "; ".join(docket_num)
        return records
    except Exception as e:
        logging.warning(f"parsing output failed: {e}")
        return dict()


def extract_with_llm(
    batches,
    system_prompt,
    model_id,
    max_workers=MAX_WORKERS
):
    raw_output = []
    parsed_output = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_batch_idx = {
            executor.submit(get_prediction, f"{batch}", system_prompt, model_id): idx
            for idx, batch in enumerate(batches)
        }

        for future in as_completed(future_to_batch_idx):
            batch_idx = future_to_batch_idx[future]
            try:
                prediction = future.result()
                raw_output.append(prediction)
                parsed_records = parse_output(prediction)
                parsed_output.append(parsed_records)
                # write the raw_output and parsed_output to json files after each batch
                with open(f"predictions/raw_output.jsonl", "a") as f:
                    f.write(json.dumps(prediction) + "\n")
                with open(f"predictions/parsed_output.jsonl", "a") as f:
                    f.write(json.dumps(parsed_records) + "\n")
                logging.info(f"Processed batch {batch_idx}")
            except Exception as e:
                logging.error(f"Error processing batch {batch_idx}: {e}")

    return raw_output, parsed_output

# parsed_output = [{"001": ["80", "184"], "002": ["940, Misc."]}, {"003": ["11850"]}]

# batches = [[{'1003': 'Nos. 1, 2, 3, and 11 Originals'}, {'1004': 'Nos. 87, 88'}]]