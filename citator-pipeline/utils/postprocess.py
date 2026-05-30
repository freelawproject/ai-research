import logging
import os
import re

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical severity + direction mappings
# (Single source of truth — eval_utils.py imports these.)
# ---------------------------------------------------------------------------

severity_mapping = {
    "Reversed by": "Stop",
    "Reversed and remanded by": "Stop",
    "Vacated and remanded by": "Stop",
    "Vacated by": "Stop",
    "Overruled by": "Stop",
    "Abrogated by": "Stop",
    "Questioned by": "Stop",
    "Reversed in part; Vacated in part by": "Warning",
    "Affirmed in part; Reversed in part by": "Warning",
    "Affirmed in part; Vacated in part by": "Warning",
    "Disapproved by": "Warning",
    "Limited by": "Warning",
    "Modified by": "Caution",
    "Remanded by": "Caution",
    "Cert. granted by": "Caution",
    "Criticized by": "Caution",
    "Distinguished by": "Caution",
    "Declined to follow by": "Caution",
    "Dismissed by": "Neutral",
    "Cert. denied by": "Neutral",
    "Affirmed by": "Neutral",
    "Cited by": "Neutral",
    "Unknown": "Neutral",
}

direction_mapping = {
    "Reversed by": "Direct History",
    "Reversed and remanded by": "Direct History",
    "Vacated and remanded by": "Direct History",
    "Vacated by": "Direct History",
    "Overruled by": "Citing Reference",
    "Abrogated by": "Citing Reference",
    "Questioned by": "Citing Reference",
    "Reversed in part; Vacated in part by": "Direct History",
    "Affirmed in part; Reversed in part by": "Direct History",
    "Affirmed in part; Vacated in part by": "Direct History",
    "Disapproved by": "Citing Reference",
    "Limited by": "Citing Reference",
    "Modified by": "Direct History",
    "Remanded by": "Direct History",
    "Cert. granted by": "Direct History",
    "Criticized by": "Citing Reference",
    "Distinguished by": "Citing Reference",
    "Declined to follow by": "Citing Reference",
    "Dismissed by": "Direct History",
    "Cert. denied by": "Direct History",
    "Affirmed by": "Direct History",
    "Cited by": "Citing Reference",
    "Unknown": "Citing Reference",
}

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
    # Direct History continued — Warning tier (partial-disposition composites)
    "Reversed in part; Vacated in part by": 7,
    "Affirmed in part; Reversed in part by": 8,
    "Affirmed in part; Vacated in part by": 9,
    # Citing Reference continued
    "Disapproved by": 10,
    "Limited by": 11,
    # Direct History continued — Caution tier
    "Modified by": 12,
    "Remanded by": 13,
    "Cert. granted by": 14,
    # Citing Reference continued
    "Criticized by": 15,
    "Distinguished by": 16,
    "Declined to follow by": 17,
    # Direct History — neutral
    "Dismissed by": 18,
    "Cert. denied by": 19,
    "Affirmed by": 20,
    # Citing Reference — neutral
    "Cited by": 21,
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
# Step 0a: Remove non-case citations (statutes, regulations, law reviews)
# ---------------------------------------------------------------------------

# Patterns that indicate a citation is not a court case
_NON_CASE_PATTERNS = re.compile(
    r"§"                        # Section symbol (statutes)
    r"|L\.\s*Rev"               # Law reviews (L. Rev, L.Rev)
    r"|Stat\."                  # Statutes at Large
    r"|Fed\.\s*Reg"             # Federal Register
    r"|C\.?\s*F\.?\s*R"         # CFR, C.F.R.
    r"|^Pub\.\s*L"              # Public Laws
    r"|U\.\s*S\.\s*C"           # United States Code (U.S.C., U. S. C.)
    r"|Ann\.\s*(?:Code|Stat)"   # Annotated codes/statutes
    r"|Cong\.\s*Rec"            # Congressional Record
    r"|Exec\.\s*Order"          # Executive Orders
    r"|Fed\.\s*R\.\s*(?:Civ|Crim|Evid|App)"  # Federal Rules (Civil/Criminal/Evidence/Appellate Procedure)
    , re.IGNORECASE
)


