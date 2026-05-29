"""S3 + Bedrock batch helpers for the two-stage citator pipeline.

S3 layout per run (matches db_design.md):

    s3://{bucket}/Citator/runs/{run_id}/
        manifest.json
        stage1/
            input.jsonl                    (one record per (cluster_id, page))
            raw/output.jsonl.out
            parsed/{cluster_id}.json       (sections + extracted citations)
        stage2/
            input.jsonl                    (one record per (cluster_id, group_idx))
            raw/output.jsonl.out
            parsed/{cluster_id}.json       (per-citation classifications)
"""

import datetime
import hashlib
import json
import logging
import os
import time
import uuid

from utils.bedrock_converse_utils import (
    session, config, AWS_REGION, TEMPERATURE,
    haiku_extraction_tool_spec, kimi_classification_tool_spec,
    citation_grouping_tool_spec,
    MODEL_CAPS,
)
from utils.preprocess import split_opinion_to_pages
from utils.section_utils import split_into_sections, annotate_opinion_with_sections

logger = logging.getLogger(__name__)

# ── Models locked for the two-stage production pipeline ──
EXTRACTION_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
CLASSIFICATION_MODEL_ID = "moonshotai.kimi-k2.5"

# ── S3 / IAM config ──
S3_BUCKET = os.getenv("CITATOR_S3_BUCKET", "")
S3_RUNS_PREFIX = "Citator/runs"
BATCH_ROLE_ARN = os.getenv("CITATOR_BATCH_ROLE_ARN", "")


def _validate_batch_config():
    if not S3_BUCKET:
        raise ValueError(
            "S3_BUCKET is not configured. Set the CITATOR_S3_BUCKET environment variable."
        )
    if not BATCH_ROLE_ARN:
        raise ValueError(
            "BATCH_ROLE_ARN is not configured. Set the CITATOR_BATCH_ROLE_ARN environment variable "
            "to an IAM role ARN with Bedrock and S3 access."
        )


# ── S3 path helpers ──

def s3_run_prefix(run_id):
    return f"{S3_RUNS_PREFIX}/{run_id}"


def s3_manifest_key(run_id):
    return f"{s3_run_prefix(run_id)}/manifest.json"


def s3_stage1_input_key(run_id):
    return f"{s3_run_prefix(run_id)}/stage1/input.jsonl"


def s3_stage1_raw_prefix(run_id):
    return f"{s3_run_prefix(run_id)}/stage1/raw/"


def s3_stage1_parsed_prefix(run_id):
    return f"{s3_run_prefix(run_id)}/stage1/parsed/"


def s3_stage1_parsed_key(run_id, cluster_id):
    return f"{s3_stage1_parsed_prefix(run_id)}{cluster_id}.json"


def s3_stage2_input_key(run_id):
    return f"{s3_run_prefix(run_id)}/stage2/input.jsonl"


def s3_stage2_raw_prefix(run_id):
    return f"{s3_run_prefix(run_id)}/stage2/raw/"


def s3_stage2_parsed_prefix(run_id):
    return f"{s3_run_prefix(run_id)}/stage2/parsed/"


def s3_stage2_parsed_key(run_id, cluster_id):
    return f"{s3_stage2_parsed_prefix(run_id)}{cluster_id}.json"


# Pipeline migration Phase 2 (citation grouping) — separate from the legacy
# two-stage layout so a Phase 2 run doesn't collide with a stage 1/2 run.

def s3_phase2_input_key(run_id):
    return f"{s3_run_prefix(run_id)}/phase2/input.jsonl"


def s3_phase2_raw_prefix(run_id):
    return f"{s3_run_prefix(run_id)}/phase2/raw/"


def s3_phase2_calls_key(run_id):
    return f"{s3_run_prefix(run_id)}/phase2/calls.json"


def s3_uri(key):
    return f"s3://{S3_BUCKET}/{key}"


# ── S3 I/O ──

def _s3_client():
    _validate_batch_config()
    return session.client("s3", region_name=AWS_REGION)


def upload_file(local_path, s3_key):
    client = _s3_client()
    client.upload_file(local_path, S3_BUCKET, s3_key)
    logger.info(f"Uploaded {local_path} -> {s3_uri(s3_key)}")
    return s3_uri(s3_key)


