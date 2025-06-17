import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from chat_utils import claude_completion
from instructions import claude_instructions_v213
from prediction_utils import get_passages, parse_completion, single_retry

logging.basicConfig(level=logging.INFO)

MAX_WORKERS = 5
WAIT_S = 2
DATA_FOLDER = "data/excerpts_v0/"


@single_retry
def get_prediction(excerpts):
    try:
        completion = claude_completion(excerpts)
        parsed_completion = parse_completion(completion)
        return parsed_completion
    except Exception as e:
        logging.warning(f"model completion failed: {e}")
        return dict()


def process_prediction(row, folder_path):
    filename = row["filename"]
    file_path = os.path.join(folder_path, filename)

    excerpts = get_passages(file_path)
    prediction = get_prediction(excerpts)

    return {filename: prediction}


def get_all_predictions(df, folder_path=DATA_FOLDER, max_workers=MAX_WORKERS):
    predictions = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(process_prediction, row, folder_path): index
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
