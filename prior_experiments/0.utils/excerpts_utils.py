import ast
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup
from eyecite import annotate_citations, get_citations
from eyecite.find import extract_reference_citations
from eyecite.helpers import filter_citations
from nltk.tokenize import sent_tokenize

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


def preserve_tags(text):
    # Define punctuation characters used by sent_tokenize()
    punctuations = r"[.?!;:…]"
    placeholder = "<PUNCT_PLACEHOLDER>"

    # Replace all punctuation inside <targetCase> tags with placeholders
    def replace_punct(match):
        content = match.group(1)
        # Escape all punctuation inside the tag content
        return re.sub(punctuations, placeholder, content)

    text = re.sub(
        rf"(<targetCase>.*?</targetCase>)", replace_punct, text, flags=re.DOTALL
    )

    # Tokenize sentences
    sentences = sent_tokenize(text)

    # Revert placeholders to original punctuation
    sentences = [re.sub(placeholder, ".", sentence) for sentence in sentences]

    return sentences


def extract_excerpts(text, sentences_before=SENTENCES, sentences_after=SENTENCES):
    # Split text into sentences
    sentences = preserve_tags(text)
    # print("sentences", sentences)

    # Find all indices of sentences containing <targetCase> tags
    try:
        tag_indices = [
            i
            for i, sentence in enumerate(sentences)
            if re.search(r"<targetCase>.*?</targetCase>", sentence, re.DOTALL)
        ]
        # print("tag_indices", tag_indices)

        # Collect excerpts with the specified number of sentences before and after
        excerpts = [
            (
                max(0, index - sentences_before),
                min(len(sentences), index + sentences_after + 1),
            )
            for index in tag_indices
        ]
        # print("excerpts", excerpts)

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
        ast.literal_eval(row["opinion_types"]),
        row["citing_opinion_id"],
        row["cited_opinion_id"],
    )


def load_opinion(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


def get_cite_from_opinion(opinion, cited_id):
    soup = BeautifulSoup(opinion, features="lxml")
    quotes = soup.find_all("span", attrs={"data-id": cited_id})
    cleaned_quotes = list(set([quote.text.strip() for quote in quotes]))
    # print("cleaned_quotes", cleaned_quotes)
    return cleaned_quotes if cleaned_quotes else None


def process_citations(clean_opinion, opinion, cite):
    citations = get_citations(plain_text=clean_opinion, markup_text=opinion)
    # print("citations", citations)
    # print("cite", cite)
    # print([citation.matched_text() for citation in citations])
    targets = [citation for citation in citations if citation.matched_text() == cite]
    # print("-----")
    # print("target citations", targets)
    if targets:
        references = [
            item
            for target in targets
            for item in extract_reference_citations(
                target, plain_text=clean_opinion, markup_text=opinion
            )
        ]
    else:
        references = []
    return filter_citations(targets + references)


def eyecite_annotate(clean_opinion, all_citations):
    return annotate_citations(
        clean_opinion,
        annotations=[
            (cite.span(), "<targetCase>", "</targetCase>") for cite in all_citations
        ],
    )


def manual_annotate(text, cites):
    for cite in cites:
        text = text.replace(cite, f"<targetCase>{cite}</targetCase>")
        # escaped_cite = re.escape(cite)
        # print("escaped_cite:", escaped_cite)
        # text = re.sub(rf"\b{escaped_cite}\b", f"<targetCase>{cite}</targetCase>", text)
    return text


def manual_clean(text):
    cleaned_text = re.sub(r"\. ,", ".,", text)
    return cleaned_text


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


def process_row(index, row, manual_indices):
    opinion_types, citing_id, cited_id = parse_row(row)
    cite_names = []

    for opinion_type in sorted(opinion_types):
        filename = f"{citing_id}_{opinion_type}.txt"

        # Load the original opinion & find the cite for the target case
        opinion = load_opinion(os.path.join(FOLDER, filename))

        # Load the clean opinion
        clean_opinion = load_opinion(os.path.join(CLEAN_FOLDER, filename))

        # Get the cite for the target case
        if opinion_type == "010combined":
            cites = get_cite_from_opinion(opinion, cited_id)
        # print("---cites---", row.name, "--", cites)
        # print("opinion_type", opinion_type)

        # Process citations
        all_citations = [
            item
            for cite in cites
            for item in process_citations(clean_opinion, opinion, cite)
        ]
        # print("all_citations", all_citations)

        # Annotate the target case
        if len(all_citations) > 0:
            annotated_opinion = eyecite_annotate(clean_opinion, all_citations)
            cite_names.extend([each.matched_text() for each in all_citations])
            # print("cite_names", cite_names)
        else:
            # Try manual annotation
            annotated_opinion = manual_annotate(clean_opinion, cites)
            if "<targetCase>" not in annotated_opinion:
                cleaned_opinion = manual_clean(annotated_opinion)
                annotated_opinion = manual_annotate(cleaned_opinion, cites)

            cite_names.extend([each for each in cites])
            manual_indices.append(row.name)

        # Extract the excerpts
        excerpts = extract_excerpts(annotated_opinion)

        # Save the annotated opinions with opinion_type
        save_annotated_opinion(index, citing_id, cited_id, opinion_type, excerpts)

    return list(set(cite_names))


def process_row_wrapper(args, manual_indices):
    index, row = args
    try:
        cite_names = process_row(index, row, manual_indices)
        return index, cite_names
    except Exception as e:
        logging.error(f"Error processing index {index}: {e}")
        return index, None


def process_dataframe(df, max_workers=MAX_WORKERS):
    manual_indices = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Use ThreadPoolExecutor to process rows in parallel
        results = executor.map(
            lambda args: process_row_wrapper(args, manual_indices), df.iterrows()
        )

    # Update DataFrame with results
    for index, cite_names in results:
        if cite_names is not None:
            df.at[index, "cite_names"] = f"{cite_names}"

    return df, list(set(manual_indices))
