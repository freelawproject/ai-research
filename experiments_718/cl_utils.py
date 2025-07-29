import re
import numpy as np
import pandas as pd


def clean_docket_numbers(raw_docket_str):
    # Normalize dashes: em dash, en dash → regular dash
    raw_docket_str = re.sub(r"[–—]", "-", raw_docket_str)

    # Normalize "Original"/"ORIG" → "Orig" with comma if missing
    raw_docket_str = re.sub(r"\b(Original|ORIG)\b", "Orig", raw_docket_str, flags=re.IGNORECASE)
    raw_docket_str = re.sub(r"(\d+)\s+Orig\b", r"\1, Orig", raw_docket_str)

    # Normalize "Misc"/"MISC" → "Misc" with comma if missing
    raw_docket_str = re.sub(r"\b(Misc|MISC)\b", "Misc", raw_docket_str, flags=re.IGNORECASE)
    raw_docket_str = re.sub(r"(\d+)\s+Misc\b", r"\1, Misc", raw_docket_str)

    # Normalize "No." and "Nos." (case-insensitive)
    cleaned = re.sub(r"\bNos?\.?\s*", "", raw_docket_str, flags=re.IGNORECASE).strip()

    # Normalize "and" (with or without comma) to comma
    cleaned = re.sub(r",?\s+and\s+", ", ", cleaned, flags=re.IGNORECASE)

    # Extract and remove parentheticals like (A-862)
    parentheticals = re.findall(r"\(([^)]+)\)", cleaned)
    cleaned = re.sub(r"\s*\([^)]+\)", "", cleaned)

    # Normalize spacing around hyphens
    cleaned = re.sub(r"(\d+)-\s+(\d+)", r"\1-\2", cleaned)

    # Split on semicolons first
    semi_parts = [p.strip().rstrip('.') for p in cleaned.split(';') if p.strip()]

    parts = []
    for part in semi_parts:
        # If it's like "number, Orig" or "number, Misc", preserve proper casing
        if re.match(r"^\d+,\s*(Orig|Misc)$", part):
            parts.append(part)
        else:
            # Otherwise, split on commas
            subparts = [s.strip() for s in part.split(',') if s.strip()]
            parts.extend(subparts)

    # Add cleaned parentheticals
    parentheticals = [p.strip().rstrip('.') for p in parentheticals]

    return parts + parentheticals


def get_docket_type(docket_number):
    docket_number = str(docket_number)
    if re.fullmatch(r"\d{2}A\d+", docket_number):
        return "A"
    elif re.fullmatch(r"\d{2}M\d+", docket_number):
        return "M"
    elif docket_number.lower().endswith("orig"):
        return "O"
    elif docket_number.lower().endswith("misc"):
        return "Misc"
    elif re.fullmatch(r"\d{2}[-–]\d+", docket_number):
        return "N"
    elif docket_number.isdigit():
        return "N"
    elif re.match(r"R\d{2}-", docket_number):
        return "R"
    else:
        return "OTHER"
