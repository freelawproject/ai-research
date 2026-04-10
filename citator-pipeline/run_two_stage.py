"""Two-stage citator pipeline: extraction + Kimi treatment classification.

Stage 1: Extract citations and identify which sections they appear in (Haiku or Gemini Flash).
Stage 2: Kimi classifies treatments using only the relevant section context.

Usage:
    python run_two_stage.py --output-dir ../experiments_04072026/data --evaluate
    python run_two_stage.py --output-dir ../experiments_04082026/data --evaluate
    python run_two_stage.py --output-dir ../experiments_04072026/data --postprocess-only
"""

import argparse
import json
import logging
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
from json_repair import repair_json

from utils.bedrock_converse_utils import (
    converse_completion,
    haiku_extraction_tool_spec,
    kimi_classification_tool_spec,
)
from utils.eval_utils import evaluate_example
from utils.instructions import haiku_extractor, kimi_classifier, reevaluator
from utils.postprocess import (
    clean_treatments,
    deduplicate_cited_cases,
    filter_non_case_citations,
    flag_for_review,
    merge_to_labels,
)
from utils.reevaluate import reevaluation_tool_spec
from utils.predict import load_cluster_ids, load_opinion_text, parse_cited_cases
from utils.section_utils import (
    split_into_sections,
    annotate_opinion_with_sections,
    get_section_context,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
KIMI_MODEL_ID = "moonshotai.kimi-k2.5"
MAX_WORKERS = 5
WAIT_S = 2


# ── Helpers ──

def _single_retry(func, wait_s=WAIT_S):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            time.sleep(wait_s)
            return func(*args, **kwargs)
    return wrapper


def _save_incremental(key, data, subdir, output_dir):
    inc_dir = os.path.join(output_dir, subdir)
    os.makedirs(inc_dir, exist_ok=True)
    path = os.path.join(inc_dir, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _load_incremental(subdir, output_dir):
    inc_dir = os.path.join(output_dir, subdir)
    results = {}
    if not os.path.isdir(inc_dir):
        return results
    for fname in os.listdir(inc_dir):
        if fname.endswith(".json"):
            path = os.path.join(inc_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                key = fname.replace(".json", "")
                results[key] = data
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")
    return results


def _parse_extraction_result(result):
    """Parse Haiku extraction response into a list of cited case dicts.

    Normalizes mainCitationString to remove spaces in reporter names
    (e.g., 'F. 2d' → 'F.2d') since Haiku occasionally returns them.
    """
    cited_cases = result.get("cited_cases", [])
    if isinstance(cited_cases, dict):
        cited_cases = cited_cases.get("citedCases", [])
    elif isinstance(cited_cases, str):
        try:
            parsed = json.loads(cited_cases)
        except json.JSONDecodeError:
            parsed = repair_json(cited_cases, return_objects=True)
        if isinstance(parsed, dict):
            cited_cases = parsed.get("citedCases", [])
        elif isinstance(parsed, list):
            cited_cases = parsed
        else:
            cited_cases = []
    elif not isinstance(cited_cases, list):
        cited_cases = []

    return cited_cases


def _parse_classification_result(result):
    """Parse Kimi classification response into a list of classification dicts."""
    cited_cases = result.get("cited_cases", [])
    if isinstance(cited_cases, dict):
        return cited_cases.get("citedCases", [])
    if isinstance(cited_cases, str):
        try:
            parsed = json.loads(cited_cases)
        except json.JSONDecodeError:
            parsed = repair_json(cited_cases, return_objects=True)
        if isinstance(parsed, dict):
            return parsed.get("citedCases", [])
        return parsed if isinstance(parsed, list) else []
    if isinstance(cited_cases, list):
        return cited_cases
    return []


# ── Stage 1: Extraction ──

@_single_retry
def _extract_single(system_prompt, annotated_text, model_id):
    return converse_completion(
        system_prompt,
        annotated_text,
        model_id=model_id,
        tool_spec_override=haiku_extraction_tool_spec,
    )


def run_extraction(cluster_ids, opinion_dir, output_dir, resume=False):
    """Run Haiku extraction on all opinions. Returns (extractions, sections_map).

    extractions: {cluster_id: {cited_cases: [...], model, tokens, ...}}
    sections_map: {cluster_id: [section_dicts]}
    """
    sections_map = {}
    extractions = {}
    save_lock = Lock()

    # Resume
    if resume:
        extractions = _load_incremental("extraction_incremental", output_dir)
    already_done = set(extractions.keys())
    remaining = [cid for cid in cluster_ids if str(cid) not in already_done]

    if already_done:
        logger.info(f"Extraction: resuming — {len(already_done)} done, {len(remaining)} remaining")

    # Pre-process all opinions into sections
    annotated_texts = {}
    sections_dir = os.path.join(output_dir, "sections")
    os.makedirs(sections_dir, exist_ok=True)
    for cid in cluster_ids:
        opinion_text = load_opinion_text(opinion_dir, cid)
        sections = split_into_sections(opinion_text)
        sections_map[str(cid)] = sections
        annotated_text = annotate_opinion_with_sections(opinion_text, sections)
        annotated_texts[str(cid)] = annotated_text
        logger.info(f"cluster_id={cid}: {len(sections)} sections, {len(opinion_text):,} chars")

        # Save annotated text and section metadata for review
        with open(os.path.join(sections_dir, f"{cid}_annotated.txt"), "w", encoding="utf-8") as f:
            f.write(annotated_text)
        with open(os.path.join(sections_dir, f"{cid}_sections.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": s["id"], "start_char": s["start_char"], "end_char": s["end_char"],
                        "length": len(s["text"])} for s in sections], f, indent=2)

    # Run extraction on remaining
    processed = len(already_done)
    total = len(cluster_ids)
    failed_ids = []

    logger.info(f"Starting Haiku extraction ({HAIKU_MODEL_ID}) for {len(remaining)} opinions")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(
                _extract_single, haiku_extractor, annotated_texts[str(cid)], HAIKU_MODEL_ID
            ): str(cid)
            for cid in remaining
        }
        for future in as_completed(future_to_id):
            cid = future_to_id[future]
            processed += 1
            try:
                result = future.result()
                if not result:
                    raise RuntimeError("Empty response from Extractor")
                cited_cases = _parse_extraction_result(result)
                extraction = {
                    "model": result.get("model", ""),
                    "stop_reason": result.get("stop_reason", ""),
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "latency_ms": result.get("latency_ms", 0),
                    "cited_cases": cited_cases,
                }
                extractions[cid] = extraction
                with save_lock:
                    _save_incremental(cid, extraction, "extraction_incremental", output_dir)
                logger.info(
                    f"Extracted {processed}/{total}: cluster_id={cid}, "
                    f"{len(cited_cases)} cited cases"
                )
            except Exception as e:
                failed_ids.append(cid)
                logger.error(f"Extraction failed {processed}/{total}: cluster_id={cid}: {e}")

    # Save failed cluster IDs for later review
    if failed_ids:
        failed_path = os.path.join(output_dir, "extraction_failed.txt")
        with open(failed_path, "w") as f:
            f.write("\n".join(failed_ids))
        logger.warning(f"{len(failed_ids)} extractions failed — saved to {failed_path}")

    # Save extraction results
    _save_extraction_csvs(extractions, output_dir)

    return extractions, sections_map


def _save_extraction_csvs(extractions, output_dir):
    """Save extraction raw and parsed results."""
    os.makedirs(output_dir, exist_ok=True)

    # Raw results
    raw_rows = []
    for cid, info in extractions.items():
        raw_rows.append({
            "citing_cluster_id": cid,
            "model": info.get("model", ""),
            "stop_reason": info.get("stop_reason", ""),
            "latency_ms": info.get("latency_ms", 0),
            "input_tokens": info.get("input_tokens", 0),
            "output_tokens": info.get("output_tokens", 0),
            "num_cited_cases": len(info.get("cited_cases", [])),
        })
    raw_df = pd.DataFrame(raw_rows)
    raw_df.to_csv(os.path.join(output_dir, "extraction_raw_results.csv"), index=False)
    logger.info(f"Saved extraction raw results ({len(raw_df)} rows)")

    # Parsed results
    parsed_rows = []
    for cid, info in extractions.items():
        for cc in info.get("cited_cases", []):
            parsed_rows.append({
                "citing_cluster_id": cid,
                "mainCitationString": cc.get("mainCitationString"),
                "caseName": cc.get("caseName"),
                "section_ids": json.dumps(cc.get("section_ids", [])),
            })
    parsed_df = pd.DataFrame(parsed_rows)
    parsed_df.to_csv(os.path.join(output_dir, "extraction_parsed_results.csv"), index=False)
    logger.info(f"Saved extraction parsed results ({len(parsed_df)} rows)")


# ── Stage 2: Kimi Classification ──

def _group_citations_by_context(cited_cases, sections):
    """Group citations that share the exact same section_ids.

    Citations with identical section_ids are sent to Kimi in a single call
    with the same context window (sections + neighbors). Citations with
    different section_ids get separate calls.

    Returns list of groups: [{"citations": [...], "section_indices": set()}]
    """
    id_to_idx = {s["id"]: i for i, s in enumerate(sections)}
    n = len(sections)

    # Group by exact section_ids tuple
    key_to_entries = defaultdict(list)
    for i, cc in enumerate(cited_cases):
        section_ids = tuple(sorted(cc.get("section_ids", [])))
        key_to_entries[section_ids].append((i, cc))

    groups = []
    for section_ids_key, entries in key_to_entries.items():
        # Build context window (sections + neighbors)
        context_indices = set()
        for sid in section_ids_key:
            idx = id_to_idx.get(sid)
            if idx is not None:
                context_indices.add(idx)
                if idx > 0:
                    context_indices.add(idx - 1)
                if idx < n - 1:
                    context_indices.add(idx + 1)

        groups.append({
            "citations": [cc for _, cc in entries],
            "original_indices": [idx for idx, _ in entries],
            "section_indices": context_indices,
        })

    return groups


def _format_kimi_user_text(group, sections):
    """Format a Kimi user prompt for a group of citations."""
    # Build section context
    sorted_indices = sorted(group["section_indices"])
    context_parts = []
    for idx in sorted_indices:
        s = sections[idx]
        context_parts.append(f"[Section {s['id']}]\n{s['text']}")
    context_text = "\n\n".join(context_parts)

    # Build citation list
    citation_parts = [
        "Classify the treatment for each of the following cited cases based on "
        "the opinion excerpt above.\n"
    ]
    for i, cc in enumerate(group["citations"], 1):
        citation_str = cc.get("mainCitationString") or "(no citation string)"
        case_name = cc.get("caseName") or "(unnamed)"
        section_ids = cc.get("section_ids", [])
        citation_parts.append(
            f"--- Cited Case {i} ---\n"
            f"Citation: {citation_str}\n"
            f"Case Name: {case_name}\n"
            f"Appears in sections: {', '.join(section_ids)}\n"
        )

    return f"<opinion_excerpt>\n{context_text}\n</opinion_excerpt>\n\n" + "\n".join(citation_parts)


@_single_retry
def _classify_single(system_prompt, user_text, model_id):
    return converse_completion(
        system_prompt,
        user_text,
        model_id=model_id,
        tool_spec_override=kimi_classification_tool_spec,
    )


def run_classification(extractions, sections_map, output_dir, resume=False):
    """Run Kimi classification on all extracted citations.

    Returns a dict: {cluster_id: [classification_dicts]}
    """
    classifications = {}
    save_lock = Lock()

    # Resume
    if resume:
        classifications = _load_incremental("classification_incremental", output_dir)
    already_done = set(classifications.keys())

    all_cluster_ids = list(extractions.keys())
    remaining = [cid for cid in all_cluster_ids if cid not in already_done]

    if already_done:
        logger.info(f"Classification: resuming — {len(already_done)} done, {len(remaining)} remaining")

    processed = len(already_done)
    total = len(all_cluster_ids)
    failed_ids = []

    # Track raw results for CSV
    all_raw_rows = []

    for cid in remaining:
        extraction = extractions[cid]
        cited_cases = extraction.get("cited_cases", [])
        sections = sections_map.get(cid, [])

        if not cited_cases or not sections:
            logger.warning(f"cluster_id={cid}: no cited cases or sections — skipping")
            classifications[cid] = []
            processed += 1
            continue

        # Group citations by overlapping section context
        groups = _group_citations_by_context(cited_cases, sections)
        logger.info(
            f"cluster_id={cid}: {len(cited_cases)} citations → {len(groups)} context groups"
        )

        # Classify each group
        opinion_results = [None] * len(cited_cases)  # indexed by original position

        for group_idx, group in enumerate(groups):
            user_text = _format_kimi_user_text(group, sections)
            try:
                result = _classify_single(kimi_classifier, user_text, KIMI_MODEL_ID)
                group_classifications = _parse_classification_result(result)

                # Map classifications back to original citation indices
                for gc in group_classifications:
                    case_id = gc.get("citedCaseId", 0)
                    if 1 <= case_id <= len(group["citations"]):
                        orig_idx = group["original_indices"][case_id - 1]
                        # Merge extraction info with classification
                        cc = cited_cases[orig_idx]
                        opinion_results[orig_idx] = {
                            "mainCitationString": cc.get("mainCitationString"),
                            "caseName": cc.get("caseName"),
                            "section_ids": cc.get("section_ids", []),
                            "actingCase": gc.get("actingCase", ""),
                            "caseHistory": gc.get("caseHistory", ""),
                            "treatment": gc.get("treatment", ""),
                            "opinionType": gc.get("opinionType", ""),
                            "quote": gc.get("quote", ""),
                            "rationale": gc.get("rationale", ""),
                        }

                all_raw_rows.append({
                    "citing_cluster_id": cid,
                    "group_idx": group_idx,
                    "num_citations": len(group["citations"]),
                    "model": result.get("model", ""),
                    "stop_reason": result.get("stop_reason", ""),
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "latency_ms": result.get("latency_ms", 0),
                })
            except Exception as e:
                logger.error(f"Classification failed for cluster_id={cid} group {group_idx}: {e}")

        # Fill in any citations that weren't classified (default to Cited by)
        for i, cc in enumerate(cited_cases):
            if opinion_results[i] is None:
                opinion_results[i] = {
                    "mainCitationString": cc.get("mainCitationString"),
                    "caseName": cc.get("caseName"),
                    "section_ids": cc.get("section_ids", []),
                    "actingCase": "Citing Case",
                    "caseHistory": "Citing Reference",
                    "treatment": "Cited by",
                    "opinionType": "Lead",
                    "quote": None,
                    "rationale": "Classification failed; defaulting to Cited by.",
                }

        # Track if all citations defaulted to fallback (all groups failed)
        n_fallback = sum(1 for r in opinion_results if r.get("rationale", "").startswith("Classification failed"))
        if n_fallback == len(opinion_results):
            failed_ids.append(cid)

        classifications[cid] = opinion_results
        with save_lock:
            _save_incremental(cid, opinion_results, "classification_incremental", output_dir)

        processed += 1
        logger.info(f"Classified {processed}/{total}: cluster_id={cid}")

    # Save failed cluster IDs for later review
    if failed_ids:
        failed_path = os.path.join(output_dir, "classification_failed.txt")
        with open(failed_path, "w") as f:
            f.write("\n".join(failed_ids))
        logger.warning(f"{len(failed_ids)} classifications fully failed — saved to {failed_path}")

    # Save classification CSVs
    _save_classification_csvs(classifications, all_raw_rows, output_dir)

    return classifications


def _save_classification_csvs(classifications, raw_rows, output_dir):
    """Save classification raw and parsed results."""
    os.makedirs(output_dir, exist_ok=True)

    # Raw results
    if raw_rows:
        raw_df = pd.DataFrame(raw_rows)
        raw_df.to_csv(os.path.join(output_dir, "classification_raw_results.csv"), index=False)
        logger.info(f"Saved classification raw results ({len(raw_df)} rows)")

    # Parsed results
    parsed_rows = []
    for cid, results in classifications.items():
        for r in results:
            parsed_rows.append({
                "citing_cluster_id": cid,
                "mainCitationString": r.get("mainCitationString"),
                "caseName": r.get("caseName"),
                "actingCase": r.get("actingCase", ""),
                "caseHistory": r.get("caseHistory", ""),
                "treatment": r.get("treatment", ""),
                "opinionType": r.get("opinionType", ""),
                "quote": r.get("quote"),
                "rationale": r.get("rationale", ""),
                "section_ids": json.dumps(r.get("section_ids", [])),
            })
    parsed_df = pd.DataFrame(parsed_rows)
    parsed_df.to_csv(os.path.join(output_dir, "classification_parsed_results.csv"), index=False)
    logger.info(f"Saved classification parsed results ({len(parsed_df)} rows)")


# ── Stage 3: Re-evaluation (optional) ──

SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
REEVAL_BATCH_SIZE = 5


def _format_reeval_user_text(rows_with_context):
    """Format a batch of flagged results with section context for re-evaluation."""
    parts = [
        "Re-evaluate each of the following Cited Cases. For each one, independently analyze "
        "the directionality and determine the correct treatment. The model's original treatment "
        "and rationale may be wrong.\n"
    ]
    for i, (_, row, section_context) in enumerate(rows_with_context, 1):
        parts.append(
            f"--- Cited Case {i} ---\n"
            f"Cited Case Name: {row.get('caseName', 'N/A')}\n"
            f"Citation: {row.get('mainCitationString', 'N/A')}\n"
            f"Model's Assigned Treatment: {row.get('treatment', 'N/A')}\n"
            f"Model's Rationale: {row.get('rationale', 'N/A')}\n"
            f"\nRelevant opinion excerpt:\n{section_context}\n"
        )
    return "\n".join(parts)


@_single_retry
def _reevaluate_batch(rows_with_context, model_id):
    user_text = _format_reeval_user_text(rows_with_context)
    result = converse_completion(
        reevaluator,
        user_text,
        model_id=model_id,
        tool_spec_override=reevaluation_tool_spec,
    )
    cited_cases = result.get("cited_cases", [])
    if isinstance(cited_cases, dict):
        reassessments = cited_cases.get("reassessments", [])
    elif isinstance(cited_cases, str):
        try:
            parsed = json.loads(cited_cases)
        except json.JSONDecodeError:
            parsed = repair_json(cited_cases, return_objects=True)
        reassessments = parsed.get("reassessments", []) if isinstance(parsed, dict) else []
    else:
        reassessments = []
    raw_info = {
        "model": result.get("model", ""),
        "stop_reason": result.get("stop_reason", ""),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "latency_ms": result.get("latency_ms", 0),
    }
    return reassessments, raw_info


def run_reevaluation(deduped_df, sections_map, extractions, output_dir,
                     model_id=SONNET_MODEL_ID, resume=False):
    """Re-evaluate flagged results with section context.

    Unlike the single-stage re-evaluator which only passes the quote,
    this passes the full section context where each citation appears.
    """
    flagged_df, clean_df = flag_for_review(deduped_df)

    if flagged_df.empty:
        logger.info("No results flagged — skipping re-evaluation")
        return deduped_df

    # Build section context for each flagged row
    rows_with_context = []
    for df_idx, row in flagged_df.iterrows():
        cid = str(row["citing_cluster_id"])
        sections = sections_map.get(cid, [])
        # Find which sections this citation appears in from extraction data
        extraction = extractions.get(cid, {})
        section_ids = []
        for cc in extraction.get("cited_cases", []):
            if cc.get("mainCitationString") == row.get("mainCitationString"):
                section_ids = cc.get("section_ids", [])
                break
        context = get_section_context(sections, section_ids, include_neighbors=True) if section_ids else str(row.get("quote", ""))
        rows_with_context.append((df_idx, row, context))

    # Batch and send to re-evaluator
    batches = [rows_with_context[i:i + REEVAL_BATCH_SIZE]
               for i in range(0, len(rows_with_context), REEVAL_BATCH_SIZE)]

    logger.info(f"Re-evaluating {len(flagged_df)} flagged results in {len(batches)} batches ({model_id})")

    # Resume from incremental results
    batch_results = {}
    if resume:
        batch_results = _load_incremental("reevaluation_incremental", output_dir)
        # Convert string keys back to int
        batch_results = {int(k): v for k, v in batch_results.items()}
    already_done = set(batch_results.keys())
    remaining_batches = [(idx, b) for idx, b in enumerate(batches) if idx not in already_done]

    if already_done:
        logger.info(f"Re-evaluation: resuming — {len(already_done)} batches done, {len(remaining_batches)} remaining")

    updated = flagged_df.copy()
    save_lock = Lock()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_batch = {
            executor.submit(_reevaluate_batch, batch, model_id): (batch_idx, batch)
            for batch_idx, batch in remaining_batches
        }
        for future in as_completed(future_to_batch):
            batch_idx, batch = future_to_batch[future]
            try:
                reassessments, raw_info = future.result()
                batch_results[batch_idx] = {"reassessments": reassessments, "raw_info": raw_info}

                with save_lock:
                    _save_incremental(
                        str(batch_idx),
                        {"reassessments": reassessments, "raw_info": raw_info},
                        "reevaluation_incremental",
                        output_dir,
                    )
                logger.info(f"Re-evaluated batch {len(batch_results)}/{len(batches)}")
            except Exception as e:
                logger.error(f"Re-evaluation batch {batch_idx} failed: {e}")

    # Apply all results (including resumed) to the flagged dataframe
    all_raw_rows = []
    for batch_idx in sorted(batch_results.keys()):
        data = batch_results[batch_idx]
        reassessments = data.get("reassessments", [])
        raw_info = data.get("raw_info", {})
        batch = batches[batch_idx]
        reeval_by_id = {r.get("citedCaseId"): r for r in reassessments if isinstance(r, dict)}

        for position, (df_idx, row, _) in enumerate(batch, 1):
            reeval = reeval_by_id.get(position, {})
            new_treatment = reeval.get("treatment", "")
            new_rationale = reeval.get("rationale", "")
            if new_treatment and new_treatment != "Cited by":
                updated.at[df_idx, "treatment"] = new_treatment
                updated.at[df_idx, "rationale"] = f"[Re-evaluated] {new_rationale}"

        if raw_info:
            all_raw_rows.append({"batch": batch_idx, "batch_size": len(batch), **raw_info})

    n_changed = (updated["treatment"] != flagged_df["treatment"]).sum()
    logger.info(f"Re-evaluation complete: {n_changed}/{len(flagged_df)} treatments changed")

    # Save consolidated results
    if all_raw_rows:
        raw_df = pd.DataFrame(all_raw_rows)
        raw_df.to_csv(os.path.join(output_dir, "reevaluation_raw_results.csv"), index=False)

    combined_df = pd.concat([clean_df, updated], ignore_index=True)
    combined_df.to_csv(os.path.join(output_dir, "reevaluated_results.csv"), index=False)
    logger.info(f"Saved re-evaluated results ({len(combined_df)} rows)")

    return combined_df


# ── Combine into final DataFrame ──

def combine_results(classifications, output_dir):
    """Build the final DataFrame compatible with the existing eval pipeline."""
    rows = []
    for cid, results in classifications.items():
        for r in results:
            rows.append({
                "citing_cluster_id": cid,
                "mainCitationString": r.get("mainCitationString"),
                "caseName": r.get("caseName"),
                "actingCase": r.get("actingCase", ""),
                "caseHistory": r.get("caseHistory", ""),
                "treatment": r.get("treatment", ""),
                "opinionType": r.get("opinionType", ""),
                "quote": r.get("quote"),
                "rationale": r.get("rationale", ""),
            })
    df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    df.to_csv(os.path.join(output_dir, "parsed_results.csv"), index=False)
    logger.info(f"Saved combined parsed results ({len(df)} rows)")
    return df


def build_raw_results(extractions, classifications, output_dir):
    """Build a raw_results.csv compatible with eval_utils.evaluate_example."""
    rows = []
    for cid in extractions:
        ext = extractions[cid]
        # Sum classification tokens for this opinion
        cls_data = classifications.get(cid, [])
        rows.append({
            "citing_cluster_id": cid,
            "model": f"{ext.get('model', '')} + {KIMI_MODEL_ID}",
            "stop_reason": ext.get("stop_reason", ""),
            "latency_ms": ext.get("latency_ms", 0),
            "input_tokens": ext.get("input_tokens", 0),
            "output_tokens": ext.get("output_tokens", 0),
            "num_cited_cases": len(cls_data),
        })
    df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    df.to_csv(os.path.join(output_dir, "raw_results.csv"), index=False)
    logger.info(f"Saved raw results ({len(df)} rows)")


# ── Main Pipeline ──

def run(
    input_dir,
    output_dir,
    txt_path,
    opinion_dir=None,
    labels_path=None,
    reevaluate=False,
    evaluate=False,
    resume=False,
):
    results_dir = os.path.join(output_dir, "output")
    eval_dir = os.path.join(output_dir, "eval")
    if opinion_dir is None:
        opinion_dir = os.path.join(input_dir, "example_opinion_texts")
    cluster_ids = load_cluster_ids(txt_path)
    logger.info(f"Loaded {len(cluster_ids)} cluster IDs from {txt_path}")

    # ── Step 1: Pre-process + Haiku extraction ──
    logger.info(f"\n{'='*60}")
    logger.info(f"Step 1: Haiku extraction ({HAIKU_MODEL_ID})")
    logger.info(f"{'='*60}")
    extractions, sections_map = run_extraction(
        cluster_ids, opinion_dir, results_dir, resume=resume
    )

    # ── Step 2: Kimi classification ──
    logger.info(f"\n{'='*60}")
    logger.info(f"Step 2: Kimi classification ({KIMI_MODEL_ID})")
    logger.info(f"{'='*60}")
    classifications = run_classification(
        extractions, sections_map, results_dir, resume=resume
    )

    # ── Step 3: Combine + post-process ──
    logger.info(f"\n{'='*60}")
    logger.info("Step 3: Combine and post-process")
    logger.info(f"{'='*60}")
    parsed_df = combine_results(classifications, results_dir)
    build_raw_results(extractions, classifications, results_dir)

    parsed_df = filter_non_case_citations(parsed_df)
    parsed_df = clean_treatments(parsed_df)
    deduped_df = deduplicate_cited_cases(parsed_df)

    # ── Step 3b: Re-evaluate flagged results (optional) ──
    if reevaluate:
        logger.info(f"\n{'='*60}")
        logger.info(f"Step 3b: Re-evaluate flagged results ({SONNET_MODEL_ID})")
        logger.info(f"{'='*60}")
        deduped_df = run_reevaluation(
            deduped_df, sections_map, extractions, results_dir,
            model_id=SONNET_MODEL_ID, resume=resume,
        )
        deduped_df = clean_treatments(deduped_df)

    final_df = deduplicate_cited_cases(deduped_df)
    final_df.to_csv(os.path.join(results_dir, "final_results.csv"), index=False)
    logger.info(f"Saved final results ({len(final_df)} rows)")

    # ── Step 4: Merge to labels ──
    if labels_path and os.path.exists(labels_path):
        logger.info(f"\n{'='*60}")
        logger.info("Step 4: Merge to labels")
        logger.info(f"{'='*60}")
        labels_df = pd.read_csv(labels_path)
        labels_df = labels_df[
            (labels_df["final_treatment"] != "PENDING")
            & (labels_df["final_treatment"] != "REMOVE")
            & (labels_df["final_treatment"] != "MANUAL")
        ]
        merge_to_labels(final_df, labels_df, eval_dir)

    # ── Step 5: Evaluate (optional) ──
    if evaluate and labels_path and os.path.exists(labels_path):
        logger.info(f"\n{'='*60}")
        logger.info("Step 5: Evaluate against expert labels")
        logger.info(f"{'='*60}")
        evaluate_example(
            output_dir=results_dir,
            eval_dir=eval_dir,
            labels_path=labels_path,
        )

    logger.info(f"\n{'='*60}")
    logger.info("Pipeline complete")
    logger.info(f"{'='*60}")


def postprocess_only(output_dir, labels_path):
    """Re-run post-processing and evaluation on existing parsed results."""
    results_dir = os.path.join(output_dir, "output")
    eval_dir = os.path.join(output_dir, "eval")

    parsed_path = os.path.join(results_dir, "parsed_results.csv")
    if not os.path.exists(parsed_path):
        raise FileNotFoundError(f"No parsed results at {parsed_path} — run the full pipeline first")

    logger.info(f"\n{'='*60}")
    logger.info("Post-process: filter, clean, dedup")
    logger.info(f"{'='*60}")
    parsed_df = pd.read_csv(parsed_path)
    logger.info(f"Loaded parsed results ({len(parsed_df)} rows)")

    parsed_df = filter_non_case_citations(parsed_df)
    parsed_df = clean_treatments(parsed_df)
    final_df = deduplicate_cited_cases(parsed_df)
    final_df.to_csv(os.path.join(results_dir, "final_results.csv"), index=False)
    logger.info(f"Saved final results ({len(final_df)} rows)")

    # Merge + evaluate
    if labels_path and os.path.exists(labels_path):
        logger.info(f"\n{'='*60}")
        logger.info("Merge to labels")
        logger.info(f"{'='*60}")
        labels_df = pd.read_csv(labels_path)
        merge_to_labels(final_df, labels_df, eval_dir)

        logger.info(f"\n{'='*60}")
        logger.info("Evaluate against expert labels")
        logger.info(f"{'='*60}")
        evaluate_example(
            output_dir=results_dir,
            eval_dir=eval_dir,
            labels_path=labels_path,
        )

    logger.info(f"\n{'='*60}")
    logger.info("Post-processing complete")
    logger.info(f"{'='*60}")


def evaluate_only(output_dir, labels_path):
    """Run only evaluation on existing final results."""
    results_dir = os.path.join(output_dir, "output")
    eval_dir = os.path.join(output_dir, "eval")

    final_path = os.path.join(results_dir, "final_results.csv")
    if not os.path.exists(final_path):
        raise FileNotFoundError(f"No final results at {final_path} — run the full pipeline first")

    if not labels_path or not os.path.exists(labels_path):
        raise FileNotFoundError(f"Labels file required: {labels_path}")

    # Merge to labels
    logger.info(f"\n{'='*60}")
    logger.info("Step 4: Merge to labels")
    logger.info(f"{'='*60}")
    final_df = pd.read_csv(final_path)
    labels_df = pd.read_csv(labels_path)
    merge_to_labels(final_df, labels_df, eval_dir)

    # Evaluate
    logger.info(f"\n{'='*60}")
    logger.info("Step 5: Evaluate against expert labels")
    logger.info(f"{'='*60}")
    evaluate_example(
        output_dir=results_dir,
        eval_dir=eval_dir,
        labels_path=labels_path,
    )

    logger.info(f"\n{'='*60}")
    logger.info("Evaluation complete")
    logger.info(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Two-stage citator pipeline: extraction + Kimi classification")
    parser.add_argument("--input-dir", default="../data", help="Path to shared input data directory")
    parser.add_argument("--output-dir", required=True, help="Path to experiment data directory for results")
    parser.add_argument("--txt-path", default=None, help="Path to cluster ID list file (default: input_dir/example.txt)")
    parser.add_argument("--opinion-dir", default=None, help="Override opinion text directory")
    parser.add_argument("--labels", default="0410.csv", help="Benchmark label filename in data/benchmark_original/ (e.g., 0410.csv)")
    parser.add_argument("--reevaluate", action="store_true", help="Re-evaluate flagged results with Sonnet after Kimi classification")
    parser.add_argument("--evaluate", action="store_true", help="Run full pipeline + evaluation")
    parser.add_argument("--evaluate-only", action="store_true", help="Run only evaluation on existing results")
    parser.add_argument("--postprocess-only", action="store_true", help="Re-run post-processing + evaluation on existing parsed results")
    parser.add_argument("--resume", action="store_true", help="Resume from previously saved incremental results")
    args = parser.parse_args()

    labels_path = os.path.join(args.input_dir, "benchmark_original", args.labels)

    if args.evaluate_only:
        evaluate_only(output_dir=args.output_dir, labels_path=labels_path)
    elif args.postprocess_only:
        postprocess_only(output_dir=args.output_dir, labels_path=labels_path)
    else:
        txt_path = args.txt_path or os.path.join(args.input_dir, "example.txt")
        run(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            txt_path=txt_path,
            opinion_dir=args.opinion_dir,
            labels_path=labels_path,
            reevaluate=args.reevaluate,
            evaluate=args.evaluate,
            resume=args.resume,
        )


if __name__ == "__main__":
    main()
