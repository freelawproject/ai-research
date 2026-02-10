import ast
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup
from nupunkt import sent_tokenize

MAX_WORKERS = 8

FOLDER = "data/raw_citing_opinions/"
ANNOTATE_FOLDER = "data/annotated_opinions/"
SENTENCES = 6

# Configure logging to output to console in real-time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def add_annotation(opinion, cited_id):
    # Annotate cited in the opinion text
    soup = BeautifulSoup(opinion, features="lxml")
    target_id = str(cited_id)

    # Find all <span class="citation"> elements
    quotes = []
    for span in soup.find_all("span", class_="citation"):
        a_tag = span.find("a")
        if a_tag and "href" in a_tag.attrs:
            match = re.search(r"/opinion/(\d+)/", a_tag["href"])
            if match and match.group(1) == target_id:
                quotes.append(span)
    # print("quotes: ", quotes)

    for quote in quotes:
        quote.string = f"<citedDecision>{quote.text}</citedDecision>"
        # print(quote.string)

    return soup


def clean_opinion(soup):
    clean_soup = soup.get_text(separator=" ", strip=True)
    return clean_soup


def extract_excerpts(
    opinion, cited_id, sentences_before=SENTENCES, sentences_after=SENTENCES
):
    soup = add_annotation(opinion, cited_id)
    clean_soup = clean_opinion(soup)

    # Split text into sentences
    sentences = sent_tokenize(clean_soup)

    # Find all indices of sentences containing <citedDecision> tags
    try:
        tag_indices = [
            i
            for i, sentence in enumerate(sentences)
            if re.search(r"<citedDecision>.*?</citedDecision>", sentence, re.DOTALL)
        ]
        # print("tag_indices: ", tag_indices)

        # Collect excerpts with the specified number of sentences before and after
        excerpts = [
            (
                max(0, index - sentences_before),
                min(len(sentences), index + sentences_after + 1),
            )
            for index in tag_indices
        ]

        # Merge overlapping excerpts
        merged_excerpts = []
        current_start, current_end = excerpts[0]
        for start, end in excerpts[1:]:
            if start <= current_end:  # Overlapping
                current_end = max(current_end, end)
            else:  # No overlap, save the current excerpt
                merged_excerpts.append((current_start, current_end))
                current_start, current_end = start, end
        merged_excerpts.append((current_start, current_end))

        # Extract the excerpts
        extracted_excerpts = [
            " ".join(sentences[start:end]) for start, end in merged_excerpts
        ]
    except IndexError:
        extracted_excerpts = []

    return extracted_excerpts


def parse_row(row):
    return (
        ast.literal_eval(row["citing_filenames"]),
        row["citing_cluster_id"],
        row["cited_cluster_id"],
        row["use_full_opinion"],
    )


def load_opinion(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


def get_opinion_prefix(opinion_type):
    prefixes = {
        "010combined": "**Combined Opinion**",
        "020lead": "**Lead Opinion**",
        "030concurrence": "**Concurrence Opinion**",
        "035concurrenceinpart": "**Concurrence In Part Opinion**",
        "040dissent": "**Dissenting Opinion**",
    }
    return prefixes.get(opinion_type, "**Opinion**")


def save_annotated_opinion(index, citing_id, cited_id, opinion_type, excerpts):
    prefix = get_opinion_prefix(opinion_type)
    annotated_excerpts = "\n".join([f"{prefix}\n{excerpt}" for excerpt in excerpts])
    annotated_filename = (
        f"{index + 1:04d}.{citing_id}_cites_{cited_id}_{opinion_type}.txt"
    )
    annotated_file_path = os.path.join(ANNOTATE_FOLDER, annotated_filename)

    with open(annotated_file_path, "w") as annotated_file:
        annotated_file.write(annotated_excerpts)


def process_row(index, row):
    filenames, citing_id, cited_id, use_full_opinion = parse_row(row)

    for filename in sorted(filenames):
        # Extract the opinion type from the filename
        _, opinion_type = filename.replace(".txt", "").split("_")

        # Load the citing opinion & find the cite for the target case
        opinion = load_opinion(os.path.join(FOLDER, filename))

        # Extract the excerpts
        if use_full_opinion:
            soup = BeautifulSoup(opinion, features="lxml")
            excerpts = [clean_opinion(soup)]
        else:
            excerpts = extract_excerpts(opinion, cited_id)

        # Save the annotated opinions with opinion_type
        save_annotated_opinion(index, citing_id, cited_id, opinion_type, excerpts)


def process_row_wrapper(args):
    index, row = args
    try:
        process_row(index, row)
    except Exception as e:
        logging.error(f"Error processing index {index}: {e}")


def process_dataframe(df, max_workers=MAX_WORKERS):

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Use ThreadPoolExecutor to process rows in parallel
        _ = executor.map(lambda args: process_row_wrapper(args), df.iterrows())

    return df
