import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import nltk
import pandas as pd

logging.basicConfig(level=logging.INFO)

nltk.download("punkt_tab", quiet=True)
from nltk.tokenize import sent_tokenize
from openai import OpenAI
from transformers import AutoTokenizer

MAX_WORKERS = 5
WAIT_S = 2

TOKENIZER = AutoTokenizer.from_pretrained("google-bert/bert-base-cased")
MAX_TOKENS = 7800 #480
NUM_OVERLAP_SENTENCES = 2

API_KEY = "<YOUR_API_KEY>"
MODEL = "gpt-4o-2024-11-20" # "gpt-4o-mini-2024-07-18"
SYSTEM_PROMPT = """
Given a case law opinion, generate two search queries. Both queries should be phrased as natural language questions that a user might enter into a search engine.

- The **user does not have prior access** to the opinion, so the queries should be **general** rather than referring to specific case details.  
- The opinion should be the **best possible answer** to the **relevant** query.  
- The opinion should be **insufficient** or **unrelated** to answering the **irrelevant** query.  

### Query Types:  
1. **Relevant Query**: A question where the given opinion is both **highly relevant** and **sufficient** to provide an answer.  
2. **Irrelevant Query**: A question where the given opinion is **not relevant** and **not sufficient** to provide an answer.  

### Output Format:
Return the queries in JSON format:
```json
{
"relevant": "What are the legal standards for self-defense in criminal cases?",
"irrelevant": "How does bankruptcy law apply to small businesses?"
}
```
"""


def single_retry(func, wait_s=WAIT_S):

    def retry(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            time.sleep(wait_s)
            return func(*args, **kwargs)

    return retry


# This is the same chunking function we implemented for Inception
def split_text_into_chunks(
    text,
    tokenizer=TOKENIZER,
    max_tokens=MAX_TOKENS,
    num_overlap_sentences=NUM_OVERLAP_SENTENCES,
):
    """Split text into chunks based on sentences, not exceeding max_tokens, with sentence overlap"""

    # Split the text to sentences & encode sentences with tokenizer
    sentences = sent_tokenize(text)
    encoded_sentences = [
        tokenizer.encode(sentence, add_special_tokens=False) for sentence in sentences
    ]
    lead_text = ""  # "search_document: "
    lead_tokens = tokenizer.encode(lead_text)
    lead_len = len(lead_tokens)
    chunks = []
    current_chunks: list[str] = []
    current_token_counts = len(lead_tokens)

    for sentence_tokens in encoded_sentences:
        sentence_len = len(sentence_tokens)
        # if the current sentence itself is above max_tokens
        if lead_len + sentence_len > max_tokens:
            # store the previous chunk
            if current_chunks:
                chunks.append(lead_text + " ".join(current_chunks))
            # truncate the sentence and store the truncated sentence as its own chunk
            truncated_sentence = tokenizer.decode(
                sentence_tokens[: (max_tokens - len(lead_tokens))]
            )
            chunks.append(lead_text + truncated_sentence)

            # start a new chunk with no overlap (because adding the current sentence will exceed the max_tokens)
            current_chunks = []
            current_token_counts = lead_len
            continue

        # if adding the new sentence will cause the chunk to exceed max_tokens
        if current_token_counts + sentence_len > max_tokens:
            overlap_sentences = current_chunks[-max(0, num_overlap_sentences) :]
            # store the previous chunk
            if current_chunks:
                chunks.append(lead_text + " ".join(current_chunks))

            overlap_token_counts = tokenizer.encode(
                " ".join(overlap_sentences), add_special_tokens=False
            )
            # If the sentence with the overlap exceeds the limit, start a new chunk without overlap.
            if lead_len + len(overlap_token_counts) + sentence_len > max_tokens:
                current_chunks = [tokenizer.decode(sentence_tokens)]
                current_token_counts = lead_len + sentence_len
            else:
                current_chunks = overlap_sentences + [tokenizer.decode(sentence_tokens)]
                current_token_counts = (
                    lead_len + len(overlap_token_counts) + sentence_len
                )
            continue

        # if within max_tokens, continue to add the new sentence to the current chunk
        current_chunks.append(tokenizer.decode(sentence_tokens))
        current_token_counts += len(sentence_tokens)

    # store the last chunk if it has any content
    if current_chunks:
        chunks.append(lead_text + " ".join(current_chunks))
    return chunks


# Count the number of tokens in a chunk
def count_tokens(chunk, tokenizer=TOKENIZER):
    return len(tokenizer.encode(chunk))


# call gpt model
def gpt_completion(
    user_prompt, api_key=API_KEY, system_prompt=SYSTEM_PROMPT, model=MODEL
):

    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt}"},
            {"role": "user", "content": f"{user_prompt}"},
        ],
    )
    results = json.loads(response.choices[0].message.content)

    completion = {
        "model": response.model,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "relevant": results.get("relevant", ""),
        "irrelevant": results.get("irrelevant", ""),
    }

    return completion


@single_retry
def get_prediction(row):
    try:
        completion = gpt_completion(row["chunked_opinion"])
        completion["chunk_id"] = row["chunk_id"]
        return completion
    except Exception as e:
        logging.warning(f"model completion failed: {e}")
        return dict()


# Process the opinions
def process_opinions(df, text_column="opinion", id_column="opinion_id"):

    # Apply chunking function
    df["chunked_opinion"] = df[text_column].apply(split_text_into_chunks)

    # Explode to create multiple rows for each chunk
    df = df.explode("chunked_opinion").reset_index(drop=True)

    # Assign chunk_id within each opinion_id group
    df["chunk_id"] = df.groupby(id_column).cumcount() + 1
    df["chunk_id"] = df[id_column].astype(str) + "_" + df["chunk_id"].astype(str)

    # Calculate chunk size
    df["chunk_size"] = df["chunked_opinion"].apply(count_tokens)

    # Select and rename columns
    df = df[["chunk_id", "chunk_size", "chunked_opinion", id_column, text_column]]

    return df


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
