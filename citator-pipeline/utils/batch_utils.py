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

S3_BUCKET = os.environ.get("CITATOR_S3_BUCKET", "your-batch-inference-bucket")
S3_INPUT_PREFIX = "citator/v404/input"
S3_OUTPUT_PREFIX = "citator/v404/output"


def _validate_s3_bucket():
    if S3_BUCKET == "your-batch-inference-bucket":
        raise ValueError(
            "S3_BUCKET is not configured. Set the CITATOR_S3_BUCKET environment variable "
            "or update S3_BUCKET in utils/batch_utils.py."
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


def build_jsonl_record(record_id, opinion_text):
    """Build a single JSONL record for Bedrock batch inference using Converse API format.

    Args:
        record_id: Unique record identifier. For single-page opinions this is the
            cluster_id. For paginated opinions this is '{cluster_id}_page_{n}'.
        opinion_text: The opinion text (or page of opinion text) to process.
    """
    return {
        "recordId": str(record_id),
        "modelInput": {
            "modelId": MODEL_ID,
            "system": [{"text": SYSTEM_PROMPT}],
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": f"<opinion>\n{opinion_text}\n</opinion>"}],
                }
            ],
            "toolConfig": {
                "tools": [tool_spec],
                "toolChoice": {"tool": {"name": "analyze_cited_case_treatment"}},
            },
            "inferenceConfig": {"temperature": TEMPERATURE},
        },
    }


def prepare_jsonl(circuit_name, cluster_ids, citing_dir, output_dir=OUTPUT_DIR):
    """Prepare a JSONL file for a circuit court's batch job.

    Long opinions are split into overlapping pages using the same pagination
    logic as the real-time pipeline (experiments_04032026). Each page becomes
    a separate JSONL record with recordId '{cluster_id}_page_{n}'.
    """
    jsonl_path = os.path.join(output_dir, f"{circuit_name}.jsonl")
    os.makedirs(output_dir, exist_ok=True)

    records = []
    total_pages = 0
    paginated_opinions = 0

    for cluster_id in cluster_ids:
        try:
            opinion_text = load_opinion_text(citing_dir, cluster_id)
            pages = split_opinion_to_pages(opinion_text)

            if len(pages) == 1:
                record = build_jsonl_record(cluster_id, opinion_text)
                records.append(record)
                total_pages += 1
            else:
                paginated_opinions += 1
                for i, page in enumerate(pages, 1):
                    record_id = f"{cluster_id}_page_{i}"
                    record = build_jsonl_record(record_id, page)
                    records.append(record)
                    total_pages += 1
                logger.info(f"cluster_id={cluster_id}: split into {len(pages)} pages")

        except Exception as e:
            logger.error(f"Failed to load opinion for {cluster_id}: {e}")

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    logger.info(
        f"Prepared {len(records)} records for {circuit_name} at {jsonl_path} "
        f"({len(cluster_ids)} opinions, {paginated_opinions} paginated, {total_pages} total pages)"
    )
    return jsonl_path


def upload_to_s3(local_path, s3_key):
    """Upload a local file to S3."""
    _validate_s3_bucket()
    s3_client = session.client("s3", region_name=AWS_REGION)
    s3_client.upload_file(local_path, S3_BUCKET, s3_key)
    s3_uri = f"s3://{S3_BUCKET}/{s3_key}"
    logger.info(f"Uploaded {local_path} to {s3_uri}")
    return s3_uri


def submit_batch_job(circuit_name, s3_input_uri):
    """Submit a Bedrock batch inference job."""
    bedrock_client = session.client("bedrock", region_name=AWS_REGION, config=config)

    job_name = f"citator-v404-{circuit_name}-{int(time.time())}"
    s3_output_uri = f"s3://{S3_BUCKET}/{S3_OUTPUT_PREFIX}/{circuit_name}/"

    response = bedrock_client.create_model_invocation_job(
        jobName=job_name,
        modelId=MODEL_ID,
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
    logger.info(f"Submitted batch job for {circuit_name}: {job_arn}")
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
