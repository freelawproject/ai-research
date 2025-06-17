import ast
import os

SOURCE_FOLDER = "data/annotated_opinions/"
TARGET_FOLDER = "data/excerpts_v1/"


def combine_excerpts(df):

    for index, row in df.iterrows():
        citing_id = row["citing_opinion_id"]
        cited_id = row["cited_opinion_id"]
        opinion_types = ast.literal_eval(row["opinion_types"])

        # Sort opinion types and move '010combined' to the end
        if len(opinion_types) > 1:
            opinion_types = sorted(opinion_types)
            opinion_types.remove("010combined")
            opinion_types.append("010combined")

        combined_content = ""

        # Read and combine content in the sorted order
        for op in opinion_types:
            source_filename = f"{index + 1:04d}.{citing_id}_cites_{cited_id}_{op}.txt"
            source_filepath = os.path.join(SOURCE_FOLDER, source_filename)
            if os.path.exists(source_filepath):
                with open(source_filepath, "r") as f:
                    combined_content += f.read() + "\n"

        combined_content = combined_content.replace("<targetCase>", "").replace("</targetCase>", "").strip()

        # Construct the final filename and save the combined content
        target_filename = f"{index + 1:04d}.{citing_id}_cites_{cited_id}.txt"
        target_filepath = os.path.join(TARGET_FOLDER, target_filename)
        with open(target_filepath, "w") as f:
            f.write(combined_content.strip())

        df.at[index, "filename"] = target_filename

    return df
