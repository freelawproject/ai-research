import ast
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from courtlistener import get_case_details

from bs4 import BeautifulSoup
from nupunkt import sent_tokenize

MAX_WORKERS = 8

FOLDER = "data/opinions/"
CLEAN_FOLDER = "data/clean_opinions/"
ANNOTATE_FOLDER = "data/annotated_opinions/"
SENTENCES = 6

# Configure logging to output to console in real-time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def extract_excerpts(opinion, cited_id, sentences_before=SENTENCES, sentences_after=SENTENCES):
    
    # Annotate cited in the opinion text
    soup = BeautifulSoup(opinion, features="lxml")
    quotes = soup.find_all("span", attrs={"data-id": cited_id})
    # print("quotes: ", quotes)

    ref_names = []
    for quote in quotes:
        ref_names.append(quote.text)
        quote.string = f"<citedDecision>{quote.text}</citedDecision>"
    # print("ref_names: ", ref_names)

    #clean_opinion = clean_text(str(soup), ["html", "all_whitespace"])
    clean_opinion = soup.get_text(separator=" ", strip=True)
    
    # Split text into sentences
    sentences = sent_tokenize(clean_opinion)

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

    return extracted_excerpts, ref_names


def parse_row(row):
    return (
        ast.literal_eval(row["opinion_types"]),
        row["citing_opinion_id"],
        row["cited_opinion_id"],
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
    opinion_types, citing_id, cited_id = parse_row(row)
    ref_names = []

    case_name_short, case_name, case_name_full, cluster_id, citation_names = get_case_details(cited_id)

    for opinion_type in sorted(opinion_types):
        filename = f"{citing_id}_{opinion_type}.txt"

        # Load the original opinion & find the cite for the target case
        opinion = load_opinion(os.path.join(FOLDER, filename))
        
        # Extract the excerpts
        excerpts, cites = extract_excerpts(opinion, cited_id)
        ref_names.extend(list(set(cites)))

        if len(excerpts) == 0:
            excerpts = [load_opinion(os.path.join(CLEAN_FOLDER, filename))]
            ref_names.extend(list(set(["MISSING"])))

        # Save the annotated opinions with opinion_type
        save_annotated_opinion(index, citing_id, cited_id, opinion_type, excerpts)

    return ref_names, case_name_short, case_name, case_name_full, cluster_id, citation_names


def process_row_wrapper(args):
    index, row = args
    try:
        ref_names, case_name_short, case_name, case_name_full, cluster_id, citation_names = process_row(index, row)
        return index, ref_names, case_name_short, case_name, case_name_full, cluster_id, citation_names
    except Exception as e:
        logging.error(f"Error processing index {index}: {e}")
        return index, None, None, None, None, None, None


def process_dataframe(df, max_workers=MAX_WORKERS):

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Use ThreadPoolExecutor to process rows in parallel
        results = executor.map(
            lambda args: process_row_wrapper(args), df.iterrows()
        )

    for index, ref_names, case_name_short, case_name, case_name_full, cluster_id, citation_names in results:
        df.at[index, "cited_ref_names"] = f"{ref_names}"
        df.at[index, "cited_cluster_id"] = int(cluster_id)
        df.at[index, "cited_name_short"] = case_name_short
        df.at[index, "cited_name"] = case_name
        df.at[index, "cited_name_full"] = case_name_full
        df.at[index, "cited_citations"] = f"{citation_names}"
    
    df["cited_cluster_id"] = df["cited_cluster_id"].astype(int)

    return df
