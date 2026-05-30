"""Run the citator two-stage batch pipeline.

Stage 1 (Haiku 4.5): extract citations + section IDs from each opinion.
Stage 2 (Kimi K2.5):  classify treatment for each (citation, section context).

Each run is keyed by a UUID `run_id` and written to:

    s3://{bucket}/Citator/runs/{run_id}/
        manifest.json
        stage1/{input.jsonl, raw/output.jsonl.out, parsed/{cluster_id}.json}
        stage2/{input.jsonl, raw/output.jsonl.out, parsed/{cluster_id}.json}

S3 is the source of truth between stages. The local `--output-dir` is used
only for scratch downloads and the final post-processed CSVs.

Usage:

    # Stage 1: extract
    python run_batch.py submit-extraction \\
        --output-dir ../experiments_X/data \\
        [--court ca1] [--metadata-file ../data/...] [--opinion-dir ../data/...] \\
        [--num-records N] [--smoke-test]
    # Prints `run_id` at exit.

    # Stage 2: classify (uses stage1 parsed output from S3)
    python run_batch.py submit-classification --run-id <run_id> [--smoke-test]

    # Collect: read stage2 parsed from S3, postprocess, write final CSVs
    python run_batch.py collect --run-id <run_id> \\
        --output-dir ../experiments_X/data \\
        [--labels-file ../data/benchmark_original/0410.csv]

    # Run the whole pipeline end-to-end (chains the three above)
    python run_batch.py run --output-dir ../experiments_X/data [other flags...]

Smoke test (after `python scripts/generate_synthetic_opinions.py`):

    python run_batch.py run --output-dir ../experiments_batch_test/data \\
        --metadata-file ../data/batch_test_citing_metadata.csv \\
        --opinion-dir ../data/batch_test_opinion_texts \\
        --smoke-test
"""

import argparse
import json
import logging
import os
import time
from collections import defaultdict

import pandas as pd

