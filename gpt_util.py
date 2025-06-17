import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

logging.basicConfig(level=logging.INFO)

from openai import OpenAI

from gpt_prompts import SUP_CLASS, SUB_CLASS_OTHER

MAX_WORKERS = 5
WAIT_S = 2


API_KEY = "<YOUR_API_KEY>"
MODEL = "gpt-4o-mini-2024-07-18" #"gpt-4o-2024-11-20"


def single_retry(func, wait_s=WAIT_S):

    def retry(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            time.sleep(wait_s)
            return func(*args, **kwargs)

    return retry



# call gpt model
def gpt_completion(
    user_prompt, api_key=API_KEY, system_prompt=SUB_CLASS_OTHER, model=MODEL
):

    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": f"{system_prompt}"},
            {"role": "user", "content": f"{user_prompt}"},
        ],
    )

    completion = {
        "model": response.model,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "label": response.choices[0].message.content,
    }

    return completion


@single_retry
def get_prediction(row):
    try:
        completion = gpt_completion(row["text"])
        completion["unique_id"] = row["unique_id"]
        return completion
    except Exception as e:
        logging.warning(f"model completion failed: {e}")
        return dict()


def predict(df, max_workers=MAX_WORKERS):
    predictions = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(get_prediction, row): index for index, row in df.iterrows()
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