def upload_json(obj, s3_key):
    client = _s3_client()
    client.put_object(
        Bucket=S3_BUCKET, Key=s3_key,
        Body=json.dumps(obj, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(f"Wrote JSON -> {s3_uri(s3_key)}")
    return s3_uri(s3_key)


def download_to_file(s3_key, local_path):
    client = _s3_client()
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    client.download_file(S3_BUCKET, s3_key, local_path)
    logger.info(f"Downloaded {s3_uri(s3_key)} -> {local_path}")


def read_json(s3_key):
    client = _s3_client()
    response = client.get_object(Bucket=S3_BUCKET, Key=s3_key)
    return json.loads(response["Body"].read().decode("utf-8"))


def list_keys(prefix):
    client = _s3_client()
    keys = []
    continuation_token = None
    while True:
        kwargs = {"Bucket": S3_BUCKET, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            keys.append(obj["Key"])
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
    return keys


# ── Run identity ──

def generate_run_id():
    return str(uuid.uuid4())


def prompt_sha(prompt_text):
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


# ── JSONL record builders ──

MAX_TOKENS = 8192


def _anthropic_tool(tool_spec):
    """Convert a Bedrock Converse toolSpec to native Anthropic tool format."""
    spec = tool_spec["toolSpec"]
    return {
        "name": spec["name"],
        "description": spec["description"],
        "input_schema": spec["inputSchema"]["json"],
    }


def build_extraction_record(record_id, page_text, system_prompt, model_id=EXTRACTION_MODEL_ID):
    """Build a Bedrock batch JSONL record for Stage 1 (Haiku extraction).

    Uses the native Anthropic Messages API format — Bedrock batch parses
    modelInput as the model's native schema, not the Converse schema.
    """
    caps = MODEL_CAPS.get(model_id, {})
    if not caps.get("tool_use"):
        raise ValueError(f"Stage 1 model {model_id} does not support tool_use")

    tool = _anthropic_tool(haiku_extraction_tool_spec)
    model_input = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"<opinion>\n{page_text}\n</opinion>"}
                ],
            }
        ],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
    }
    return {"recordId": str(record_id), "modelInput": model_input}


CITATION_GROUPING_MAX_TOKENS = 16384  # Phase 2 emits ~80 tokens per cited case; allow headroom.


def build_citation_grouping_record(record_id, llm_input, system_prompt, model_id=EXTRACTION_MODEL_ID):
    """Build a Bedrock batch JSONL record for pipeline-migration Phase 2.

    Args:
        record_id: stable id of the form `{cluster_id}_call_{call_idx}` so
            outputs can be routed back to clusters during collect.
        llm_input: dict shaped like `{"opinions": [...]}` per the
            citation_grouping schema (NOT JSON-encoded; this function encodes).
        system_prompt: the `citation_grouping` instruction text.
        model_id: must be a tool_use-capable model (Haiku 4.5 by default).
    """
    caps = MODEL_CAPS.get(model_id, {})
    if not caps.get("tool_use"):
        raise ValueError(f"Citation grouping model {model_id} does not support tool_use")

    tool = _anthropic_tool(citation_grouping_tool_spec)
    user_text = json.dumps(llm_input, ensure_ascii=False)
    model_input = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": CITATION_GROUPING_MAX_TOKENS,
        "temperature": TEMPERATURE,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": user_text}]}
        ],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
    }
    return {"recordId": str(record_id), "modelInput": model_input}


def extract_citation_grouping_emission(model_output):
    """Pull the `{"cited_cases": [...]}` dict from a Bedrock batch model output.

    `model_output` is the per-record `modelOutput` field from the batch output
    JSONL — for Anthropic models it follows the native Messages API shape
    `{"content": [{"type": "tool_use", "input": {...}}, ...]}`.
    Returns `{}` if no tool_use block is present.
    """
    if not model_output:
        return {}
    for block in model_output.get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input", {}) or {}
    return {}