from utils.batch_utils import (
    EXTRACTION_MODEL_ID, CLASSIFICATION_MODEL_ID, SONNET_MODEL_ID,
    generate_run_id, prompt_sha,
    s3_uri, s3_stage1_input_key, s3_stage1_raw_prefix,
    s3_stage2_input_key, s3_stage2_raw_prefix,
    s3_sonnet_input_key, s3_sonnet_raw_prefix,
    s3_reeval_input_key, s3_reeval_raw_prefix, s3_reeval_batches_key,
    upload_file, upload_json, download_to_file, read_json,
    prepare_stage1_jsonl, prepare_sonnet_jsonl, prepare_reeval_jsonl,
    submit_batch_job, wait_for_jobs,
    write_manifest, read_manifest, update_manifest,
    build_classification_record,
)
from utils.instructions import (
    citator,
    haiku_extractor, kimi_classifier, reevaluator,
    haiku_extractor_short, kimi_classifier_short,
)
from utils.parse_utils import (
    download_raw_outputs,
    parse_stage1_outputs, write_stage1_parsed, read_stage1_parsed,
    parse_stage2_outputs, write_stage2_parsed, read_stage2_parsed,
    parse_sonnet_outputs, write_sonnet_parsed, read_sonnet_parsed,
    parse_reeval_outputs, write_reeval_parsed, read_reeval_parsed,
)
from utils.reevaluate import reevaluation_schema
from utils.postprocess import (
    clean_treatments, deduplicate_cited_cases, derive_severity_direction,
    filter_non_case_citations, flag_for_review, merge_to_labels,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Cluster-ID selection ──

def select_cluster_ids(metadata_file, court=None, num_records=None):
    """Read a citing-metadata CSV and return the selected cluster IDs (as strings)."""
    df = pd.read_csv(metadata_file)
    if "source" in df.columns:
        df = df[df["source"] == "sampled"]

    if court is None or court == "all":
        selected = df
    else:
        courts = [c.strip() for c in court.split(",")]
        selected = df[df["court"].isin(courts)]

    if selected.empty:
        available = sorted(df["court"].unique()) if "court" in df.columns else []
        raise ValueError(
            f"No opinions found for court='{court}' in {metadata_file}. "
            f"Available: {available}"
        )

    cluster_ids = sorted(str(c) for c in selected["cluster_id"].tolist())
    if num_records is not None:
        cluster_ids = cluster_ids[:num_records]
    return cluster_ids


# ── Stage 1: submit-extraction ──

def submit_extraction(args):
    metadata_file = args.metadata_file or os.path.join(args.input_dir, "citing_metadata.csv")
    opinion_dir = args.opinion_dir or os.path.join(args.input_dir, "opinion_texts")

    cluster_ids = select_cluster_ids(metadata_file, court=args.court, num_records=args.num_records)
    logger.info(f"Selected {len(cluster_ids)} cluster IDs from {metadata_file}")

    extraction_prompt = haiku_extractor_short if args.smoke_test else haiku_extractor

    run_id = args.run_id or generate_run_id()
    scratch_dir = os.path.join(args.output_dir, "scratch", run_id)
    os.makedirs(scratch_dir, exist_ok=True)
    local_jsonl = os.path.join(scratch_dir, "stage1_input.jsonl")

    n_records, sections_per_cluster = prepare_stage1_jsonl(
        cluster_ids, opinion_dir, extraction_prompt, local_jsonl,
        model_id=EXTRACTION_MODEL_ID,
    )
    if n_records < 100:
        logger.warning(
            f"Only {n_records} records — Bedrock batch requires >=100. The job will fail."
        )

    s3_input_key = s3_stage1_input_key(run_id)
    s3_input_uri = upload_file(local_jsonl, s3_input_key)
    s3_output_uri = s3_uri(s3_stage1_raw_prefix(run_id))

    write_manifest(run_id, {
        "cluster_ids": cluster_ids,
        "extraction_model": EXTRACTION_MODEL_ID,
        "classification_model": CLASSIFICATION_MODEL_ID,
        "extraction_prompt_sha": prompt_sha(extraction_prompt),
        "smoke_test": bool(args.smoke_test),
        "n_stage1_records": n_records,
    })

    job_name = f"citator-stage1-{run_id[:8]}-{int(time.time())}"
    job_arn = submit_batch_job(
        job_name, s3_input_uri, s3_output_uri, model_id=EXTRACTION_MODEL_ID,
    )
    update_manifest(run_id, {"stage1_job_arn": job_arn, "stage1_job_name": job_name})

    if not args.no_wait:
        logger.info(f"Waiting for Stage 1 job {job_name}...")
        results = wait_for_jobs([job_arn])
        status = results.get(job_arn, {})
        if status.get("status") != "Completed":
            logger.error(f"Stage 1 failed: {status}")
            update_manifest(run_id, {"stage1_status": status.get("status", "unknown")})
            raise SystemExit(1)
        update_manifest(run_id, {"stage1_status": "Completed"})

        # Parse + write per-cluster Stage 1 JSON to S3
        raw_dir = os.path.join(scratch_dir, "stage1_raw")
        raw_files = download_raw_outputs(s3_stage1_raw_prefix(run_id), raw_dir)
        parsed = parse_stage1_outputs(raw_files, sections_per_cluster)
        write_stage1_parsed(run_id, parsed)
        _log_status_breakdown("Stage 1", parsed)

    print(f"\nrun_id: {run_id}\n")
    return run_id


# ── Stage 2: submit-classification ──

def _group_citations(extracted_citations, sections):
    """Replay the deterministic citation grouping by section_ids tuple.

    Returns list of {group_idx, section_ids, citation_indices, section_indices}.
    Sorting by section_ids ensures the same input always produces the same
    grouping order — critical because we don't persist the grouping anywhere
    and the collect step has to replay it identically.
    """
    by_key = defaultdict(list)
    for cc in extracted_citations:
        key = tuple(sorted(cc.get("section_ids", [])))
        by_key[key].append(cc["citation_idx"])

    id_to_idx = {s["id"]: i for i, s in enumerate(sections)}
    n = len(sections)

    groups = []
    for group_idx, key in enumerate(sorted(by_key.keys())):
        # Build context window: target sections plus immediate neighbors
        context_indices = set()
        for sid in key:
            idx = id_to_idx.get(sid)
            if idx is None:
                continue
            context_indices.add(idx)
            if idx > 0:
                context_indices.add(idx - 1)
            if idx < n - 1:
                context_indices.add(idx + 1)
        groups.append({
            "group_idx": group_idx,
            "section_ids": list(key),
            "citation_indices": by_key[key],
            "section_indices": sorted(context_indices),
        })
    return groups


def _format_classification_user_text(group, sections, citations):
    """Build the user prompt for one Kimi classification record."""
    context_parts = []
    for idx in group["section_indices"]:
        s = sections[idx]
        context_parts.append(f"[Section {s['id']}]\n{s['text']}")
    context_text = "\n\n".join(context_parts)

    parts = [
        "Classify the treatment for each of the following cited cases based on "
        "the opinion excerpt above.\n"
    ]
    for position, citation_idx in enumerate(group["citation_indices"], 1):
        cc = citations[citation_idx]
        citation_str = cc.get("mainCitationString") or "(no citation string)"
        case_name = cc.get("caseName") or "(unnamed)"
        section_ids = cc.get("section_ids", [])
        parts.append(
            f"--- Cited Case {position} ---\n"
            f"Citation: {citation_str}\n"
            f"Case Name: {case_name}\n"
            f"Appears in sections: {', '.join(section_ids)}\n"
        )
    return f"<opinion_excerpt>\n{context_text}\n</opinion_excerpt>\n\n" + "\n".join(parts)


def submit_classification(args):
    manifest = read_manifest(args.run_id)
    smoke_test = bool(args.smoke_test or manifest.get("smoke_test"))
    classification_prompt = kimi_classifier_short if smoke_test else kimi_classifier

    scratch_dir = os.path.join(args.output_dir, "scratch", args.run_id)
    os.makedirs(scratch_dir, exist_ok=True)

    stage1_dir = os.path.join(scratch_dir, "stage1_parsed")
    stage1_by_cluster = read_stage1_parsed(args.run_id, stage1_dir)
    logger.info(f"Loaded Stage 1 results for {len(stage1_by_cluster)} clusters")

    local_jsonl = os.path.join(scratch_dir, "stage2_input.jsonl")
    n_records = 0
    n_clusters_submitted = 0
    n_clusters_skipped = 0

    with open(local_jsonl, "w", encoding="utf-8") as f:
        for cluster_id in sorted(stage1_by_cluster.keys()):
            stage1 = stage1_by_cluster[cluster_id]
            if stage1.get("processing_status") == "extraction_failed":
                n_clusters_skipped += 1
                logger.info(f"Skipping cluster_id={cluster_id} (extraction_failed)")
                continue
            citations = stage1.get("extracted_citations", [])
            sections = stage1.get("sections", [])
            if not citations:
                n_clusters_skipped += 1
                continue

            groups = _group_citations(citations, sections)
            if not groups:
                n_clusters_skipped += 1
                continue

            for group in groups:
                user_text = _format_classification_user_text(group, sections, citations)
                record_id = f"{cluster_id}__g{group['group_idx']}"
                record = build_classification_record(
                    record_id, user_text, classification_prompt,
                    model_id=CLASSIFICATION_MODEL_ID,
                )
                f.write(json.dumps(record) + "\n")
                n_records += 1
            n_clusters_submitted += 1

    logger.info(
        f"Wrote {n_records} classification records for {n_clusters_submitted} clusters "
        f"(skipped {n_clusters_skipped})"
    )
    if n_records < 100:
        logger.warning(
            f"Only {n_records} records — Bedrock batch requires >=100. The job will fail."
        )

    s3_input_key = s3_stage2_input_key(args.run_id)
    s3_input_uri = upload_file(local_jsonl, s3_input_key)
    s3_output_uri = s3_uri(s3_stage2_raw_prefix(args.run_id))

    update_manifest(args.run_id, {
        "classification_prompt_sha": prompt_sha(classification_prompt),
        "n_stage2_records": n_records,
        "n_clusters_classified": n_clusters_submitted,
        "n_clusters_skipped_stage2": n_clusters_skipped,
    })

    job_name = f"citator-stage2-{args.run_id[:8]}-{int(time.time())}"
    job_arn = submit_batch_job(
        job_name, s3_input_uri, s3_output_uri, model_id=CLASSIFICATION_MODEL_ID,
    )
    update_manifest(args.run_id, {"stage2_job_arn": job_arn, "stage2_job_name": job_name})

    if not args.no_wait:
        logger.info(f"Waiting for Stage 2 job {job_name}...")
        results = wait_for_jobs([job_arn])
        status = results.get(job_arn, {})
        if status.get("status") != "Completed":
            logger.error(f"Stage 2 failed: {status}")
            update_manifest(args.run_id, {"stage2_status": status.get("status", "unknown")})
            raise SystemExit(1)
        update_manifest(args.run_id, {"stage2_status": "Completed"})

        # Parse Stage 2 raw output, replay groups to map back to citation_idx
        raw_dir = os.path.join(scratch_dir, "stage2_raw")
        raw_files = download_raw_outputs(s3_stage2_raw_prefix(args.run_id), raw_dir)
        groups_by_cluster = {
            cid: _group_citations(s.get("extracted_citations", []), s.get("sections", []))
            for cid, s in stage1_by_cluster.items()
        }
        parsed = parse_stage2_outputs(raw_files, groups_by_cluster, stage1_by_cluster)
        write_stage2_parsed(args.run_id, parsed)
        _log_status_breakdown("Stage 2", parsed)


# ── Collect: postprocess + merge to labels ──

def collect(args):
    scratch_dir = os.path.join(args.output_dir, "scratch", args.run_id)
    stage2_dir = os.path.join(scratch_dir, "stage2_parsed")
    parsed = read_stage2_parsed(args.run_id, stage2_dir)
    logger.info(f"Loaded Stage 2 results for {len(parsed)} clusters")

    # Flatten to a per-citation DataFrame
    rows = []
    for cluster_id, data in parsed.items():
        cluster_status = data.get("processing_status", "ok")
        results = data.get("results", [])
        if not results and cluster_status != "ok":
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": "",
                "caseName": "",
                "actingCase": "",
                "caseHistory": "",
                "treatment": "",
                "opinionType": "",
                "quote": "",
                "rationale": "",
                "section_ids": "[]",
                "processing_status": cluster_status,
            })
            continue
        for r in results:
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": r.get("mainCitationString") or "",
                "caseName": r.get("caseName") or "",
                "actingCase": r.get("actingCase", ""),
                "caseHistory": r.get("caseHistory", ""),
                "treatment": r.get("treatment", ""),
                "opinionType": r.get("opinionType", ""),
                "quote": r.get("quote") or "",
                "rationale": r.get("rationale", ""),
                "section_ids": json.dumps(r.get("section_ids", [])),
                "processing_status": r.get("processing_status", cluster_status),
            })

    parsed_df = pd.DataFrame(rows)
    results_dir = os.path.join(args.output_dir, "output")
    os.makedirs(results_dir, exist_ok=True)
    parsed_df.to_csv(os.path.join(results_dir, "parsed_results.csv"), index=False)
    logger.info(f"Wrote parsed_results.csv ({len(parsed_df)} rows)")

    # Postprocess: filter non-case → clean → dedup → derive severity/direction
    cleaned_df = filter_non_case_citations(parsed_df)
    cleaned_df = clean_treatments(cleaned_df)
    cleaned_df = deduplicate_cited_cases(cleaned_df)
    final_df = derive_severity_direction(cleaned_df)
    final_df.to_csv(os.path.join(results_dir, "final_results.csv"), index=False)
    logger.info(f"Wrote final_results.csv ({len(final_df)} rows)")

    if args.labels_file and os.path.exists(args.labels_file):
        labels_df = pd.read_csv(args.labels_file)
        labels_df = labels_df[
            (labels_df.get("final_treatment", "") != "PENDING")
            & (labels_df.get("final_treatment", "") != "REMOVE")
            & (labels_df.get("final_treatment", "") != "MANUAL")
        ] if "final_treatment" in labels_df.columns else labels_df
        eval_dir = os.path.join(args.output_dir, "eval")
        merge_to_labels(final_df, labels_df, eval_dir)
        logger.info(f"Wrote eval merge to {eval_dir}")
    elif args.labels_file:
        logger.warning(f"Labels file not found: {args.labels_file}")


