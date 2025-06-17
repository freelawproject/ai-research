import ast
import os
import re

from eyecite import clean_text

from courtlistener import get_opinions_in_cluster

FOLDER = "data/opinions/"
CLEAN_FOLDER = "data/clean_opinions/"

HTMLS = [
    "html_with_citations",
    "html_columbia",
    "html_lawbox",
    "xml_harvard",
    "html_anon_2020",
    "html",
]
SOURCES = HTMLS + ["plain_text"]


def save_opinions(opinion_id, court_id, docket_id, cluster_id):
    cluster = get_opinions_in_cluster(court_id, docket_id, cluster_id)

    opinion_sources = []
    opinion_types = []
    for result in cluster["results"]:
        opinion_type = result["type"]
        opinion_types.append(opinion_type)

        filename = f"{opinion_id}_{opinion_type}.txt"
        destination = os.path.join(FOLDER, filename)

        for source in SOURCES:
            opinion = result[source]
            if opinion:
                with open(destination, "w") as f:
                    f.write(opinion)
                opinion_sources.append(source)
                break

    print(f"Saved {len(opinion_types)} opinions for opinion: {opinion_id}")

    return opinion_types, opinion_sources


def save_opinions_df(df):
    for i, row in df.iterrows():
        opinion_types, opinion_sources = save_opinions(
            row["citing_opinion_id"], row["court"], row["docket_id"], row["cluster_id"]
        )
        df.at[i, "opinion_types"] = opinion_types
        df.at[i, "opinion_sources"] = opinion_sources

        if i % 10 == 0:
            print(f"Completed: {i}")

    return df


def clean_opinions(opinion_types, opinion_sources, opinion_id):
    word_counts = []

    for i, opinion_type in enumerate(opinion_types):
        opinion_source = opinion_sources[i]
        filename = f"{opinion_id}_{opinion_type}.txt"
        file_path = os.path.join(FOLDER, filename)

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                opinion = file.read()
        except FileNotFoundError:
            print(f"File not found: {file_path}")
            opinion = ""

        # Clean the opinion text
        if opinion_source in HTMLS:
            opinion = re.sub(r"^<\?xml.*?\?>", "", opinion, count=1)
            cleaned_opinion = clean_text(opinion, ["html", "all_whitespace"])
        else:
            cleaned_opinion = clean_text(opinion, ["all_whitespace"])

        # Save cleaned opinion to a new file
        cleaned_file_path = os.path.join(CLEAN_FOLDER, f"{filename}")
        with open(cleaned_file_path, "w") as cleaned_file:
            cleaned_file.write(cleaned_opinion)

        # Get words count
        word_counts.append(f"{len(cleaned_opinion.split())}")

    return word_counts


def clean_opinions_df(df):

    for i, row in df.iterrows():
        opinion_types, opinion_sources, opinion_id = (
            ast.literal_eval(row["opinion_types"]),
            ast.literal_eval(row["opinion_sources"]),
            row["citing_opinion_id"],
        )
        word_counts = clean_opinions(opinion_types, opinion_sources, opinion_id)
        combined_index = opinion_types.index("010combined")

        df.at[i, "word_counts"] = word_counts
        df.at[i, "combined_word_counts"] = int(word_counts[combined_index])

    return df
