"""Parsers for Bedrock batch outputs (Stage 1 extraction + Stage 2 classification).

Both stages produce raw JSONL output files in S3. This module:
  1. Downloads the raw output to a local scratch dir.
  2. Parses each line into model output (toolUse for Haiku, text for Kimi).
  3. Joins records back to clusters/citations, applying the
     extraction_failed / partial_extraction / classification_failed
     processing_status bookkeeping.
  4. Writes per-cluster parsed JSON to S3.
"""

import json
import logging
import os
import re
from collections import defaultdict

from json_repair import repair_json

from utils.batch_utils import (
    download_to_file, list_keys, read_json, upload_json,
    s3_stage1_parsed_key, s3_stage1_parsed_prefix,
    s3_stage2_parsed_key, s3_stage2_parsed_prefix,
    s3_sonnet_parsed_key, s3_sonnet_parsed_prefix,
    s3_reeval_parsed_key,
)
from utils.reevaluate import extract_reassessments

logger = logging.getLogger(__name__)

_PAGE_PATTERN = re.compile(r"^(.+)_page_(\d+)$")
_GROUP_PATTERN = re.compile(r"^(.+?)__g(\d+)$")


# ── Generic raw-output download ──

def download_raw_outputs(s3_prefix, local_dir):
    """Download every .jsonl.out file under s3_prefix to local_dir.

    Returns the list of local file paths.
    """
    keys = [k for k in list_keys(s3_prefix) if k.endswith(".jsonl.out")]
    os.makedirs(local_dir, exist_ok=True)
    paths = []
    for key in keys:
        local_path = os.path.join(local_dir, os.path.basename(key))
        download_to_file(key, local_path)
        paths.append(local_path)
    if not paths:
        logger.warning(f"No raw output files found under {s3_prefix}")
    return paths


# ── JSON extraction from a model output block ──

def _extract_tool_input(content_blocks):
    """Pull a tool_use block's input out of an Anthropic-native content list."""
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("input", {})
        # Also accept Converse-style toolUse just in case
        if isinstance(block, dict) and "toolUse" in block:
            return block["toolUse"].get("input", {})
    return None


def _extract_text(content_blocks):
    """Pull text out of an Anthropic-native content list or a flat string body."""
    if isinstance(content_blocks, str):
        return content_blocks
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "text" and "text" in block:
                return block["text"]
            if "text" in block and "type" not in block:
                # Converse-style {"text": "..."}
                return block["text"]
    return ""


def _parse_text_json(text):
    """Pull the first JSON object out of a model text response."""
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return repair_json(match.group(1), return_objects=True) or {}
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return repair_json(match.group(0), return_objects=True) or {}
    return {}