# ── Run wrapper ──

def run_all(args):
    run_id = submit_extraction(args)
    args.run_id = run_id
    submit_classification(args)
    collect(args)


# ── Single-stage Sonnet ──

def submit_sonnet(args):
    """Submit a single-stage Sonnet (citator prompt) Bedrock batch job."""
    metadata_file = args.metadata_file or os.path.join(args.input_dir, "citing_metadata.csv")
    opinion_dir = args.opinion_dir or os.path.join(args.input_dir, "opinion_texts")

    cluster_ids = select_cluster_ids(metadata_file, court=args.court, num_records=args.num_records)
    logger.info(f"Selected {len(cluster_ids)} cluster IDs from {metadata_file}")

    system_prompt = citator

    run_id = args.run_id or generate_run_id()
    scratch_dir = os.path.join(args.output_dir, "scratch", run_id)
    os.makedirs(scratch_dir, exist_ok=True)
    local_jsonl = os.path.join(scratch_dir, "sonnet_input.jsonl")

    n_records, pages_per_cluster = prepare_sonnet_jsonl(
        cluster_ids, opinion_dir, system_prompt, local_jsonl,
        model_id=SONNET_MODEL_ID,
    )
    if n_records < 100:
        logger.warning(
            f"Only {n_records} records — Bedrock batch requires >=100. The job will fail."
        )

    s3_input_key = s3_sonnet_input_key(run_id)
    s3_input_uri = upload_file(local_jsonl, s3_input_key)
    s3_output_uri = s3_uri(s3_sonnet_raw_prefix(run_id))

    write_manifest(run_id, {
        "cluster_ids": cluster_ids,
        "pipeline": "sonnet-single-stage",
        "sonnet_model": SONNET_MODEL_ID,
        "citator_prompt_sha": prompt_sha(system_prompt),
        "n_sonnet_records": n_records,
        "pages_per_cluster": pages_per_cluster,
    })

    job_name = f"citator-sonnet-{run_id[:8]}-{int(time.time())}"
    job_arn = submit_batch_job(
        job_name, s3_input_uri, s3_output_uri, model_id=SONNET_MODEL_ID,
    )
    update_manifest(run_id, {"sonnet_job_arn": job_arn, "sonnet_job_name": job_name})

    if not args.no_wait:
        logger.info(f"Waiting for Sonnet job {job_name}...")
        results = wait_for_jobs([job_arn])
        status = results.get(job_arn, {})
        if status.get("status") != "Completed":
            logger.error(f"Sonnet job failed: {status}")
            update_manifest(run_id, {"sonnet_status": status.get("status", "unknown")})
            raise SystemExit(1)
        update_manifest(run_id, {"sonnet_status": "Completed"})

        raw_dir = os.path.join(scratch_dir, "sonnet_raw")
        raw_files = download_raw_outputs(s3_sonnet_raw_prefix(run_id), raw_dir)
        parsed = parse_sonnet_outputs(raw_files, pages_per_cluster)
        write_sonnet_parsed(run_id, parsed)
        _log_status_breakdown("Sonnet", parsed)

    print(f"\nrun_id: {run_id}\n")
    return run_id


