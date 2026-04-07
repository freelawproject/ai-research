import json
import logging
import os
import time

from utils.bedrock_converse_utils import (
    session, config, AWS_REGION, TEMPERATURE, tool_spec,
)
from utils.instructions import citator
from utils.preprocess import split_opinion_to_pages

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-sonnet-4-6"
SYSTEM_PROMPT = citator

S3_BUCKET = os.getenv("CITATOR_S3_BUCKET", "")
S3_INPUT_PREFIX = "Citator/input"
S3_OUTPUT_PREFIX = "Citator/output"
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

INPUT_DIR = "data/input"
OUTPUT_DIR = "data/output"


def load_cluster_ids(txt_path):
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()
    return [id.strip() for id in content.split(",") if id.strip()]


def load_opinion_text(citing_dir, cluster_id):
    file_path = os.path.join(citing_dir, f"{cluster_id}.txt")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def build_jsonl_record(record_id, opinion_text, model_id=None, system_prompt=None):
    """Build a single JSONL record for Bedrock batch inference using Converse API format.

    Args:
        record_id: Unique record identifier. For single-page opinions this is the
            cluster_id. For paginated opinions this is '{cluster_id}_page_{n}'.
        opinion_text: The opinion text (or page of opinion text) to process.
        model_id: Override model ID (defaults to MODULE_ID).
        system_prompt: Override system prompt (defaults to SYSTEM_PROMPT).
    """
    model = model_id or MODEL_ID
    prompt = system_prompt or SYSTEM_PROMPT

    model_input = {
        "modelId": model,
        "system": [{"text": prompt}],
        "messages": [
            {
                "role": "user",
                "content": [{"text": f"<opinion>\n{opinion_text}\n</opinion>"}],
            }
        ],
        "inferenceConfig": {"temperature": TEMPERATURE},
    }

    # Only add tool config for the default citator prompt
    if system_prompt is None:
        model_input["toolConfig"] = {
            "tools": [tool_spec],
            "toolChoice": {"tool": {"name": "analyze_cited_case_treatment"}},
        }

    return {"recordId": str(record_id), "modelInput": model_input}


def prepare_jsonl(job_label, cluster_ids, citing_dir, output_dir=OUTPUT_DIR, model_id=None, system_prompt=None):
    """Prepare a JSONL file for a batch job.

    Long opinions are split into overlapping pages using the same pagination
    logic as the real-time pipeline (experiments_04032026). Each page becomes
    a separate JSONL record with recordId '{cluster_id}_page_{n}'.
    """
    jsonl_path = os.path.join(output_dir, f"{job_label}.jsonl")
    os.makedirs(output_dir, exist_ok=True)

    records = []
    total_pages = 0
    paginated_opinions = 0

    for cluster_id in cluster_ids:
        try:
            opinion_text = load_opinion_text(citing_dir, cluster_id)
            pages = split_opinion_to_pages(opinion_text)

            if len(pages) == 1:
                record = build_jsonl_record(cluster_id, opinion_text, model_id=model_id, system_prompt=system_prompt)
                records.append(record)
                total_pages += 1
            else:
                paginated_opinions += 1
                for i, page in enumerate(pages, 1):
                    record_id = f"{cluster_id}_page_{i}"
                    record = build_jsonl_record(record_id, page, model_id=model_id, system_prompt=system_prompt)
                    records.append(record)
                    total_pages += 1
                logger.info(f"cluster_id={cluster_id}: split into {len(pages)} pages")

        except Exception as e:
            logger.error(f"Failed to load opinion for {cluster_id}: {e}")

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    logger.info(
        f"Prepared {len(records)} records for {job_label} at {jsonl_path} "
        f"({len(cluster_ids)} opinions, {paginated_opinions} paginated, {total_pages} total pages)"
    )
    return jsonl_path


def upload_to_s3(local_path, s3_key):
    """Upload a local file to S3."""
    _validate_batch_config()
    s3_client = session.client("s3", region_name=AWS_REGION)
    s3_client.upload_file(local_path, S3_BUCKET, s3_key)
    s3_uri = f"s3://{S3_BUCKET}/{s3_key}"
    logger.info(f"Uploaded {local_path} to {s3_uri}")
    return s3_uri


def submit_batch_job(job_label, s3_input_uri, model_id=None):
    """Submit a Bedrock batch inference job."""
    model = model_id or MODEL_ID
    bedrock_client = session.client("bedrock", region_name=AWS_REGION, config=config)

    job_name = f"citator-{job_label}-{int(time.time())}"
    s3_output_uri = f"s3://{S3_BUCKET}/{S3_OUTPUT_PREFIX}/{job_label}/"

    response = bedrock_client.create_model_invocation_job(
        jobName=job_name,
        modelId=model,
        roleArn=BATCH_ROLE_ARN,
        inputDataConfig={
            "s3InputDataConfig": {
                "s3Uri": s3_input_uri,
                "s3InputFormat": "JSONL",
            }
        },
        outputDataConfig={
            "s3OutputDataConfig": {
                "s3Uri": s3_output_uri,
            }
        },
    )

    job_arn = response["jobArn"]
    logger.info(f"Submitted batch job for {job_label}: {job_arn}")
    return job_arn


def check_job_status(job_arn):
    """Check the status of a batch inference job."""
    bedrock_client = session.client("bedrock", region_name=AWS_REGION, config=config)
    response = bedrock_client.get_model_invocation_job(jobIdentifier=job_arn)
    return {
        "jobArn": response["jobArn"],
        "jobName": response.get("jobName", ""),
        "status": response["status"],
        "message": response.get("message", ""),
    }


def wait_for_jobs(job_arns, poll_interval=60):
    """Wait for all batch jobs to complete."""
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