def filter_non_case_citations(parsed_df):
    """Remove rows where mainCitationString or caseName matches statute/regulation/law review patterns."""
    df = parsed_df.copy()
    initial = len(df)

    def _is_non_case(row):
        for col in ["mainCitationString", "caseName"]:
            val = row.get(col)
            if val and isinstance(val, str) and _NON_CASE_PATTERNS.search(val):
                return True
        return False

    mask = df.apply(_is_non_case, axis=1)
    n_removed = mask.sum()
    if n_removed > 0:
        logger.info(f"Filtered {n_removed} non-case citations (statutes/regulations/law reviews), {initial - n_removed} remaining")
    return df[~mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 0b: Clean up common treatment formatting errors from model output
# ---------------------------------------------------------------------------

# Lowercase → canonical-case map, derived from TREATMENT_RANK so the canonical
# list lives in one place.
_CANONICAL_BY_LOWER = {t.lower(): t for t in TREATMENT_RANK}

# Treatments that have never been valid but the model sometimes emits.
# Mapped to the closest neutral canonical form.
_INVALID_TO_NEUTRAL = {
    "followed": "Cited by",
    "followed by": "Cited by",
    "following": "Cited by",
}

# "Cert. denied" family — model often writes "Certiorari Denied" or omits "by".
_CERT_DENIED_PATTERN = re.compile(
    r"^\s*cert(?:\.|iorari)?\s+denied(?:\s+by)?\s*$", re.IGNORECASE
)
_CERT_DENIED_AS_RECOGNIZED_PATTERN = re.compile(
    r"^\s*cert(?:\.|iorari)?\s+denied(?:\s+by)?\s+as\s+recognized\s+by\s*$", re.IGNORECASE
)

# "Writ denied" / "Writ refused" = denial of writ of cert (state supreme courts;
# Louisiana uses "refused" interchangeably with "denied").
# Treated as the same legal event as "Cert. denied by".
_WRIT_DENIED_AS_RECOGNIZED_PATTERN = re.compile(
    r"^\s*writ\s+(?:denied|refused)(?:\s+by)?\s+as\s+recognized\s+by\s*$",
    re.IGNORECASE,
)

# "Appeal dismissed" and "Cert. dism'd" both map to the canonical
# "Dismissed by" Direct-History event.
_DISMISSED_VARIANTS_AS_RECOGNIZED_PATTERN = re.compile(
    r"^\s*(?:appeal\s+dismissed|cert(?:\.|iorari)?\s+dism(?:'d|issed))"
    r"(?:\s+by)?\s+as\s+recognized\s+by\s*$",
    re.IGNORECASE,
)

# "Cert. pending" — explicitly not a treatment per instructions.py.
# The prompt says: "Cert. pending is not a treatment. Assign 'Cited by'."
_CERT_PENDING_PATTERN = re.compile(
    r"^\s*cert(?:\.|iorari)?\s+pending(?:\s+by)?(?:\s+as\s+recognized\s+by)?\s*$",
    re.IGNORECASE,
)

# Partial-treatment Related Reference forms — the model sometimes emits one
# half of a composite (e.g. "Reversed in part as recognized by") when reporting
# a partial appellate disposition. Collapse to the full-treatment RR form
# ("Reversed as recognized by"); partiality information is lost but the
# directionality and severity tier are preserved.
_IN_PART_RR_PATTERN = re.compile(
    r"^\s*(affirmed|reversed)\s+in\s+part"
    r"(?:\s+on\s+other\s+grounds)?"
    r"\s+as\s+recognized\s+by\s*$",
    re.IGNORECASE,
)


def _canonicalize_treatment(treatment):
    """Map a raw treatment string to the canonical form in TREATMENT_RANK.

    Returns (canonical_treatment, change_kind) where change_kind is one of:
      - "ok":        already canonical (no change)
      - "fixed":     normalized to a canonical treatment
      - "invalid":   recognized as a known invalid string, replaced with neutral
      - "unknown":   not recognizable — left unchanged for visibility downstream
    """
    if not treatment or pd.isna(treatment):
        return treatment, "ok"

    raw = str(treatment).strip()
    if not raw:
        return raw, "ok"

    # 1. Already canonical
    if raw in TREATMENT_RANK:
        return raw, "ok"

    lower = raw.lower()

    # 2. Cert. denied family (covers "Certiorari Denied", "Cert. denied",
    #    "Cert denied", with/without "by"). Including the "as recognized by"
    #    form which is valid (Related Reference).
    if _CERT_DENIED_AS_RECOGNIZED_PATTERN.match(raw):
        return "Cert. denied as recognized by", (
            "ok" if raw == "Cert. denied as recognized by" else "fixed"
        )
    if _CERT_DENIED_PATTERN.match(raw):
        return "Cert. denied by", "fixed"

    # 2b. "Writ denied as recognized by" → "Cert. denied as recognized by"
    if _WRIT_DENIED_AS_RECOGNIZED_PATTERN.match(raw):
        return "Cert. denied as recognized by", "fixed"

    # 2c. "Appeal dismissed" / "Cert. dism'd" → "Dismissed as recognized by"
    if _DISMISSED_VARIANTS_AS_RECOGNIZED_PATTERN.match(raw):
        return "Dismissed as recognized by", "fixed"

    # 2d. "Cert. pending [as recognized by]" → "Cited by"
    # (instructions.py says cert. pending is not a treatment)
    if _CERT_PENDING_PATTERN.match(raw):
        return "Cited by", "fixed"

    # 2e. "[Affirmed|Reversed] in part [on other grounds] as recognized by"
    # → "[Affirmed|Reversed] as recognized by" (partiality info dropped)
    m = _IN_PART_RR_PATTERN.match(raw)
    if m:
        verb = m.group(1).capitalize()
        return f"{verb} as recognized by", "fixed"

    # 3. "Cited as recognized by" is explicitly NOT valid — collapse to "Cited by"
    if lower == "cited as recognized by":
        return "Cited by", "fixed"

    # 4. Malformed "... by as recognized by" → "... as recognized by"
    if " by as recognized by" in lower:
        fixed_lower = lower.replace(" by as recognized by", " as recognized by")
        # Recurse to canonicalize the corrected form
        return _canonicalize_treatment(fixed_lower)

    # 5. Known invalid treatments (e.g., "Followed", not in our taxonomy)
    if lower in _INVALID_TO_NEUTRAL:
        return _INVALID_TO_NEUTRAL[lower], "invalid"

    # 6. Case-insensitive match against canonical list
    if lower in _CANONICAL_BY_LOWER:
        return _CANONICAL_BY_LOWER[lower], "fixed"

    # 7. Missing " by" suffix: e.g. "Vacated" → "Vacated by", "Overruled" → "Overruled by"
    if (lower + " by") in _CANONICAL_BY_LOWER:
        return _CANONICAL_BY_LOWER[lower + " by"], "fixed"

    # 8. "as recognized by" form — base treatment must canonicalize to "<X> by"
    if lower.endswith(" as recognized by"):
        base = lower[: -len(" as recognized by")].strip()
        # Try the base directly and with " by" appended
        for candidate in (base, base + " by"):
            if candidate in _CANONICAL_BY_LOWER:
                canonical_base = _CANONICAL_BY_LOWER[candidate]
                # Strip trailing " by" before re-attaching " as recognized by"
                stem = canonical_base[: -len(" by")] if canonical_base.endswith(" by") else canonical_base
                return f"{stem} as recognized by", "fixed"

    # 9. Give up — leave raw value in place so it surfaces in QA, no silent rewrite
    return raw, "unknown"


def clean_treatments(parsed_df):
    """Canonicalize treatment strings against the TREATMENT_RANK taxonomy.

    Handles:
    - missing " by" suffix (Vacated → Vacated by)
    - case fixes (Vacated and Remanded by → Vacated and remanded by)
    - 'Certiorari Denied'/'Cert. denied' → 'Cert. denied by'
    - 'Cited as recognized by' → 'Cited by' (explicitly invalid)
    - '... by as recognized by' → '... as recognized by' (malformed modifier)
    - known-invalid treatments like 'Followed' → 'Cited by'
    - 'X as recognized by' forms canonicalized via the base treatment

    Strings that don't resolve to a canonical treatment are left as-is so
    they remain visible in eval rather than being silently rewritten.
    """
    df = parsed_df.copy()
    counts = {"ok": 0, "fixed": 0, "invalid": 0, "unknown": 0}
    unknown_samples = set()

    canonicalized = []
    for raw in df["treatment"].tolist():
        new_value, kind = _canonicalize_treatment(raw)
        counts[kind] += 1
        if kind == "unknown" and raw:
            unknown_samples.add(str(raw))
        canonicalized.append(new_value)
    df["treatment"] = canonicalized

    if counts["fixed"] or counts["invalid"] or counts["unknown"]:
        logger.info(
            "Treatment cleanup: "
            f"{counts['ok']} ok, {counts['fixed']} fixed, "
            f"{counts['invalid']} invalid→neutral, {counts['unknown']} unknown"
        )
        if unknown_samples:
            sample = sorted(unknown_samples)[:10]
            logger.warning(
                f"Unrecognized treatments left as-is ({len(unknown_samples)} distinct, "
                f"showing up to 10): {sample}"
            )
    return df


# ---------------------------------------------------------------------------
# Step 0c: Override severity + caseHistory from the canonical treatment
# (Runs as the FINAL postprocess step in collect(), after dedup — the model
# emits its own caseHistory but we intentionally discard it so direction
# stays a deterministic function of treatment, matching how the 0410
# benchmark labels were built.)
# ---------------------------------------------------------------------------

def _severity_from_treatment(treatment):
    if pd.isna(treatment) or not treatment:
        return "Unknown"
    base = (
        treatment.replace(" as recognized by", " by")
        if " as recognized" in treatment
        else treatment
    )
    return severity_mapping.get(base, "Unknown")


def _direction_from_treatment(treatment):
    if pd.isna(treatment) or not treatment:
        return "Unknown"
    if " as recognized by" in treatment:
        return "Related Reference"
    return direction_mapping.get(treatment, "Unknown")


def derive_severity_direction(parsed_df):
    """Override `caseHistory` and add a `severity` column, both derived from
    the (canonical) treatment string.

    Must be called AFTER clean_treatments so the treatment strings are
    canonical — non-canonical strings fall through as 'Unknown'.
    """
    df = parsed_df.copy()
    df["caseHistory"] = df["treatment"].apply(_direction_from_treatment)
    df["severity"] = df["treatment"].apply(_severity_from_treatment)
    n_unknown_dir = (df["caseHistory"] == "Unknown").sum()
    n_unknown_sev = (df["severity"] == "Unknown").sum()
    if n_unknown_dir or n_unknown_sev:
        logger.warning(
            f"derive_severity_direction: {n_unknown_dir} rows with Unknown direction, "
            f"{n_unknown_sev} with Unknown severity (treatment not in canonical mapping)"
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

# Keywords in the quote that suggest a "Cited by" may actually be "Distinguished by".
# Only checked against the quote field (opinion text), not the model's rationale.
DISTINGUISHING_KEYWORDS = [
    r"inapplicable", r"not applicable",
    r"does not apply", r"do not apply", r"did not apply",
    r"not control", r"not binding", r"not on point",
    r"does not govern", r"not dispositive",
    r"not extend", r"decline.? to extend",
    r"decline.? to follow", r"not follow", r"refuse.? to follow",
    r"distinguish", r"distinguishable",
    r"not analogous", r"unlike",
    r"unable to assent",
    r"contrary", r"but see", r"but cf\.",
    r"compare\b.*\bwith\b",
]

_DISTINGUISHING_PATTERN = re.compile("|".join(DISTINGUISHING_KEYWORDS), re.IGNORECASE)

# Keywords in the quote that suggest a "Cited by" may actually be a
# Cert. denied / Cert. granted / writ-denied chain. The 0528 Sonnet run had
# 65 'Cited by → Cert. denied by' under-predictions; routing these to re-eval
# gives the Kimi reevaluator a chance to apply its directionality rules.
CERT_QUOTE_KEYWORDS = [
    r"cert(?:\.|iorari)?\s+denied",
    r"cert(?:\.|iorari)?\s+granted",
    r"cert(?:\.|iorari)?\s+dism(?:issed|'d)",
    r"writ\s+(?:denied|refused)",
    r"appeal\s+dismissed",
]

_CERT_QUOTE_PATTERN = re.compile("|".join(CERT_QUOTE_KEYWORDS), re.IGNORECASE)


def flag_for_review(parsed_df):
    """Identify results that should be re-evaluated:
    1. 'Cited by' results whose quote contains distinguishing keywords
       (often missed negative treatments).
    2. 'Cited by' results whose quote contains cert/writ language
       (often missed Cert. denied chains — the dominant 0528 error class).
    3. All 'as recognized by' treatments (directionality is frequently wrong).
    4. All 'Distinguished by' treatments (often confused with 'Cited by').
    5. All 'Cert. denied by' base-form treatments (often over-predicted —
       the applier of a cert denial should be 'Cited by', not 'Cert. denied by').

    Returns (flagged_df, unflagged_df) where flagged_df has the rows
    to re-evaluate and unflagged_df has the clean rows.
    """
    treatment_col = parsed_df["treatment"].fillna("").str.strip()

    # Flag 1: "Cited by" with distinguishing keywords in the quote
    cited_by_mask = treatment_col.str.lower() == "cited by"
    cited_by = parsed_df[cited_by_mask].copy()

    def has_distinguishing_keywords(row):
        quote = str(row.get("quote", ""))
        return bool(_DISTINGUISHING_PATTERN.search(quote))

    keyword_flagged_mask = cited_by.apply(has_distinguishing_keywords, axis=1)
    keyword_flagged = cited_by[keyword_flagged_mask]

    # Flag 2: "Cited by" with cert/writ language in the quote
    def has_cert_keywords(row):
        quote = str(row.get("quote", ""))
        return bool(_CERT_QUOTE_PATTERN.search(quote))

    cert_flagged_mask = cited_by.apply(has_cert_keywords, axis=1)
    cert_flagged = cited_by[cert_flagged_mask]

    # Flag 3: All "as recognized by" treatments
    as_recognized_mask = treatment_col.str.contains("as recognized by", case=False, na=False)
    as_recognized = parsed_df[as_recognized_mask]

    # Flag 4: All "Distinguished by" treatments
    distinguished_mask = treatment_col.str.lower() == "distinguished by"
    distinguished_cases = parsed_df[distinguished_mask]

    # Flag 5: All "Cert. denied by" base-form (catches over-prediction;
    # the "as recognized by" variant is already covered by Flag 3).
    cert_denied_mask = treatment_col.str.lower() == "cert. denied by"
    cert_denied_cases = parsed_df[cert_denied_mask]

    # Combine flagged rows (deduplicate in case of overlap)
    flagged_indices = (
        keyword_flagged.index
        .union(cert_flagged.index)
        .union(as_recognized.index)
        .union(distinguished_cases.index)
        .union(cert_denied_cases.index)
    )
    flagged = parsed_df.loc[flagged_indices].copy()
    unflagged = parsed_df.loc[~parsed_df.index.isin(flagged_indices)].copy()

    n_kw = len(keyword_flagged)
    n_cert = len(cert_flagged)
    n_ar = len(as_recognized)
    n_dist = len(distinguished_cases)
    n_cd_base = len(cert_denied_cases)
    logger.info(
        f"Flagged for review: {len(flagged)} total — "
        f"{n_kw} 'Cited by' w/ distinguishing kws, "
        f"{n_cert} 'Cited by' w/ cert kws, "
        f"{n_ar} 'as recognized by', "
        f"{n_dist} 'Distinguished by', "
        f"{n_cd_base} 'Cert. denied by'"
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

def normalize_citation(s):
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

    labels_df should have: citing_cluster_id, cited_cluster_id, cited_case_name,
    and a citations column (either 'cited_case_citations' or 'cited_citation_strings').

    Saves matched and unmatched results to output_dir.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Detect the citations column name
    parsed_df = parsed_df.copy()
    labels_df = labels_df.copy()
    if "cited_case_citations" in labels_df.columns:
        citations_col = "cited_case_citations"
    elif "cited_citation_strings" in labels_df.columns:
        citations_col = "cited_citation_strings"
    else:
        raise KeyError("labels_df must have 'cited_case_citations' or 'cited_citation_strings' column")

    # Ensure consistent dtype before merging
    parsed_df["citing_cluster_id"] = parsed_df["citing_cluster_id"].astype(int)
    labels_df["citing_cluster_id"] = labels_df["citing_cluster_id"].astype(int)

    # Merge on citing_cluster_id
    merged = parsed_df.merge(labels_df, on="citing_cluster_id", how="left", suffixes=("_pred", "_label"))

    # Match on mainCitationString appearing in citations column
    # Normalize both sides to handle spacing differences in reporter names
    # (e.g., model returns "F.Supp." but labels have "F. Supp.")
    def _citations_match(row):
        citation = row.get("mainCitationString")
        label_citations = row.get(citations_col)
        # Empty strings (predictions without a Full Citation, e.g. unnamed
        # lower-court Direct History cases) make the substring match vacuous —
        # "" is in every string — so reject them before normalization.
        if pd.isna(citation) or not str(citation).strip():
            return False
        if pd.isna(label_citations):
            return False
        norm_citation = normalize_citation(citation).strip()
        if not norm_citation:
            return False
        norm_labels = normalize_citation(str(label_citations))
        return norm_citation in norm_labels

    mask = merged.apply(_citations_match, axis=1)
    matched = merged[mask]

    # Model results matched to labels
    # Deduplicate parallel citations matching the same label (same cited_cluster_id)
    # Keep the most severe treatment when multiple parallel citations match
    matched_cols = ["citing_cluster_id", "mainCitationString", "caseName",
                    "caseHistory", "treatment", "opinionType", "quote", "rationale",
                    "cited_cluster_id", citations_col, "cited_case_name"]
    # The merge above adds `_pred`/`_label` suffixes when both sides share a
    # column. Pick up the predicted-severity column if it landed under either
    # name and surface it as `severity` in matched_results.
    if "severity_pred" in matched.columns:
        matched = matched.rename(columns={"severity_pred": "severity"})
    if "severity" in matched.columns:
        matched_cols.insert(matched_cols.index("caseHistory") + 1, "severity")
    matched_results = matched[matched_cols].copy()
    matched_results["_rank"] = matched_results["treatment"].apply(_get_treatment_rank)
    matched_results = (
        matched_results.sort_values("_rank")
        .drop_duplicates(subset=["citing_cluster_id", "cited_cluster_id"], keep="first")
        .drop(columns=["_rank"])
    )
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