def collect_sonnet(args):
    """Postprocess single-stage Sonnet results into final CSVs + optional eval merge."""
    scratch_dir = os.path.join(args.output_dir, "scratch", args.run_id)
    sonnet_dir = os.path.join(scratch_dir, "sonnet_parsed")
    parsed = read_sonnet_parsed(args.run_id, sonnet_dir)
    logger.info(f"Loaded Sonnet results for {len(parsed)} clusters")

    rows = []
    for cluster_id, data in parsed.items():
        cluster_status = data.get("processing_status", "ok")
        results = data.get("results", [])
        if not results and cluster_status != "ok":
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": "",
                "caseName": "",
                "actingCase": "",
                "caseHistory": "",
                "treatment": "",
                "opinionType": "",
                "quote": "",
                "rationale": "",
                "section_ids": "[]",
                "processing_status": cluster_status,
            })
            continue
        for r in results:
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": r.get("mainCitationString") or "",
                "caseName": r.get("caseName") or "",
                "actingCase": r.get("actingCase", ""),
                "caseHistory": r.get("caseHistory", ""),
                "treatment": r.get("treatment", ""),
                "opinionType": r.get("opinionType", ""),
                "quote": r.get("quote") or "",
                "rationale": r.get("rationale", ""),
                "section_ids": json.dumps(r.get("section_ids", [])),
                "processing_status": r.get("processing_status", cluster_status),
            })

    parsed_df = pd.DataFrame(rows)
    results_dir = os.path.join(args.output_dir, "output")
    os.makedirs(results_dir, exist_ok=True)
    parsed_df.to_csv(os.path.join(results_dir, "parsed_results.csv"), index=False)
    logger.info(f"Wrote parsed_results.csv ({len(parsed_df)} rows)")

    cleaned_df = filter_non_case_citations(parsed_df)
    cleaned_df = clean_treatments(cleaned_df)
    cleaned_df = deduplicate_cited_cases(cleaned_df)
    final_df = derive_severity_direction(cleaned_df)
    final_df.to_csv(os.path.join(results_dir, "final_results.csv"), index=False)
    logger.info(f"Wrote final_results.csv ({len(final_df)} rows)")

    if args.labels_file and os.path.exists(args.labels_file):
        labels_df = pd.read_csv(args.labels_file)
        labels_df = labels_df[
            (labels_df.get("final_treatment", "") != "PENDING")
            & (labels_df.get("final_treatment", "") != "REMOVE")
            & (labels_df.get("final_treatment", "") != "MANUAL")
        ] if "final_treatment" in labels_df.columns else labels_df
        eval_dir = os.path.join(args.output_dir, "eval")
        merge_to_labels(final_df, labels_df, eval_dir)
        logger.info(f"Wrote eval merge to {eval_dir}")
    elif args.labels_file:
        logger.warning(f"Labels file not found: {args.labels_file}")


