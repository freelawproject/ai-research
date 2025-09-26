import os
import json
import re

import numpy as np
import pandas as pd
pd.options.mode.chained_assignment = None


# def clean_docket_number(df):
#     # Apply the cleaning function to the docket_number_raw column
#     df[["docket_type", "docket_number"]] = df.apply(
#         lambda row: pd.Series(clean_docket_numbers(row["docket_number_raw"])), axis=1
#     )

#     # Explode the df for rows with multiple docket numbers
#     df = df.explode("docket_number").reset_index(drop=True)

#     # Add a column for the docket number format validation
#     df["docket_format_confirmed"] = df["docket_number"].apply(confirm_docket_format)
    
#     return df


def load_courts_to_df(courts, filename, path="cl_courts"):
    all_records = []

    for court in courts:
        court_filename = f"cl_{court}_dockets.json"
        with open(os.path.join(path, court_filename), "r") as f:
            data = json.load(f)
            # tag each record with the court
            for record in data:
                record["court_id"] = court
            all_records.extend(data)

    cl_df = pd.DataFrame(all_records)

    # Rename the docket_number column to docket_number_raw
    cl_df = cl_df.rename(columns={"docket_number": "docket_number_raw"})

    # Remove records without docket number
    cl_df = cl_df[(~cl_df["docket_number_raw"].isna()) & (cl_df["docket_number_raw"] != "") & (~cl_df["docket_number_raw"].isnull())]

    cl_df.to_csv(f"cl_csv/{filename}.csv", index=False)

    ## Clean the docket_number column
    #cl_df = clean_docket_number(cl_df)

    #cl_df.to_csv(f"{filename}_clean.csv", index=False)

    return cl_df

