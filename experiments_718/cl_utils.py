import re
import numpy as np
import pandas as pd


def clean_docket_numbers(raw_docket_str):
    # Normalize "Original" and "ORIG" to "Orig" (case-insensitive), insert comma if needed
    raw_docket_str = re.sub(r"\b(Original|ORIG)\b", "Orig", raw_docket_str, flags=re.IGNORECASE)
    raw_docket_str = re.sub(r"(\d+)\s+Orig\b", r"\1, Orig", raw_docket_str)

    # Normalize "No." and "Nos." (case-insensitive)
    cleaned = re.sub(r"\bNos?\.?\s*", "", raw_docket_str, flags=re.IGNORECASE).strip()

    # Normalize "and" (with optional comma) to comma
    cleaned = re.sub(r",?\s+and\s+", ", ", cleaned, flags=re.IGNORECASE)

    # Extract and remove parentheticals, e.g., (A-862)
    parentheticals = re.findall(r"\(([^)]+)\)", cleaned)
    cleaned = re.sub(r"\s*\([^)]+\)", "", cleaned)

    # Normalize hyphen spacing: "78- 78" -> "78-78"
    cleaned = re.sub(r"(\d+)-\s+(\d+)", r"\1-\2", cleaned)

    # Split on semicolons first
    semi_parts = [p.strip().rstrip('.') for p in cleaned.split(';') if p.strip()]

    parts = []
    for part in semi_parts:
        # Preserve known multi-part docket formats like "13, Orig" or "871, Misc"
        if re.match(r"^\d+,\s*(Orig|Misc)$", part, re.IGNORECASE):
            parts.append(part)
        else:
            # Otherwise split on commas
            subparts = [s.strip() for s in part.split(',') if s.strip()]
            parts.extend(subparts)

    # Clean parentheticals
    parentheticals = [p.strip().rstrip('.') for p in parentheticals]

    return parts + parentheticals


def get_docket_type(docket_number):
    if re.fullmatch(r"\d{2}A\d+", docket_number):
        return "A"
    elif re.fullmatch(r"\d{2}M\d+", docket_number):
        return "M"
    elif docket_number.endswith("Orig"):
        return "O"
    elif docket_number.endswith("Misc"):
        return "Misc"
    elif re.fullmatch(r"\d{2}[-–]\d+", docket_number):
        return "N"
    elif docket_number.isdigit():
        return "N"
    elif docket_number.startswith("A-"):
        return "A"
    elif docket_number.startswith("D-"):
        return "D"
    elif re.match(r"R\d{2}-", docket_number):
        return "R"
    else:
        return "OTHER"




def clean_and_split_docket_numbers(docket_str):
    if pd.isna(docket_str):
        return []

    # Remove dots
    docket_str = docket_str.replace("No. ", "").replace("No.", "").replace(".", "").replace("- ", "-").replace("No ", "")

    # Handle semicolon-separated values
    parts = [part.strip() for part in docket_str.split(";")]

    final_parts = []
    for part in parts:
        # If it has parentheses like '21-588 (21A85)'
        if "(" in part and ")" in part:
            match = re.match(r"^(\S+)\s+\(([^)]+)\)$", part)
            if match:
                final_parts.extend([match.group(1), match.group(2)])
            else:
                final_parts.append(part)
        else:
            final_parts.append(part)

    return final_parts


def extract_docket_info(docket_str):
    if pd.isna(docket_str):
        return {"docket_type": None, "year": None, "case_num": None}

    docket_str = str(docket_str).strip()

    if "-" in docket_str:
        parts = docket_str.split("-", 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return {
                "docket_type": "N",
                "year": parts[0],
                "case_num": int(parts[1])
            }

    elif "Orig" in docket_str:
            return {
                "docket_type": "O",
                "year": None,
                "case_num": docket_str
            }
    
    elif "Misc" in docket_str:
            return {
                "docket_type": "M",
                "year": None,
                "case_num": docket_str
            }
        
    elif "A" in docket_str:
        parts = docket_str.split("A", 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return {
                "docket_type": "A",
                "year": parts[0],
                "case_num": int(parts[1])
            }
        
    elif "AM" in docket_str:
        parts = docket_str.split("M", 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return {
                "docket_type": "M",
                "year": parts[0],
                "case_num": int(parts[1])
            }

    elif docket_str.isdigit():
        return {
            "docket_type": "N",
            "year": None,
            "case_num": int(docket_str)
        }

    return {
        "docket_type": None,
        "year": None,
        "case_num": None
    }