def run_sonnet_all(args):
    run_id = submit_sonnet(args)
    args.run_id = run_id
    collect_sonnet(args)


# ── Kimi re-evaluation (chained onto a Sonnet run) ──

def _build_final_from_sonnet_parsed(parsed):
    """Rebuild final_df from per-cluster Sonnet parsed output (same shape that
    collect_sonnet.parsed_df → final_df produces in memory, minus the to_csv
    roundtrip). Returns final_df ready for flag_for_review + merge_to_labels.
    """
    rows = []
    for cluster_id, data in parsed.items():
        results = data.get("results", [])
        if not results:
            continue
        for r in results:
            rows.append({
                "citing_cluster_id": cluster_id,
                "mainCitationString": r.get("mainCitationString") or "",
                "caseName": r.get("caseName") or "",
                "actingCase": r.get("actingCase", ""),
                "caseHistory": r.get("caseHistory", ""),
                "treatment": r.get("treatment", ""),
                "opinionType": r.get("opinionType", ""),
                "quote": r.get("quote") or "",
                "rationale": r.get("rationale", ""),
                "section_ids": json.dumps(r.get("section_ids", [])),
                "processing_status": r.get("processing_status", "ok"),
            })
    parsed_df = pd.DataFrame(rows)
    cleaned = filter_non_case_citations(parsed_df)
    cleaned = clean_treatments(cleaned)
    cleaned = deduplicate_cited_cases(cleaned)
    return derive_severity_direction(cleaned)


