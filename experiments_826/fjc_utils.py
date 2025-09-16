import re

import numpy as np
import pandas as pd
pd.options.mode.chained_assignment = None

from fjc_codes import *


def load_fjc_data(old_filepath="ap71to07.txt", new_filepath="ap08on.txt"):
    # Load the two sources of data and combine to fjc data
    old_df = pd.read_csv(old_filepath, sep="\t", dtype=str)
    old_df["SOURCE"] = "1971-2007"

    new_df = pd.read_csv(new_filepath, sep="\t", dtype=str)
    new_df["SOURCE"] = "2008-2025"
    new_df["NEWSYTRM"] = None  # Add missing column to new data

    fjc_df = pd.concat([old_df, new_df], ignore_index=True)
    fjc_df = fjc_df[COLUMNS.keys()]

    print(f"Initial FJC data len: {len(fjc_df)}")

    return fjc_df


def clean_df(df):
    # remove rows with missing values in key columns
    cols = ["CIRCUIT", "DOCKET", "DDIST", "DDOCKET"]
    df = df[(df[cols]!="-8").all(axis=1)]

    # Cast int columns to int type
    int_cols = ["CIRCUIT", "APPTYPE", "DISP", "OUTCOME", "PROCTERM", "METHOD", "PUBSTAT", "NEWSYTRM"]
    for col in int_cols:
        df[col] = df[col].fillna(-8)  # fill NaN with -8 for conversion
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Cast date columns to datetime
    date_cols = ["DKTDATE", "DDKTDATE", "APPDATE", "JUDGDATE"]
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], format="%m/%d/%Y", errors="coerce")
    
    return df


def clean_docket_number(docket: str) -> str:
    """
    Clean a U.S. Court of Appeals docket number into standard yy-nnnn or yy-nnnnn format.
    
    Rules:
    - Modern format: YY-NNNN (e.g., 17-1234)
    - Some dockets are stored as YY0NNNN (7-digit). Convert to YY-NNNN (e.g., 1701234 → 17-1234).
    - Some dockets use YYNNNNN (7-digit with 5-digit serial). Convert to YY-NNNNN (e.g., 1910001 → 19-10001).
    - Preserve serial number length if 5 digits.
    """
    if docket is None:
        return None

    docket = str(docket).strip()

    # Already in correct format
    if re.match(r"^\d{2}-\d{4,5}$", docket):
        return docket

    # Case: 7-digit with inserted 0 (YY0NNNN) or without inserted 0 (YYNNNNN)
    if re.match(r"^\d{7}$", docket):
        year = docket[:2]
        mid = docket[2]
        serial = docket[3:]
        if mid == "0":
            return f"{year}-{serial}"  # e.g., 1701234 -> 17-1234
        else:
            return f"{year}-{docket[2:]}"  # e.g., 1910001 -> 19-10001

    # Case: 6-digit standard style (YYNNNN)
    if re.match(r"^\d{6}$", docket):
        return f"{docket[:2]}-{docket[2:]}"
    
    # Fallback: return as-is
    return docket


def fix_pre2000_docket(dkt_year, docket_clean):
    """
    If DKTYEAR < 2000 and DOCKET_CLEAN starts with '00-', 
    remove '00-' and keep only the digits after the dash.
    """
    if pd.isnull(dkt_year) or pd.isnull(docket_clean):
        return docket_clean
    if dkt_year < 2000 and str(docket_clean).startswith("00-"):
        return str(docket_clean).split("-", 1)[1]
    return docket_clean


def get_disp_unified(row):
    disp_type = DISP_MAP.get(row["DISP"], None)
    if disp_type == "OUTCOME":
        return row.get("OUTCOME_STR", "Missing")
    elif disp_type == "PROCTERM":
        return row.get("PROCTERM_STR", "Missing")
    elif disp_type == "METHOD":
        return row.get("METHOD_STR", "Missing")
    else:
        return "Missing"


def parse_fjc(old_filepath="ap71to07.txt", new_filepath="ap08on.txt"):
    # Load the FJC data
    fjc_df = load_fjc_data(old_filepath, new_filepath)

    # Clean the dataframe
    fjc_df = clean_df(fjc_df)

    # Get the DKTYEAR
    fjc_df["DOCKET_YEAR"] = fjc_df["DKTDATE"].dt.year

    # For missing NEWSYTRM (which is -8), fill with DOCKET_YEAR as approximation for NEWSYTRM
    fjc_df["NEWSYTRM"] = fjc_df["NEWSYTRM"].mask(fjc_df["NEWSYTRM"] == -8, fjc_df["DOCKET_YEAR"])

    # Clean DOCKET number column & fix pre-2000 docket numbers
    fjc_df["DOCKET_NUM"] = fjc_df["DOCKET"].apply(clean_docket_number)
    fjc_df["DOCKET_NUM"] = fjc_df.apply(
        lambda row: fix_pre2000_docket(row["DOCKET_YEAR"], row["DOCKET_NUM"]), axis=1
    )
    
    # Get CIRCUIT_STR, which is the CL Circuit Court
    fjc_df["CIRCUIT_STR"] = fjc_df["CIRCUIT"].map(CIRCUIT_MAP)

    # Get DDIST_STR, which is the CL District Court
    fjc_df = fjc_df[fjc_df["DDIST"] != "1111"] # Remove test district
    fjc_df["DDIST_STR"] = (
    fjc_df["DDIST"].map(DDIST_MAP_OLD)
    .fillna(fjc_df["DDIST"].map(DDIST_MAP_NEW))
    )

    # Get the APPTYPE_STR, for natural language description of APPTYPE
    fjc_df["APPTYPE_STR"] = fjc_df["APPTYPE"].map(APPTYPE_MAP)

    # Get the DISP_STR, for natural language description of DISP
    fjc_df["DISP_STR"] = fjc_df["DISP"].map(DISP_MAP_NEW)

    # Get the OUTCOME_STR, for natural language description of OUTCOME
    fjc_df["OUTCOME_STR"] = fjc_df["OUTCOME"].map(OUTCOME_MAP)

    # Get the PROCTERM_STR, for natural language description of PROCTERM, with separate mapping by year
    fjc_df["PROCTERM_STR"] = np.where(
        fjc_df["DKTDATE"] < pd.Timestamp("1976-01-01"),
        fjc_df["PROCTERM"].map(PROCTERM_MAP_71_DEC_75),
        np.where(
            (fjc_df["DKTDATE"] >= pd.Timestamp("1976-01-01")) & (fjc_df["NEWSYTRM"] <= 1984),
            fjc_df["PROCTERM"].map(PROCTERM_MAP_JAN_76_84),
            fjc_df["PROCTERM"].map(PROCTERM_MAP_NEW),
        )
    )

    # Get the METHOD_STR, for natural language description of METHOD, with separate mapping by year
    fjc_df["METHOD_STR"] = np.where(
        fjc_df["NEWSYTRM"] <= 1984,
        fjc_df["METHOD"].map(METHOD_MAP_71_84),
        fjc_df["METHOD"].map(METHOD_MAP_NEW),
    )

    # Get the PUBSTAT_STR, for natural language description of PUBSTAT
    fjc_df["PUBSTAT_STR"] = fjc_df["PUBSTAT"].map(PUBSTAT_MAP_NEW)

    # Get unified disposition description DISP_UNIFIED
    fjc_df["DISP_UNIFIED"] = fjc_df.apply(get_disp_unified, axis=1)

    print(f"Cleaned FJC data len: {len(fjc_df)}")

    fjc_df.to_csv("fjc_cleaned.csv", index=False)

    return fjc_df