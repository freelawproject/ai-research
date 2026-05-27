"""Run eyecite citation extraction on the 0518 benchmark opinions.

For each opinion in benchmark_cluster_ids.csv, run eyecite to extract citations
and emit a CSV matching the column shape of Haiku's parsed_results.csv
(subset of extraction-only columns), so the two can be diffed directly.

Output: data/eyecite_extractions.csv with columns:
    citing_cluster_id, mainCitationString, caseName, section_ids

Usage:
    python run_eyecite.py
"""

import json
import logging
import os
import re
import sys
import time

import pandas as pd
from eyecite import get_citations, resolve_citations
from eyecite.models import FullCaseCitation

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "citator-pipeline"))
from utils.section_utils import split_into_sections  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
BENCHMARK_IDS = os.path.join(DATA_DIR, "benchmark_cluster_ids.csv")
OPINIONS_DIR = os.path.join(DATA_DIR, "opinion_texts")
OUTPUT_PATH = os.path.join(DATA_DIR, "eyecite_extractions.csv")

REPORTER_SPACE = re.compile(r"\s+")


def normalize_reporter(reporter):
    """Match Haiku's reporter normalization: strip internal spaces.

    'F. Supp.' -> 'F.Supp.', 'F. 2d' -> 'F.2d', 'F. Supp. 3d' -> 'F.Supp.3d'.
    """
    return REPORTER_SPACE.sub("", reporter or "")


def build_main_citation_string(cite):
    """Format a FullCaseCitation as 'volume reporter page' with normalized reporter."""
    groups = cite.groups or {}
    volume = groups.get("volume")
    reporter = groups.get("reporter")
    page = groups.get("page")
    if not (volume and reporter and page):
        return None
    return f"{volume} {normalize_reporter(reporter)} {page}"


def build_case_name(cite):
    """Compose 'Plaintiff v. Defendant' from FullCaseCitation metadata, or None."""
    meta = cite.metadata
    plaintiff = (getattr(meta, "plaintiff", None) or "").strip(" ,()")
    defendant = (getattr(meta, "defendant", None) or "").strip(" ,()")
    if plaintiff and defendant:
        return f"{plaintiff} v. {defendant}"
    if defendant:
        return defendant
    if plaintiff:
        return plaintiff
    return None


def offset_to_section_id(offset, sections):
    """Map a char offset in raw text to the containing section_id, or None."""
    for s in sections:
        if s["start_char"] <= offset < s["end_char"]:
            return s["id"]
    if sections and offset >= sections[-1]["end_char"]:
        return sections[-1]["id"]
    return None


def extract_for_opinion(cluster_id, opinion_text):
    """Return a list of {mainCitationString, caseName, section_ids} dicts."""
    sections = split_into_sections(opinion_text)
    citations = get_citations(opinion_text)
    resolutions = resolve_citations(citations)

    rows = []
    for resource, cites in resolutions.items():
        full = next((c for c in cites if isinstance(c, FullCaseCitation)), None)
        if full is None:
            continue

        main_cite = build_main_citation_string(full)
        case_name = build_case_name(full)

        section_ids = []
        seen = set()
        for c in cites:
            start = c.span()[0]
            sid = offset_to_section_id(start, sections)
            if sid and sid not in seen:
                seen.add(sid)
                section_ids.append(sid)

        rows.append({
            "citing_cluster_id": cluster_id,
            "mainCitationString": main_cite,
            "caseName": case_name,
            "section_ids": json.dumps(sorted(section_ids, key=lambda s: int(s[1:]))),
        })
    return rows


def main():
    ids_df = pd.read_csv(BENCHMARK_IDS)
    cluster_ids = ids_df["cluster_id"].astype(str).tolist()
    logger.info(f"Loaded {len(cluster_ids)} cluster IDs")

    all_rows = []
    failed = []
    start = time.time()

    for i, cid in enumerate(cluster_ids, 1):
        path = os.path.join(OPINIONS_DIR, f"{cid}.txt")
        if not os.path.exists(path):
            logger.warning(f"[{i}/{len(cluster_ids)}] {cid}: no opinion text on disk")
            failed.append(cid)
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            rows = extract_for_opinion(cid, text)
            all_rows.extend(rows)
            if i % 25 == 0 or i == len(cluster_ids):
                elapsed = time.time() - start
                logger.info(f"[{i}/{len(cluster_ids)}] {len(all_rows)} citations so far, {elapsed:.1f}s elapsed")
        except Exception as e:
            logger.error(f"[{i}/{len(cluster_ids)}] {cid}: {e}")
            failed.append(cid)

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_PATH, index=False)
    elapsed = time.time() - start
    logger.info(f"\nDone in {elapsed:.1f}s")
    logger.info(f"  opinions processed: {len(cluster_ids) - len(failed)}/{len(cluster_ids)}")
    logger.info(f"  citations extracted: {len(df)}")
    logger.info(f"  output: {OUTPUT_PATH}")
    if failed:
        logger.info(f"  failed: {len(failed)} -> {failed[:10]}...")


if __name__ == "__main__":
    main()