def build_classification_record(record_id, user_text, system_prompt, model_id=CLASSIFICATION_MODEL_ID):
    """Build a Bedrock batch JSONL record for Stage 2 (Kimi classification).

    Kimi K2.5 on Bedrock uses an OpenAI-compatible chat completions format.
    It doesn't support tool_use, so we prepend the system prompt and append
    the JSON schema to the user content, and the parser pulls JSON from text.
    """
    schema = kimi_classification_tool_spec["toolSpec"]["inputSchema"]["json"]

    combined = (
        f"{system_prompt}\n\n{user_text}\n\n"
        "Return your response as a JSON object conforming to this schema:\n"
        f"{json.dumps(schema, indent=2)}"
    )

    model_input = {
        "messages": [
            {"role": "user", "content": combined},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    return {"recordId": str(record_id), "modelInput": model_input}


# ── Stage 1 input prep ──

def prepare_stage1_jsonl(cluster_ids, opinion_dir, system_prompt, local_path,
                         model_id=EXTRACTION_MODEL_ID):
    """Build the Stage 1 JSONL locally and return (records_written, sections_per_cluster).

    Each opinion is split into sections, annotated with [Section Sn] markers,
    then paginated. Each page becomes one JSONL record. The sections list
    for each cluster is returned so the caller can persist it alongside
    parsed Stage 1 output.
    """
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    sections_per_cluster = {}
    n_records = 0
    n_paginated = 0

    with open(local_path, "w", encoding="utf-8") as f:
        for cluster_id in cluster_ids:
            opinion_path = os.path.join(opinion_dir, f"{cluster_id}.txt")
            if not os.path.exists(opinion_path):
                logger.warning(f"Missing opinion text for cluster_id={cluster_id} ({opinion_path}); skipping")
                continue
            with open(opinion_path, "r", encoding="utf-8") as opf:
                opinion_text = opf.read()

            sections = split_into_sections(opinion_text)
            sections_per_cluster[str(cluster_id)] = sections
            annotated = annotate_opinion_with_sections(opinion_text, sections)
            pages = split_opinion_to_pages(annotated)

            if len(pages) == 1:
                record = build_extraction_record(
                    str(cluster_id), pages[0], system_prompt, model_id=model_id,
                )
                f.write(json.dumps(record) + "\n")
                n_records += 1
            else:
                n_paginated += 1
                for page_idx, page in enumerate(pages, 1):
                    record = build_extraction_record(
                        f"{cluster_id}_page_{page_idx}", page, system_prompt, model_id=model_id,
                    )
                    f.write(json.dumps(record) + "\n")
                    n_records += 1
                logger.info(f"cluster_id={cluster_id}: {len(pages)} pages")

    logger.info(
        f"Wrote {n_records} extraction records to {local_path} "
        f"({len(sections_per_cluster)} clusters, {n_paginated} paginated)"
    )
    return n_records, sections_per_cluster


# ── Bedrock batch ──

def submit_batch_job(job_name, s3_input_uri, s3_output_uri, model_id):
    """Submit a Bedrock batch inference job. Returns the job ARN."""
    _validate_batch_config()
    bedrock_client = session.client("bedrock", region_name=AWS_REGION, config=config)
    response = bedrock_client.create_model_invocation_job(
        jobName=job_name,
        modelId=model_id,
        roleArn=BATCH_ROLE_ARN,
        inputDataConfig={
            "s3InputDataConfig": {"s3Uri": s3_input_uri, "s3InputFormat": "JSONL"}
        },
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": s3_output_uri}},
    )
    job_arn = response["jobArn"]
    logger.info(f"Submitted batch job {job_name} ({model_id}): {job_arn}")
    return job_arn


def check_job_status(job_arn):
    bedrock_client = session.client("bedrock", region_name=AWS_REGION, config=config)
    response = bedrock_client.get_model_invocation_job(jobIdentifier=job_arn)
    return {
        "jobArn": response["jobArn"],
        "jobName": response.get("jobName", ""),
        "status": response["status"],
        "message": response.get("message", ""),
    }


def wait_for_jobs(job_arns, poll_interval=60):
    pending = set(job_arns)
    results = {}
    while pending:
        for job_arn in list(pending):
            status = check_job_status(job_arn)
            logger.info(f"Job {status['jobName']}: {status['status']}")
            if status["status"] in ("Completed", "Failed", "Stopped"):
                results[job_arn] = status
                pending.discard(job_arn)
        if pending:
            logger.info(f"Waiting for {len(pending)} jobs... (polling every {poll_interval}s)")
            time.sleep(poll_interval)
    return results


# ── Manifest ──

def write_manifest(run_id, manifest):
    manifest = {
        "run_id": run_id,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        **manifest,
    }
    return upload_json(manifest, s3_manifest_key(run_id))


def read_manifest(run_id):
    return read_json(s3_manifest_key(run_id))


def update_manifest(run_id, updates):
    manifest = read_manifest(run_id)
    manifest.update(updates)
    manifest["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    upload_json(manifest, s3_manifest_key(run_id))
    return manifest