def submit_reeval(args):
    """Flag Sonnet predictions and submit a Kimi K2.5 batch re-evaluation.

    Expects --run-id of a completed Sonnet run; reads Sonnet parsed from S3,
    runs flag_for_review, packs flagged rows into REEVAL_BATCH_SIZE batches,
    and submits a Bedrock batch job. Persists `batches.csv` to S3 so the
    collect step can map reassessments back to original rows without state.
    """
    scratch_dir = os.path.join(args.output_dir, "scratch", args.run_id)
    os.makedirs(scratch_dir, exist_ok=True)
    sonnet_dir = os.path.join(scratch_dir, "sonnet_parsed")
    parsed = read_sonnet_parsed(args.run_id, sonnet_dir)
    logger.info(f"Loaded Sonnet results for {len(parsed)} clusters")

    final = _build_final_from_sonnet_parsed(parsed)
    flagged, _ = flag_for_review(final)
    logger.info(f"flagged={len(flagged)} of {len(final)} ({100*len(flagged)/max(len(final),1):.1f}%)")

    if len(flagged) == 0:
        logger.warning("No flagged rows — nothing to re-evaluate.")
        return

    local_jsonl = os.path.join(scratch_dir, "reeval_input.jsonl")
    n_records, batches_df = prepare_reeval_jsonl(
        flagged, reevaluator, reevaluation_schema, local_jsonl,
    )
    if n_records < 100:
        logger.warning(
            f"Only {n_records} reeval records — Bedrock batch requires >=100. "
            f"The job will fail."
        )

    # Persist batches.csv locally + to S3 so collect can run without state.
    local_batches = os.path.join(scratch_dir, "reeval_batches.csv")
    batches_df.to_csv(local_batches, index=False)
    upload_file(local_batches, s3_reeval_batches_key(args.run_id))

    s3_input_uri = upload_file(local_jsonl, s3_reeval_input_key(args.run_id))
    s3_output_uri = s3_uri(s3_reeval_raw_prefix(args.run_id))

    update_manifest(args.run_id, {
        "reevaluator_prompt_sha": prompt_sha(reevaluator),
        "n_reeval_records": n_records,
        "n_flagged": int(len(flagged)),
        "reeval_model": CLASSIFICATION_MODEL_ID,
    })

    job_name = f"citator-reeval-{args.run_id[:8]}-{int(time.time())}"
    job_arn = submit_batch_job(
        job_name, s3_input_uri, s3_output_uri, model_id=CLASSIFICATION_MODEL_ID,
    )
    update_manifest(args.run_id, {"reeval_job_arn": job_arn, "reeval_job_name": job_name})

    if not args.no_wait:
        logger.info(f"Waiting for re-eval job {job_name}...")
        results = wait_for_jobs([job_arn])
        status = results.get(job_arn, {})
        if status.get("status") != "Completed":
            logger.error(f"Re-eval job failed: {status}")
            update_manifest(args.run_id, {"reeval_status": status.get("status", "unknown")})
            raise SystemExit(1)
        update_manifest(args.run_id, {"reeval_status": "Completed"})

        raw_dir = os.path.join(scratch_dir, "reeval_raw")
        raw_files = download_raw_outputs(s3_reeval_raw_prefix(args.run_id), raw_dir)
        reassessments_by_batch = parse_reeval_outputs(raw_files)
        write_reeval_parsed(args.run_id, reassessments_by_batch)
        logger.info(f"Re-eval parsed: {len(reassessments_by_batch)} batches")


