import logging
import os
import re

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Treatment severity ranking (lower = more negative)
# ---------------------------------------------------------------------------

TREATMENT_RANK = {
    # Direct History — most to least severe
    "Reversed by": 0,
    "Reversed and remanded by": 1,
    "Vacated and remanded by": 2,
    "Vacated by": 3,
    # Citing Reference — most to least severe
    "Overruled by": 4,
    "Abrogated by": 5,
    "Questioned by": 6,
    # Direct History continued
    "Affirmed in part; Reversed in part by": 7,
    "Affirmed in part; Vacated in part by": 8,
    # Citing Reference continued
    "Disapproved by": 9,
    "Limited by": 10,
    # Direct History continued
    "Remanded by": 11,
    "Cert. granted by": 12,
    # Citing Reference continued
    "Criticized by": 13,
    "Distinguished by": 14,
    "Declined to follow by": 15,
    # Direct History — neutral
    "Dismissed by": 16,
    "Affirmed by": 17,
    "Cert. denied by": 18,
    # Citing Reference — neutral
    "Cited by": 19,
}

# For "as recognized by" variants: same rank as the base treatment
_MAX_RANK = max(TREATMENT_RANK.values()) + 1


def _get_treatment_rank(treatment):
    """Get numeric rank for a treatment. Lower = more negative."""
    if not treatment or pd.isna(treatment):
        return _MAX_RANK
    # "Overruled as recognized by" → "Overruled by"
    base = treatment.replace(" as recognized by", " by").strip() if " as recognized by" in treatment else treatment
    return TREATMENT_RANK.get(base, _MAX_RANK)


# ---------------------------------------------------------------------------
# Step 0: Clean up common treatment formatting errors from model output
# ---------------------------------------------------------------------------

def clean_treatments(parsed_df):
    """Fix common treatment string errors from model output:
    1. 'Cited as recognized by' → 'Cited by' (not a valid treatment).
    2. '... by recognized by' → '... as recognized by' (malformed modifier).
    """
    df = parsed_df.copy()
    treatment = df["treatment"].fillna("")

    # "Cited as recognized by" → "Cited by"
    cited_arb_mask = treatment.str.strip().str.lower() == "cited as recognized by"
    n_cited = cited_arb_mask.sum()
    df.loc[cited_arb_mask, "treatment"] = "Cited by"

    # "... by as recognized by" → "... as recognized by"
    by_as_recog_mask = treatment.str.contains(" by as recognized by", case=False, na=False)
    n_by_as_recog = by_as_recog_mask.sum()
    df.loc[by_as_recog_mask, "treatment"] = (
        df.loc[by_as_recog_mask, "treatment"]
        .str.replace(" by as recognized by", " as recognized by", case=False, regex=False)
    )

    if n_cited or n_by_as_recog:
        logger.info(
            f"Treatment cleanup: {n_cited} 'Cited as recognized by' → 'Cited by', "
            f"{n_by_as_recog} 'by as recognized by' → 'as recognized by'"
        )
    return df


# ---------------------------------------------------------------------------
# Step 1: Deduplicate cited cases per opinion — keep most negative treatment
# ---------------------------------------------------------------------------

def deduplicate_cited_cases(parsed_df):
    """For each (citing_cluster_id, mainCitationString), keep the row with
    the most negative treatment (lowest rank). Rows without a citation string
    are kept as-is (caseName is not a reliable dedup key)."""
    df = parsed_df.copy()
    df["_rank"] = df["treatment"].apply(_get_treatment_rank)

    # Deduplicate on citation string (non-null)
    has_citation = df[df["mainCitationString"].notna() & (df["mainCitationString"] != "")]
    no_citation = df[~df.index.isin(has_citation.index)]

    deduped_citation = (
        has_citation.sort_values("_rank")
        .drop_duplicates(subset=["citing_cluster_id", "mainCitationString"], keep="first")
    )

    result = pd.concat([deduped_citation, no_citation], ignore_index=True)
    result = result.drop(columns=["_rank"])

    n_removed = len(parsed_df) - len(result)
    if n_removed > 0:
        logger.info(f"Dedup: removed {n_removed} duplicate cited cases, {len(result)} remaining")
    return result


# ---------------------------------------------------------------------------
# Step 2: Keyword check on "Cited by" results + model re-evaluation
# ---------------------------------------------------------------------------

