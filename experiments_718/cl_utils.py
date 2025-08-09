import re
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer


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


def clean_scotus_lower_case_nums(raw_case_str):
    # Remove 'et al.' (case-insensitive, with or without trailing period and whitespace)
    s = re.sub(r'et\s+al\.?', '', raw_case_str, flags=re.IGNORECASE)
    
    # Remove 'No.' (case-insensitive, with or without trailing period and optional whitespace after)
    s = re.sub(r'\bNo\.?\b', '', s, flags=re.IGNORECASE)

    # Strip leading and trailing whitespace
    return s.strip()


def clean_docket_numbers_fed_appellate(raw_docket_str):
    # Remove "Docket" (case-insensitive)
    cleaned = re.sub(r"\bDocket\b\s*", "", raw_docket_str, flags=re.IGNORECASE).strip()
    
    # Remove "Civil Action No.", "Civil No.", "Action No.", or "No." (case-insensitive)
    cleaned = re.sub(r"\b(Civil\s*)?(Action\s*)?Nos?\.?\s*", "", cleaned, flags=re.IGNORECASE).strip()

    # Remove "Appeal" or "Appeal No."
    cleaned = re.sub(r"\bAppeal\s*(No\.?)?\s*", "", cleaned, flags=re.IGNORECASE).strip()

    # Remove anything before 'Civil ' if still present
    cleaned = re.sub(r".*?Civil\s+", "", cleaned)

    # Remove commas used as thousands separators
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", cleaned)

    # Split on semicolon first
    semi_parts = [p.strip() for p in cleaned.split(';') if p.strip()]

    # Split on commas unless preceded by the word "Term"
    final_parts = []
    for part in semi_parts:
        buffer = ""
        tokens = re.split(r'(,)', part)  # keep commas
        for i in range(len(tokens)):
            token = tokens[i]
            if token == ',':
                last_word = buffer.strip().split()[-1] if buffer.strip() else ""
                if last_word == "Term":
                    buffer += token  # don't split
                else:
                    final_parts.append(buffer.strip())
                    buffer = ""
            else:
                buffer += token
        if buffer.strip():
            final_parts.append(buffer.strip())

    return final_parts


def clean_docket_numbers_lower(raw_docket_str):
    # 1. Remove anything starting from "remanded" (case-insensitive)
    cleaned_input = re.split(r"\bremanded\b", raw_docket_str, flags=re.IGNORECASE)[0].strip()

    # Month names & abbreviations
    month_pattern = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|" \
                    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|" \
                    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

    # 2. Extract numbers tied to "[No.] X, <Month> Term, YYYY"
    term_pattern = re.findall(
        rf"(?:No\.?\s*)?(\d+)\s*,\s*{month_pattern}\.?\s+Term\s*,\s*\d{{4}}",
        cleaned_input,
        flags=re.IGNORECASE
    )
    if term_pattern:
        return term_pattern

    # 3. Special case: Gen. numbers → remove thousand separators
    if re.search(r"\bGen\.?", cleaned_input, flags=re.IGNORECASE):
        cleaned_input = re.sub(r"(\d),(\d)", r"\1\2", cleaned_input)

    # 4. Remove known prefixes
    cleaned = re.sub(r"\bDocket\b\s*", "", cleaned_input, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(Civ\.?\s*)?(Civil\s*)?(Action\s*)?Nos?\.?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bAppeals?,?\s*(Nos?\.?)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSC\b\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMisc\.?\s+Docket\s+[A-Z]*\s*Nos?\.?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bGen\.?\s*(Nos?\.?)?\s*", "", cleaned, flags=re.IGNORECASE)

    # 5. Normalize whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 6. Split on semicolon, comma, or 'and'
    parts = re.split(r"\s*(?:;|,|and)\s*", cleaned, flags=re.IGNORECASE)

    # 7. Keep only the numeric/alphanumeric docket parts (strip trailing words & punctuation)
    final_parts = [re.match(r"([A-Za-z0-9]+)", p).group(1) for p in parts if re.match(r"[A-Za-z0-9]+", p)]

    return final_parts


def extract_plaintiff(name):
    """Extract the part of the case name before 'v.', 'vs.', or 'versus'."""
    if not isinstance(name, str) or not name.strip():
        return ""
    # Look for common 'v.' patterns, case insensitive
    match = re.split(r'\s+(?:v\.|vs\.|versus)\s+', name, flags=re.IGNORECASE)
    return match[0].strip() if match else name.strip()


def ngram_jaccard_similarity(str1, str2, n=5):
    """Calculate Jaccard similarity based on n-grams between two strings."""
    if (not str1.strip()) and (not str2.strip()):
        return 0
    if (not str1.strip()) or (not str2.strip()):
        return 1
    vectorizer = CountVectorizer(analyzer='char', ngram_range=(1,n))
    ngrams = vectorizer.fit_transform([str1, str2]).toarray()
    # Jaccard similarity = intersection / union for binary vectors
    intersection = np.minimum(ngrams[0], ngrams[1]).sum()
    union = np.maximum(ngrams[0], ngrams[1]).sum()
    if union == 0:
        return 0
    return intersection / union


def tag_low_ngram_overlap(df, court_col='cl_lower_court_id', docket_col='cl_lower_court_docket_number_clean', case_col='cl_lower_court_case_name', threshold=0.8):
    df = df.copy()
    df['low_ngram_overlap'] = False
    
    grouped = df.groupby([court_col, docket_col])
    
    for (court, docket), group in grouped:
        names = group[case_col].tolist()
        plaintiffs = [extract_plaintiff(name) for name in names]
        indices = group.index.tolist()
        
        if len(plaintiffs) < 2:
            continue
        
        for i in range(len(plaintiffs)):
            for j in range(i+1, len(plaintiffs)):
                sim = ngram_jaccard_similarity(plaintiffs[i], plaintiffs[j])
                if sim <= threshold:
                    df.at[indices[i], 'low_ngram_overlap'] = True
                    df.at[indices[j], 'low_ngram_overlap'] = True
                    
    return df