def _parse_record_payload(record):
    """Return (record_id, payload_dict, error_msg).

    Handles three possible modelOutput shapes:
      - Anthropic native: top-level {content, stop_reason, usage:{input_tokens, output_tokens}}
      - Converse:         {output: {message: {content}}, stopReason, usage:{inputTokens, ...}}
      - OpenAI-compatible (Kimi/Moonshot): {choices:[{message:{content}}], usage:{prompt_tokens, completion_tokens}}
    """
    record_id = record.get("recordId", "")
    if record.get("error"):
        return record_id, None, str(record["error"])

    output = record.get("modelOutput", {})

    # Try Anthropic native first
    content = output.get("content")
    stop_reason = output.get("stop_reason")
    usage = output.get("usage", {})
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    # Converse fallback
    if content is None:
        content = output.get("output", {}).get("message", {}).get("content", [])
        stop_reason = stop_reason or output.get("stopReason", "")
        input_tokens = input_tokens if input_tokens is not None else usage.get("inputTokens", 0)
        output_tokens = output_tokens if output_tokens is not None else usage.get("outputTokens", 0)

    # OpenAI-compatible fallback (Kimi)
    if (content is None or content == []) and "choices" in output:
        choice = (output.get("choices") or [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        stop_reason = stop_reason or choice.get("finish_reason", "")
        input_tokens = input_tokens if input_tokens is not None else usage.get("prompt_tokens", 0)
        output_tokens = output_tokens if output_tokens is not None else usage.get("completion_tokens", 0)

    model = output.get("model") or record.get("modelInput", {}).get("modelId", "")

    tool_input = _extract_tool_input(content) if isinstance(content, list) else None
    if tool_input is not None:
        result = tool_input
    else:
        text = _extract_text(content)
        result = _parse_text_json(text) if text else {}

    payload = {
        "model": model,
        "stop_reason": stop_reason or "",
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "result": result,
    }
    return record_id, payload, None


# ── Stage 1: extraction ──

def _normalize_cited_cases(result):
    """Pull a list of cited-case dicts out of whatever shape the model returned."""
    cited = result.get("citedCases", []) if isinstance(result, dict) else []
    if isinstance(cited, str):
        try:
            cited = json.loads(cited)
        except json.JSONDecodeError:
            cited = repair_json(cited, return_objects=True) or []
    if isinstance(cited, dict):
        cited = cited.get("citedCases", [])
    return cited if isinstance(cited, list) else []


def parse_stage1_outputs(raw_files, sections_per_cluster):
    """Parse Stage 1 raw outputs into per-cluster parsed records.

    Returns {cluster_id: parsed_dict}. parsed_dict matches the production
    schema and includes:
      - cluster_id
      - sections: list of {id, text, start_char, end_char}
      - extracted_citations: list of {citation_idx, mainCitationString,
            caseName, section_ids}
      - n_pages: total pages submitted
      - processing_status: ok | extraction_failed | partial_extraction
      - failed_pages: list of failed page indices (when partial)
    """
    # First pass: parse every record by recordId
    record_payloads = {}
    record_errors = {}
    for path in raw_files:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record_id, payload, err = _parse_record_payload(record)
                if err:
                    record_errors[record_id] = err
                    logger.warning(f"Stage 1 record {record_id} failed: {err}")
                else:
                    record_payloads[record_id] = payload

    # Group by cluster_id, separating per-page records
    cluster_pages = defaultdict(dict)  # cluster_id -> {page_num: payload}
    cluster_single = {}                # cluster_id -> payload
    cluster_failures = defaultdict(list)  # cluster_id -> [page_num | None]

    for record_id, err in record_errors.items():
        match = _PAGE_PATTERN.match(record_id)
        if match:
            cluster_id, page_num = match.group(1), int(match.group(2))
            cluster_failures[cluster_id].append(page_num)
        else:
            cluster_failures[record_id].append(None)

    for record_id, payload in record_payloads.items():
        match = _PAGE_PATTERN.match(record_id)
        if match:
            cluster_id, page_num = match.group(1), int(match.group(2))
            cluster_pages[cluster_id][page_num] = payload
        else:
            cluster_single[record_id] = payload

    # Build per-cluster output. Iterate over the union of all known cluster IDs
    # from sections_per_cluster + outputs (covers all-pages-failed case where
    # nothing came back successfully).
    parsed_by_cluster = {}
    all_cluster_ids = set(sections_per_cluster.keys()) | set(cluster_pages.keys()) \
        | set(cluster_single.keys()) | set(cluster_failures.keys())

    for cluster_id in all_cluster_ids:
        sections = sections_per_cluster.get(cluster_id, [])
        successful_payloads = []
        n_pages = 0
        failed_pages = sorted([p for p in cluster_failures.get(cluster_id, []) if p is not None])

        if cluster_id in cluster_single:
            successful_payloads.append(cluster_single[cluster_id])
            n_pages = 1
        elif cluster_id in cluster_pages:
            for page_num in sorted(cluster_pages[cluster_id].keys()):
                successful_payloads.append(cluster_pages[cluster_id][page_num])
            n_pages = len(successful_payloads) + len(failed_pages)
        else:
            # All pages failed (or single-record cluster failed)
            n_pages = len(failed_pages) or 1

        if not successful_payloads:
            status = "extraction_failed"
        elif failed_pages:
            status = "partial_extraction"
        else:
            status = "ok"

        # Merge cited cases across pages: dedup by mainCitationString (fall back
        # to caseName), union section_ids.
        merged = {}
        order = []
        for payload in successful_payloads:
            for cc in _normalize_cited_cases(payload["result"]):
                if not isinstance(cc, dict):
                    continue
                key = cc.get("mainCitationString") or f"caseName::{cc.get('caseName')}"
                if key in merged:
                    existing_ids = set(merged[key].get("section_ids", []))
                    new_ids = set(cc.get("section_ids", []))
                    merged[key]["section_ids"] = sorted(existing_ids | new_ids)
                else:
                    merged[key] = {
                        "mainCitationString": cc.get("mainCitationString"),
                        "caseName": cc.get("caseName"),
                        "section_ids": list(cc.get("section_ids", [])),
                    }
                    order.append(key)

        extracted = []
        for idx, key in enumerate(order):
            extracted.append({
                "citation_idx": idx,
                "mainCitationString": merged[key]["mainCitationString"],
                "caseName": merged[key]["caseName"],
                "section_ids": merged[key]["section_ids"],
            })

        parsed_by_cluster[cluster_id] = {
            "cluster_id": cluster_id,
            "sections": sections,
            "extracted_citations": extracted,
            "n_pages": n_pages,
            "failed_pages": failed_pages,
            "processing_status": status,
        }

    return parsed_by_cluster


def write_stage1_parsed(run_id, parsed_by_cluster):
    """Upload each cluster's parsed Stage 1 JSON to S3."""
    for cluster_id, parsed in parsed_by_cluster.items():
        upload_json(parsed, s3_stage1_parsed_key(run_id, cluster_id))
    logger.info(f"Wrote {len(parsed_by_cluster)} parsed Stage 1 files to S3")


def read_stage1_parsed(run_id, scratch_dir):
    """Download every Stage 1 parsed JSON for a run.

    Returns {cluster_id: parsed_dict}.
    """
    keys = [k for k in list_keys(s3_stage1_parsed_prefix(run_id)) if k.endswith(".json")]
    os.makedirs(scratch_dir, exist_ok=True)
    parsed_by_cluster = {}
    for key in keys:
        local_path = os.path.join(scratch_dir, os.path.basename(key))
        download_to_file(key, local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cid = str(data.get("cluster_id", os.path.basename(key).replace(".json", "")))
        parsed_by_cluster[cid] = data
    return parsed_by_cluster


# ── Stage 2: classification ──

def _normalize_classifications(result):
    """Pull a list of classification dicts out of whatever shape the model returned."""
    cited = result.get("citedCases", []) if isinstance(result, dict) else []
    if isinstance(cited, str):
        try:
            cited = json.loads(cited)
        except json.JSONDecodeError:
            cited = repair_json(cited, return_objects=True) or []
    if isinstance(cited, dict):
        cited = cited.get("citedCases", [])
    return cited if isinstance(cited, list) else []


def parse_stage2_outputs(raw_files, groups_by_cluster, stage1_by_cluster):
    """Parse Stage 2 raw outputs into per-citation classifications.

    Args:
        raw_files: list of local paths to downloaded .jsonl.out files
        groups_by_cluster: {cluster_id: [{group_idx, section_ids,
            citation_indices}, ...]} — the deterministic grouping replayed
            by submit-classification.
        stage1_by_cluster: {cluster_id: stage1_parsed_dict} — used for the
            citation metadata (mainCitationString, caseName, section_ids)
            and to know which clusters were extraction_failed (skipped).

    Returns {cluster_id: stage2_parsed_dict} with shape:
        {
            "cluster_id": ...,
            "results": [{citation_idx, mainCitationString, caseName,
                section_ids, actingCase, caseHistory, treatment,
                opinionType, quote, rationale, processing_status}, ...],
            "processing_status": ok | partial_classification |
                classification_failed | extraction_failed,
        }
    """
    record_payloads = {}
    record_errors = {}
    for path in raw_files:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record_id, payload, err = _parse_record_payload(record)
                if err:
                    record_errors[record_id] = err
                    logger.warning(f"Stage 2 record {record_id} failed: {err}")
                else:
                    record_payloads[record_id] = payload

    # Group payloads by cluster
    cluster_groups = defaultdict(dict)  # cluster_id -> {group_idx: payload}
    cluster_group_failures = defaultdict(set)  # cluster_id -> {failed group_idx, ...}
    for record_id, payload in record_payloads.items():
        match = _GROUP_PATTERN.match(record_id)
        if match:
            cluster_groups[match.group(1)][int(match.group(2))] = payload
    for record_id in record_errors:
        match = _GROUP_PATTERN.match(record_id)
        if match:
            cluster_group_failures[match.group(1)].add(int(match.group(2)))

    parsed_by_cluster = {}
    for cluster_id, stage1 in stage1_by_cluster.items():
        stage1_status = stage1.get("processing_status", "ok")
        citations = stage1.get("extracted_citations", [])
        groups = groups_by_cluster.get(cluster_id, [])

        # If extraction failed entirely, propagate
        if stage1_status == "extraction_failed":
            parsed_by_cluster[cluster_id] = {
                "cluster_id": cluster_id,
                "results": [],
                "processing_status": "extraction_failed",
            }
            continue

        # Default each citation to a fallback; overwrite from group results below
        results = [_default_classification(c) for c in citations]

        any_group_failed = False
        any_group_succeeded = False

        for group in groups:
            group_idx = group["group_idx"]
            citation_indices = group["citation_indices"]
            payload = cluster_groups.get(cluster_id, {}).get(group_idx)

            if payload is None:
                any_group_failed = True
                continue

            classifications = _normalize_classifications(payload["result"])
            classifications_by_id = {
                c.get("citedCaseId"): c
                for c in classifications
                if isinstance(c, dict) and isinstance(c.get("citedCaseId"), int)
            }

            group_had_any_match = False
            for position, citation_idx in enumerate(citation_indices, 1):
                cls = classifications_by_id.get(position)
                if cls:
                    group_had_any_match = True
                    results[citation_idx] = _merge_classification(
                        citations[citation_idx], cls, status="ok",
                    )
                # else: leaves the default fallback in place

            if group_had_any_match:
                any_group_succeeded = True
            else:
                any_group_failed = True

        if not groups:
            cluster_status = stage1_status if stage1_status != "ok" else "ok"
        elif not any_group_succeeded:
            cluster_status = "classification_failed"
        elif any_group_failed:
            cluster_status = "partial_classification"
        else:
            cluster_status = "partial_extraction" if stage1_status == "partial_extraction" else "ok"

        parsed_by_cluster[cluster_id] = {
            "cluster_id": cluster_id,
            "results": results,
            "processing_status": cluster_status,
        }

    return parsed_by_cluster


def _default_classification(citation):
    """Fallback row used when a citation has no successful classification."""
    return {
        "citation_idx": citation.get("citation_idx"),
        "mainCitationString": citation.get("mainCitationString"),
        "caseName": citation.get("caseName"),
        "section_ids": citation.get("section_ids", []),
        "actingCase": "Citing Case",
        "caseHistory": "Citing Reference",
        "treatment": "Cited by",
        "opinionType": "Lead",
        "quote": None,
        "rationale": "Classification failed; defaulting to Cited by.",
        "processing_status": "classification_failed",
    }


def _merge_classification(citation, cls, status):
    return {
        "citation_idx": citation.get("citation_idx"),
        "mainCitationString": citation.get("mainCitationString"),
        "caseName": citation.get("caseName"),
        "section_ids": citation.get("section_ids", []),
        "actingCase": cls.get("actingCase", ""),
        "caseHistory": cls.get("caseHistory", ""),
        "treatment": cls.get("treatment", ""),
        "opinionType": cls.get("opinionType", ""),
        "quote": cls.get("quote"),
        "rationale": cls.get("rationale", ""),
        "processing_status": status,
    }


def write_stage2_parsed(run_id, parsed_by_cluster):
    for cluster_id, parsed in parsed_by_cluster.items():
        upload_json(parsed, s3_stage2_parsed_key(run_id, cluster_id))
    logger.info(f"Wrote {len(parsed_by_cluster)} parsed Stage 2 files to S3")


def read_stage2_parsed(run_id, scratch_dir):
    keys = [k for k in list_keys(s3_stage2_parsed_prefix(run_id)) if k.endswith(".json")]
    os.makedirs(scratch_dir, exist_ok=True)
    parsed = {}
    for key in keys:
        local_path = os.path.join(scratch_dir, os.path.basename(key))
        download_to_file(key, local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cid = str(data.get("cluster_id", os.path.basename(key).replace(".json", "")))
        parsed[cid] = data
    return parsed


# ── Single-stage Sonnet ──

def parse_sonnet_outputs(raw_files, pages_per_cluster):
    """Parse single-stage Sonnet raw outputs into per-cluster results.

    Args:
        raw_files: list of local paths to downloaded .jsonl.out files
        pages_per_cluster: {cluster_id: n_pages_submitted} — used to detect
            partial-prediction failures when only some pages came back.

    Returns {cluster_id: parsed_dict} with the same `results` shape as
    parse_stage2_outputs so the `collect` step's flattener works unchanged:
        {
            "cluster_id": ...,
            "results": [{mainCitationString, caseName, actingCase,
                caseHistory, treatment, opinionType, quote, rationale,
                section_ids, processing_status}, ...],
            "n_pages": ...,
            "failed_pages": [...],
            "processing_status": ok | partial_prediction | prediction_failed,
        }
    Single-stage Sonnet doesn't carry section_ids per case (no section
    pre-pass), so section_ids is always [] — kept for downstream schema
    parity with the two-stage output.
    """
    record_payloads = {}
    record_errors = {}
    for path in raw_files:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record_id, payload, err = _parse_record_payload(record)
                if err:
                    record_errors[record_id] = err
                    logger.warning(f"Sonnet record {record_id} failed: {err}")
                else:
                    record_payloads[record_id] = payload

    cluster_pages = defaultdict(dict)
    cluster_single = {}
    cluster_failures = defaultdict(list)

    for record_id, err in record_errors.items():
        match = _PAGE_PATTERN.match(record_id)
        if match:
            cluster_failures[match.group(1)].append(int(match.group(2)))
        else:
            cluster_failures[record_id].append(None)

    for record_id, payload in record_payloads.items():
        match = _PAGE_PATTERN.match(record_id)
        if match:
            cluster_pages[match.group(1)][int(match.group(2))] = payload
        else:
            cluster_single[record_id] = payload

    parsed_by_cluster = {}
    all_cluster_ids = set(pages_per_cluster.keys()) | set(cluster_pages.keys()) \
        | set(cluster_single.keys()) | set(cluster_failures.keys())

    for cluster_id in all_cluster_ids:
        n_pages = pages_per_cluster.get(cluster_id, 0)
        successful_payloads = []
        failed_pages = sorted([p for p in cluster_failures.get(cluster_id, []) if p is not None])

        if cluster_id in cluster_single:
            successful_payloads.append(cluster_single[cluster_id])
            n_pages = n_pages or 1
        elif cluster_id in cluster_pages:
            for page_num in sorted(cluster_pages[cluster_id].keys()):
                successful_payloads.append(cluster_pages[cluster_id][page_num])

        if not successful_payloads:
            status = "prediction_failed"
        elif failed_pages:
            status = "partial_prediction"
        else:
            status = "ok"

        # Merge cited cases across pages: dedup by mainCitationString
        # (fall back to caseName); on dedup, keep the longest quote +
        # most severe treatment (rank from postprocess.TREATMENT_RANK is
        # applied later in clean_treatments/dedup, so here just keep first).
        merged = {}
        order = []
        for payload in successful_payloads:
            for cc in _normalize_cited_cases(payload["result"]):
                if not isinstance(cc, dict):
                    continue
                key = cc.get("mainCitationString") or f"caseName::{cc.get('caseName')}"
                if key in merged:
                    # Prefer the entry with a non-null quote; otherwise keep first.
                    if not merged[key].get("quote") and cc.get("quote"):
                        merged[key] = _normalize_sonnet_case(cc)
                else:
                    merged[key] = _normalize_sonnet_case(cc)
                    order.append(key)

        results = []
        for key in order:
            row = merged[key]
            row["processing_status"] = status if status != "partial_prediction" else "ok"
            results.append(row)

        parsed_by_cluster[cluster_id] = {
            "cluster_id": cluster_id,
            "results": results,
            "n_pages": n_pages,
            "failed_pages": failed_pages,
            "processing_status": status,
        }

    return parsed_by_cluster


def _normalize_sonnet_case(cc):
    """Normalize a single-stage citedCase dict to the result row shape."""
    return {
        "mainCitationString": cc.get("mainCitationString"),
        "caseName": cc.get("caseName"),
        "section_ids": [],
        "actingCase": cc.get("actingCase", ""),
        "caseHistory": cc.get("caseHistory", ""),
        "treatment": cc.get("treatment", ""),
        "opinionType": cc.get("opinionType", ""),
        "quote": cc.get("quote"),
        "rationale": cc.get("rationale", ""),
    }


def write_sonnet_parsed(run_id, parsed_by_cluster):
    for cluster_id, parsed in parsed_by_cluster.items():
        upload_json(parsed, s3_sonnet_parsed_key(run_id, cluster_id))
    logger.info(f"Wrote {len(parsed_by_cluster)} parsed Sonnet files to S3")


def read_sonnet_parsed(run_id, scratch_dir):
    keys = [k for k in list_keys(s3_sonnet_parsed_prefix(run_id)) if k.endswith(".json")]
    os.makedirs(scratch_dir, exist_ok=True)
    parsed = {}
    for key in keys:
        local_path = os.path.join(scratch_dir, os.path.basename(key))
        download_to_file(key, local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cid = str(data.get("cluster_id", os.path.basename(key).replace(".json", "")))
        parsed[cid] = data
    return parsed


# ── Kimi re-evaluation ──

_REEVAL_PATTERN = re.compile(r"^reeval_b(\d+)$")


def parse_reeval_outputs(raw_files):
    """Parse Kimi K2.5 re-eval batch outputs into {batch_idx: reassessments}.

    Each batch record contains 1..N reassessments with `citedCaseId` (1-based
    position within the batch), `treatment`, and `rationale`. The caller maps
    (batch_idx, citedCaseId) back to the original flagged rows via the
    `batches.csv` produced at submit time.
    """
    reassessments_by_batch = {}
    record_errors = {}
    for path in raw_files:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record_id, payload, err = _parse_record_payload(record)
                if err:
                    record_errors[record_id] = err
                    logger.warning(f"Reeval record {record_id} failed: {err}")
                    continue
                match = _REEVAL_PATTERN.match(record_id)
                if not match:
                    logger.warning(f"Reeval record {record_id} has unexpected id format; skipping")
                    continue
                reassessments_by_batch[int(match.group(1))] = extract_reassessments(payload.get("result"))

    logger.info(
        f"Parsed reeval: {sum(len(v) for v in reassessments_by_batch.values())} "
        f"reassessments across {len(reassessments_by_batch)} batches "
        f"({len(record_errors)} batch failures)"
    )
    return reassessments_by_batch


def write_reeval_parsed(run_id, reassessments_by_batch):
    """Upload the consolidated reassessments dict as a single JSON blob."""
    # Keys are ints; JSON requires strings.
    payload = {str(k): v for k, v in reassessments_by_batch.items()}
    upload_json(payload, s3_reeval_parsed_key(run_id))


def read_reeval_parsed(run_id):
    """Read the consolidated reassessments JSON from S3 if present."""
    try:
        payload = read_json(s3_reeval_parsed_key(run_id))
    except Exception as e:
        logger.warning(f"No reeval parsed JSON for run_id={run_id}: {e}")
        return {}
    return {int(k): v for k, v in payload.items()}