NEGATIVE_KEYWORDS = [
    # Overruling / abrogation
    r"overrul", r"abrogat", r"supersed", r"no longer good law",
    r"no longer valid", r"no longer control",
    # Reversal / vacatur
    r"revers", r"vacat", r"set aside", r"struck down",
    # Questioning / doubting
    r"question", r"cast doubt", r"called into question",
    r"undermin", r"erode", r"weaken",
    # Disapproval / criticism
    r"disapprov", r"criticiz", r"criticized", r"erroneous",
    r"incorrect", r"wrong", r"flawed", r"misguided",
    r"unpersuasive", r"not persuasive", r"poorly reasoned",
    # Distinguishing / limiting
    r"distinguish", r"inapplicable", r"not applicable",
    r"does not apply", r"do not apply", r"not control",
    r"not binding", r"not extend", r"decline.? to extend",
    r"decline.? to follow", r"not follow", r"refuse.? to follow",
    r"narrow", r"limit",
    # Disagreement / departure
    r"disagree", r"depart", r"reject", r"repudiat",
    r"contrary", r"conflict", r"inconsistent", r"tension",
    r"at odds", r"incompatible", r"irreconcilable",
    # Cert actions
    r"cert\S*\s+grant", r"cert\S*\s+denied", r"certiorari",
    # Modification
    r"modif", r"amended", r"remand",
    # Affirmance in part (signals partial reversal)
    r"affirm\w*\s+in\s+part",
]

_KEYWORD_PATTERN = re.compile("|".join(NEGATIVE_KEYWORDS), re.IGNORECASE)


def flag_for_review(parsed_df):
    """Identify results that should be re-evaluated:
    1. 'Cited by' results whose quote or rationale contains keywords suggesting
       a possible negative treatment.
    2. All 'as recognized by' treatments (directionality is frequently wrong).
    3. All 'Reversed by' treatments (often confused with 'Reversed and remanded by').
    4. All 'Vacated by' treatments (often confused with 'Vacated and remanded by').

    Returns (flagged_df, unflagged_df) where flagged_df has the rows
    to re-evaluate and unflagged_df has the clean rows.
    """
    treatment_col = parsed_df["treatment"].fillna("").str.strip()

    # Flag 1: "Cited by" with suspicious keywords
    cited_by_mask = treatment_col.str.lower() == "cited by"
    cited_by = parsed_df[cited_by_mask].copy()

    def has_keywords(row):
        text = f"{row.get('quote', '')} {row.get('rationale', '')}"
        return bool(_KEYWORD_PATTERN.search(str(text)))

    keyword_flagged_mask = cited_by.apply(has_keywords, axis=1)
    keyword_flagged = cited_by[keyword_flagged_mask]

    # Flag 2: All "as recognized by" treatments
    as_recognized_mask = treatment_col.str.contains("as recognized by", case=False, na=False)
    as_recognized = parsed_df[as_recognized_mask]

    # Flag 3: All "Reversed by" treatments
    reversed_mask = treatment_col.str.lower() == "reversed by"
    reversed_cases = parsed_df[reversed_mask]

    # Flag 4: All "Vacated by" treatments (v404: also check for missed remand)
    vacated_mask = treatment_col.str.lower() == "vacated by"
    vacated_cases = parsed_df[vacated_mask]

    # Combine flagged rows (deduplicate in case of overlap)
    flagged_indices = (
        keyword_flagged.index
        .union(as_recognized.index)
        .union(reversed_cases.index)
        .union(vacated_cases.index)
    )
    flagged = parsed_df.loc[flagged_indices].copy()
    unflagged = parsed_df.loc[~parsed_df.index.isin(flagged_indices)].copy()

    n_kw = len(keyword_flagged)
    n_ar = len(as_recognized)
    n_rev = len(reversed_cases)
    n_vac = len(vacated_cases)
    logger.info(
        f"Flagged for review: {len(flagged)} total — "
        f"{n_kw} 'Cited by' with keywords, {n_ar} 'as recognized by', "
        f"{n_rev} 'Reversed by', {n_vac} 'Vacated by'"
    )

    return flagged, unflagged


# ---------------------------------------------------------------------------
# Step 3: Final deduplication — keep most negative per cited case per opinion
# ---------------------------------------------------------------------------

def final_deduplicate(parsed_df):
    """Final pass: for each (citing_cluster_id, mainCitationString), keep only
    the most negative treatment. Same as deduplicate_cited_cases but applied
    after re-evaluation to catch any new duplicates."""
    return deduplicate_cited_cases(parsed_df)


# ---------------------------------------------------------------------------
# Step 4: Merge results to labels
# ---------------------------------------------------------------------------