def collect_reeval(args):
    """Merge Kimi reassessments back into the Sonnet predictions and re-eval.

    Loads:
      1. Sonnet parsed (from S3) → builds final_df via the same in-memory path
         as collect_sonnet.
      2. batches.csv (from S3) → maps (batch_idx, within_batch) to original
         (citing_cluster_id, mainCitationString).
      3. Reeval parsed.json (from S3) → reassessment per (batch_idx, citedCaseId).

    Replaces flagged rows' treatments with Kimi's reassessments where present,
    re-runs derive_severity_direction (so caseHistory + severity stay consistent
    with the canonical mapping), writes final_results_reeval.csv, and merges
    against labels if provided.
    """
    scratch_dir = os.path.join(args.output_dir, "scratch", args.run_id)
    sonnet_dir = os.path.join(scratch_dir, "sonnet_parsed")
    parsed = read_sonnet_parsed(args.run_id, sonnet_dir)
    final = _build_final_from_sonnet_parsed(parsed)
    logger.info(f"Sonnet final_df: {len(final)} rows")

    # Pull batches.csv (the prediction→batch mapping) from S3.
    local_batches = os.path.join(scratch_dir, "reeval_batches.csv")
    if not os.path.exists(local_batches):
        download_to_file(s3_reeval_batches_key(args.run_id), local_batches)
    batches_df = pd.read_csv(local_batches)
    # citing_cluster_id must be a plain Python type for equality; allow
    # mainCitationString to be NaN (parsed CSV roundtrip).
    batches_df["citing_cluster_id"] = batches_df["citing_cluster_id"].astype(str)

    reassessments_by_batch = read_reeval_parsed(args.run_id)
    logger.info(f"Reeval reassessments: {sum(len(v) for v in reassessments_by_batch.values())} "
                f"across {len(reassessments_by_batch)} batches")

    # Build (citing_cluster_id, mainCitationString) → new treatment + rationale.
    reeval_updates = {}
    for _, batch_row in batches_df.iterrows():
        batch_idx = int(batch_row["batch_idx"])
        within = int(batch_row["within_batch"])
        cid = str(batch_row["citing_cluster_id"])
        main_cit = batch_row["mainCitationString"]
        # Pandas reads empty cells as NaN; normalize so the key matches final_df rows.
        if pd.isna(main_cit):
            main_cit = ""
        reassessments = reassessments_by_batch.get(batch_idx, [])
        match = next(
            (r for r in reassessments
             if isinstance(r, dict) and int(r.get("citedCaseId") or -1) == within),
            None,
        )
        if match is None:
            continue
        reeval_updates[(cid, str(main_cit))] = {
            "treatment": match.get("treatment") or "",
            "rationale": (match.get("rationale") or "") + " [reeval]",
        }

    logger.info(f"Will replace treatments on {len(reeval_updates)} rows")

    # Apply updates in place
    final = final.copy()
    final["citing_cluster_id_str"] = final["citing_cluster_id"].astype(str)
    final["mainCitationString_str"] = final["mainCitationString"].fillna("").astype(str)
    n_changed = 0
    n_unchanged = 0
    for idx, row in final.iterrows():
        key = (row["citing_cluster_id_str"], row["mainCitationString_str"])
        upd = reeval_updates.get(key)
        if upd is None:
            continue
        new_treat = upd["treatment"]
        if new_treat and new_treat != row["treatment"]:
            final.at[idx, "treatment"] = new_treat
            final.at[idx, "rationale"] = upd["rationale"]
            n_changed += 1
        else:
            n_unchanged += 1
    final = final.drop(columns=["citing_cluster_id_str", "mainCitationString_str"])
    logger.info(f"Reeval applied: {n_changed} treatments changed, {n_unchanged} kept same")

    # Re-derive severity + direction since treatment can have shifted tiers.
    final = clean_treatments(final)
    final = deduplicate_cited_cases(final)
    final = derive_severity_direction(final)

    results_dir = os.path.join(args.output_dir, "output")
    os.makedirs(results_dir, exist_ok=True)
    final.to_csv(os.path.join(results_dir, "final_results_reeval.csv"), index=False)
    logger.info(f"Wrote final_results_reeval.csv ({len(final)} rows)")

    if args.labels_file and os.path.exists(args.labels_file):
        labels_df = pd.read_csv(args.labels_file)
        labels_df = labels_df[
            (labels_df.get("final_treatment", "") != "PENDING")
            & (labels_df.get("final_treatment", "") != "REMOVE")
            & (labels_df.get("final_treatment", "") != "MANUAL")
        ] if "final_treatment" in labels_df.columns else labels_df
        eval_dir = os.path.join(args.output_dir, "eval_reeval")
        merge_to_labels(final, labels_df, eval_dir)
        logger.info(f"Wrote eval merge to {eval_dir}")
    elif args.labels_file:
        logger.warning(f"Labels file not found: {args.labels_file}")


def run_reeval_all(args):
    submit_reeval(args)
    collect_reeval(args)


# ── Helpers ──

def _log_status_breakdown(stage, parsed_by_cluster):
    counts = defaultdict(int)
    for data in parsed_by_cluster.values():
        counts[data.get("processing_status", "ok")] += 1
    logger.info(f"{stage} status breakdown: {dict(counts)}")


# ── CLI ──

