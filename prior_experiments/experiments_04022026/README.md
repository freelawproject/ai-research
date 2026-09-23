# CLReplica DB Data Export

## Overview

This experiment exports citation data from the CourtListener (CLReplica) Django database. The script (`utils/script.py`) queries for opinion cluster metadata, full opinion texts, matched authorities, and unmatched citations across U.S. federal circuit courts.

## What the Script Does

1. **Loads existing cluster IDs** from `ids.csv` (382 pre-selected clusters) and retrieves their court, case name, and year.
2. **Samples 50 additional clusters per circuit court** (13 circuits: ca1–ca11, cadc, cafc) that have at least one cited opinion and are not already in `ids.csv`.
3. **Filters the existing IDs** to only those belonging to circuit courts, then combines them with the sampled IDs into a single set.
4. **For each cluster**, the script:
   - Extracts the best available opinion text, converts citation `<span>` links into `<citedCase>` tags, and strips remaining HTML.
   - Saves the processed text to an individual `.txt` file.
   - Counts matched authorities (`OpinionsCited`) and unmatched citations (`UnmatchedCitation`).
   - Records metadata for the citing case, each authority link, and each unmatched citation.
5. **Writes output CSVs**: `citing_metadata.csv`, `authority_metadata.csv`, `unmatched_metadata.csv`, and a combined `cited_metadata.csv`.

## Data

### `data/ids.csv`
Pre-selected opinion cluster IDs used as input (382 IDs).

### `data/citing_metadata.csv` (661 rows)
One row per citing cluster with columns: `cluster_id`, `source` (existing vs. sampled), `court`, `case_name`, `year`, `num_authorities`, `num_unmatched_citations`, `opinion_text_length`.

### `data/authority_metadata.csv` (24,312 rows)
One row per citing-to-cited authority link with columns: `unique_id`, `citing_cluster_id`, `cited_cluster_id`, `cited_case_name`, `cited_case_citations`.

### `data/unmatched_metadata.csv` (6,240 rows)
One row per unmatched citation with columns: `unique_id`, `citing_cluster_id`, `citation_string`.

### `data/cited_metadata.csv` (30,552 rows)
Combined view of authorities and unmatched citations with a `type` column distinguishing them.

### `data/opinion_texts/` (661 files)
Processed opinion text files named by cluster ID (e.g., `1000719.txt`). HTML is stripped except for `<citedCase>` tags wrapping cited case references.
