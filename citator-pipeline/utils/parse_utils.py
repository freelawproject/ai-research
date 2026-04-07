import json
import logging
import os
import re
from collections import defaultdict

import pandas as pd
from json_repair import repair_json

from utils.batch_utils import S3_BUCKET, S3_OUTPUT_PREFIX
from utils.bedrock_converse_utils import session, AWS_REGION

logger = logging.getLogger(__name__)

OUTPUT_DIR = "data/output"

# Pattern to detect paginated records: {cluster_id}_page_{n}
_PAGE_PATTERN = re.compile(r"^(.+)_page_(\d+)$")


def _parse_json_response(text):
    """Extract and parse JSON from a text response."""
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return repair_json(match.group(1), return_objects=True)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return repair_json(match.group(0), return_objects=True)
    return {}


def download_batch_output(job_label, local_dir=OUTPUT_DIR):
    """Download batch output JSONL from S3."""
    s3_client = session.client("s3", region_name=AWS_REGION)
    prefix = f"{S3_OUTPUT_PREFIX}/{job_label}/"

    os.makedirs(os.path.join(local_dir, job_label), exist_ok=True)

    response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    output_files = []
    for obj in response.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".jsonl.out"):
            local_path = os.path.join(local_dir, job_label, os.path.basename(key))
            s3_client.download_file(S3_BUCKET, key, local_path)
            output_files.append(local_path)
            logger.info(f"Downloaded {key} to {local_path}")

    return output_files


def _parse_single_record(record):
    """Parse a single batch output record into (record_id, prediction_info) or None."""
    record_id = record.get("recordId", "")
    output = record.get("modelOutput", {})

    if record.get("error"):
        logger.warning(f"Record {record_id} failed: {record['error']}")
        return None

    content = output.get("output", {}).get("message", {}).get("content", [])
    stop_reason = output.get("stopReason", "unknown")
    usage = output.get("usage", {})

    result = {}
    for block in content:
        if "toolUse" in block:
            result = block["toolUse"].get("input", {})
            break
        elif "text" in block:
            result = _parse_json_response(block["text"])
            break

    return record_id, {
        "model": "us.anthropic.claude-sonnet-4-6",
        "stop_reason": stop_reason,
        "input_tokens": usage.get("inputTokens", 0),
        "output_tokens": usage.get("outputTokens", 0),
        "cited_cases": result.get("citedCases", []),
    }


def parse_batch_output(output_files):
    """Parse batch output JSONL files into predictions dict.

    Handles paginated records: records with IDs like '{cluster_id}_page_{n}'
    are combined into a single prediction per cluster_id, merging cited cases
    and summing token counts from all pages.
    """
    # First pass: parse all records
    raw_records = {}
    for file_path in output_files:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                record = json.loads(line.strip())
                parsed = _parse_single_record(record)
                if parsed:
                    record_id, info = parsed
                    raw_records[record_id] = info

    # Second pass: combine paginated records
    predictions = {}
    page_groups = defaultdict(dict)  # cluster_id → {page_num: info}

    for record_id, info in raw_records.items():
        match = _PAGE_PATTERN.match(record_id)
        if match:
            cluster_id = match.group(1)
            page_num = int(match.group(2))
            page_groups[cluster_id][page_num] = info
        else:
            # Single-page record — use directly
            predictions[record_id] = info

    # Combine multi-page records
    for cluster_id, pages in page_groups.items():
        sorted_pages = sorted(pages.items())
        all_cited_cases = []
        total_input_tokens = 0
        total_output_tokens = 0
        stop_reasons = []

        for _, page_info in sorted_pages:
            cited = page_info.get("cited_cases", [])
            if isinstance(cited, str):
                try:
                    cited = json.loads(cited)
                except json.JSONDecodeError:
                    cited = repair_json(cited, return_objects=True)
            all_cited_cases.extend(cited)
            total_input_tokens += page_info.get("input_tokens", 0)
            total_output_tokens += page_info.get("output_tokens", 0)
            stop_reasons.append(page_info.get("stop_reason", ""))

        predictions[cluster_id] = {
            "model": "us.anthropic.claude-sonnet-4-6",
            "stop_reason": "; ".join(stop_reasons),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "num_pages": len(sorted_pages),
            "cited_cases": all_cited_cases,
        }
        logger.info(f"Combined {len(sorted_pages)} pages for cluster_id={cluster_id}")

    return predictions


def save_raw_results(predictions, output_dir):
    """Save raw results CSV."""
    rows = []
    for cluster_id, info in predictions.items():
        rows.append({
            "citing_cluster_id": cluster_id,
            "model": info.get("model", ""),
            "stop_reason": info.get("stop_reason", ""),
            "input_tokens": info.get("input_tokens", 0),
            "output_tokens": info.get("output_tokens", 0),
            "num_pages": info.get("num_pages", 1),
            "cited_cases": info.get("cited_cases", []),
        })
    df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "raw_results.csv")
    df.to_csv(path, index=False)
    logger.info(f"Saved raw results ({len(df)} rows) to {path}")


def save_parsed_results(predictions, output_dir):
    """Save parsed results CSV — one row per cited case."""
    rows = []
    for cluster_id, info in predictions.items():
        cited_cases = info.get("cited_cases", [])
        if isinstance(cited_cases, str):
            try:
                cited_cases = json.loads(cited_cases)
            except json.JSONDecodeError:
                cited_cases = repair_json(cited_cases, return_objects=True)

        if not cited_cases:
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": "", "caseName": "", "actingCase": "",
                "caseHistory": "", "treatment": "", "opinionType": "",
                "quote": "", "rationale": "",
            })
            continue

        for cited_info in cited_cases:
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": cited_info.get("mainCitationString", ""),
                "caseName": cited_info.get("caseName", ""),
                "actingCase": cited_info.get("actingCase", ""),
                "caseHistory": cited_info.get("caseHistory", ""),
                "treatment": cited_info.get("treatment", ""),
                "opinionType": cited_info.get("opinionType", ""),
                "quote": cited_info.get("quote", ""),
                "rationale": cited_info.get("rationale", ""),
            })

    df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "parsed_results.csv")
    df.to_csv(path, index=False)
    logger.info(f"Saved parsed results ({len(df)} rows) to {path}")
    return df


def process_batch(job_label, output_dir=OUTPUT_DIR):
    """Download and parse batch output.

    Returns the parsed DataFrame for downstream post-processing.
    """
    batch_output_dir = os.path.join(output_dir, job_label)
    output_files = download_batch_output(job_label, output_dir)
    if not output_files:
        logger.warning(f"No output files found for {job_label}")
        return None

    predictions = parse_batch_output(output_files)
    save_raw_results(predictions, batch_output_dir)
    parsed_df = save_parsed_results(predictions, batch_output_dir)
    logger.info(f"Processed {len(predictions)} records for {job_label}")
    return parsed_df