def _normalize_citation(s):
    """Normalize a citation string for matching by removing internal spaces
    within reporter abbreviations. E.g., 'F. Supp. 3d' → 'F.Supp.3d',
    '814 F. Supp. 850' → '814 F.Supp. 850'.

    Handles patterns like 'F. 2d', 'F. Supp.', 'S. Ct.', 'L. Ed. 2d', etc.
    """
    if not s or pd.isna(s):
        return s
    # Remove spaces after periods that are followed by a letter or digit
    # (i.e., within abbreviation components), but not after the final period
    # before a page number. We do this by collapsing ". " to "." only when
    # followed by an uppercase letter, lowercase letter, or digit that is
    # part of the reporter (not a standalone page number).
    # Strategy: normalize ". X" → ".X" where X is an abbreviation component
    # (starts with uppercase letter) or an edition number (digit followed by 'd'/'th').
    s = str(s)
    # Collapse ". " within reporter names: ". " followed by a letter
    s = re.sub(r'\.\s+(?=[A-Za-z])', '.', s)
    # Collapse ". " before edition numbers like "2d", "3d"
    s = re.sub(r'\.\s+(?=\d+[a-z])', '.', s)
    return s


def merge_to_labels(parsed_df, labels_df, output_dir):
    """Merge model predictions to authority labels on citation string.

    labels_df should have: citing_cluster_id, cited_cluster_id,
    cited_case_name, cited_case_citations (comma-separated citation strings).

    Saves matched and unmatched results to output_dir.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Ensure consistent dtype before merging
    parsed_df = parsed_df.copy()
    labels_df = labels_df.copy()
    parsed_df["citing_cluster_id"] = parsed_df["citing_cluster_id"].astype(int)
    labels_df["citing_cluster_id"] = labels_df["citing_cluster_id"].astype(int)

    # Merge on citing_cluster_id
    merged = parsed_df.merge(labels_df, on="citing_cluster_id", how="left", suffixes=("_pred", "_label"))

    # Match on mainCitationString appearing in cited_case_citations
    # Normalize both sides to handle spacing differences in reporter names
    # (e.g., model returns "F.Supp." but labels have "F. Supp.")
    def _citations_match(row):
        citation = row.get("mainCitationString")
        label_citations = row.get("cited_case_citations")
        if pd.isna(citation) or pd.isna(label_citations):
            return False
        norm_citation = _normalize_citation(citation)
        norm_labels = _normalize_citation(str(label_citations))
        return norm_citation in norm_labels

    mask = merged.apply(_citations_match, axis=1)
    matched = merged[mask]

    # Model results matched to labels
    matched_results = matched[
        ["citing_cluster_id", "mainCitationString", "caseName", "caseHistory",
         "treatment", "opinionType", "quote", "rationale",
         "cited_cluster_id", "cited_case_citations", "cited_case_name"]
    ].drop_duplicates()
    matched_results.to_csv(os.path.join(output_dir, "matched_results.csv"), index=False)
    logger.info(f"Matched results: {len(matched_results)} rows → {output_dir}/matched_results.csv")

    # Model predictions not found in labels
    parsed_keys = parsed_df[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    matched_keys = matched[["citing_cluster_id", "mainCitationString"]].drop_duplicates()
    label_missed_keys = parsed_keys.merge(
        matched_keys, on=["citing_cluster_id", "mainCitationString"], how="left", indicator=True
    )
    label_missed_keys = label_missed_keys[label_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    label_missed = parsed_df.merge(label_missed_keys, on=["citing_cluster_id", "mainCitationString"], how="inner")
    label_missed.to_csv(os.path.join(output_dir, "label_missed.csv"), index=False)
    logger.info(f"Model predictions not in labels: {len(label_missed)} rows → {output_dir}/label_missed.csv")

    # Labels not found in model predictions — only for citing cluster IDs we analyzed
    analyzed_ids = parsed_df["citing_cluster_id"].unique()
    labels_df = labels_df[labels_df["citing_cluster_id"].isin(analyzed_ids)]
    label_keys = labels_df[["citing_cluster_id", "cited_cluster_id"]].drop_duplicates()
    matched_label_keys = matched[["citing_cluster_id", "cited_cluster_id"]].drop_duplicates()
    model_missed_keys = label_keys.merge(
        matched_label_keys, on=["citing_cluster_id", "cited_cluster_id"], how="left", indicator=True
    )
    model_missed_keys = model_missed_keys[model_missed_keys["_merge"] == "left_only"].drop(columns=["_merge"])
    model_missed = labels_df.merge(model_missed_keys, on=["citing_cluster_id", "cited_cluster_id"], how="inner")
    model_missed.to_csv(os.path.join(output_dir, "model_missed.csv"), index=False)
    logger.info(f"Labels missed by model: {len(model_missed)} rows → {output_dir}/model_missed.csv")

    return matched_results