def _add_input_flags(p):
    p.add_argument("--input-dir", default="../data",
                   help="Path to shared input data directory")
    p.add_argument("--metadata-file", default=None,
                   help="Path to citing-metadata CSV (defaults to {input_dir}/citing_metadata.csv)")
    p.add_argument("--opinion-dir", default=None,
                   help="Path to opinion texts directory (defaults to {input_dir}/opinion_texts)")
    p.add_argument("--court", default=None,
                   help="Comma-separated court IDs (e.g., ca1, 'ca1,ca2'). Omit or 'all' for every row.")
    p.add_argument("--num-records", type=int, default=None,
                   help="Limit number of records (for testing).")


def main():
    parser = argparse.ArgumentParser(description="Citator two-stage batch pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # submit-extraction
    p1 = sub.add_parser("submit-extraction", help="Stage 1: extract citations")
    p1.add_argument("--output-dir", required=True, help="Local scratch + final results dir")
    p1.add_argument("--run-id", default=None, help="Reuse an existing run_id (default: generate)")
    p1.add_argument("--smoke-test", action="store_true",
                    help="Use the short test prompts instead of production prompts")
    p1.add_argument("--no-wait", action="store_true",
                    help="Submit and exit without waiting for the batch job")
    _add_input_flags(p1)
    p1.set_defaults(func=submit_extraction)

    # submit-classification
    p2 = sub.add_parser("submit-classification", help="Stage 2: classify treatments")
    p2.add_argument("--run-id", required=True)
    p2.add_argument("--output-dir", required=True)
    p2.add_argument("--smoke-test", action="store_true",
                    help="Override manifest's smoke_test flag")
    p2.add_argument("--no-wait", action="store_true")
    p2.set_defaults(func=submit_classification)

    # collect
    p3 = sub.add_parser("collect", help="Postprocess Stage 2 results into final CSVs")
    p3.add_argument("--run-id", required=True)
    p3.add_argument("--output-dir", required=True)
    p3.add_argument("--labels-file", default=None,
                    help="Optional path to a benchmark labels CSV (e.g., 0410.csv)")
    p3.set_defaults(func=collect)

    # run (wrapper)
    p4 = sub.add_parser("run", help="Submit Stage 1 + Stage 2 + collect, end-to-end")
    p4.add_argument("--output-dir", required=True)
    p4.add_argument("--run-id", default=None)
    p4.add_argument("--smoke-test", action="store_true")
    p4.add_argument("--labels-file", default=None)
    _add_input_flags(p4)
    p4.set_defaults(func=run_all, no_wait=False)

    # submit-sonnet
    p5 = sub.add_parser("submit-sonnet",
                        help="Single-stage Sonnet (citator prompt) batch submit")
    p5.add_argument("--output-dir", required=True)
    p5.add_argument("--run-id", default=None)
    p5.add_argument("--no-wait", action="store_true")
    _add_input_flags(p5)
    p5.set_defaults(func=submit_sonnet)

    # collect-sonnet
    p6 = sub.add_parser("collect-sonnet",
                        help="Postprocess single-stage Sonnet results into final CSVs")
    p6.add_argument("--run-id", required=True)
    p6.add_argument("--output-dir", required=True)
    p6.add_argument("--labels-file", default=None,
                    help="Optional path to a benchmark labels CSV (e.g., 0410.csv)")
    p6.set_defaults(func=collect_sonnet)

    # run-sonnet (wrapper)
    p7 = sub.add_parser("run-sonnet",
                        help="Submit single-stage Sonnet + collect, end-to-end")
    p7.add_argument("--output-dir", required=True)
    p7.add_argument("--run-id", default=None)
    p7.add_argument("--labels-file", default=None)
    _add_input_flags(p7)
    p7.set_defaults(func=run_sonnet_all, no_wait=False)

    # submit-reeval
    p8 = sub.add_parser("submit-reeval",
                        help="Submit Kimi re-eval of flagged Sonnet predictions for a run_id")
    p8.add_argument("--run-id", required=True,
                    help="Sonnet run_id whose flagged predictions to re-evaluate")
    p8.add_argument("--output-dir", required=True)
    p8.add_argument("--no-wait", action="store_true")
    p8.set_defaults(func=submit_reeval)

    # collect-reeval
    p9 = sub.add_parser("collect-reeval",
                        help="Merge Kimi reassessments + write final_results_reeval.csv")
    p9.add_argument("--run-id", required=True)
    p9.add_argument("--output-dir", required=True)
    p9.add_argument("--labels-file", default=None)
    p9.set_defaults(func=collect_reeval)

    # run-reeval (wrapper)
    p10 = sub.add_parser("run-reeval",
                         help="Submit re-eval + wait + collect")
    p10.add_argument("--run-id", required=True)
    p10.add_argument("--output-dir", required=True)
    p10.add_argument("--labels-file", default=None)
    p10.set_defaults(func=run_reeval_all, no_wait=False)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
