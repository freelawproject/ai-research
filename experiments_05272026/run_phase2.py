"""Phase 2 driver: paginate → invoke Haiku → postprocess.

Two modes, batch is the default:

- `--mode batch` (default): paginate all clusters, build one JSONL, upload to
  S3, submit a Bedrock batch job, wait for completion, download outputs, then
  postprocess per cluster. Requires `CITATOR_S3_BUCKET` and
  `CITATOR_BATCH_ROLE_ARN` env vars and at least 100 LLM calls in the run
  (Bedrock batch minimum). For partial flows, `--run-id` skips submit and
  collects an already-finished job; `--submit-only` submits without waiting.
- `--mode realtime`: synchronous Converse-API calls per cluster, postprocess
  inline. Use this for small smoke runs that don't meet the 100-record batch
  minimum.

Outputs (both modes):
- `data/grouped/{cluster_id}.json` per cluster (the merged + resolved output).
- `data/grouped_untagged_log.jsonl` — every untagged occurrence, accepted+dropped.
- `data/grouped_unrouted_ids.jsonl` — Phase 1 ids the model didn't route.
- `data/grouped_invalid_ids.jsonl` — fabricated ids not in id_to_metadata_map.
- `data/phase2_report.json` — per-cluster tallies.

Usage:
    python run_phase2.py --prep-only                       # local-only dry run: prep + summary
    python run_phase2.py                                   # batch mode, all clusters
    python run_phase2.py --mode realtime --limit 5         # 5-cluster realtime smoke
    python run_phase2.py --clusters 100887 1722 112786     # specific clusters (batch)
    python run_phase2.py --submit-only                     # batch submit; collect later
    python run_phase2.py --run-id <uuid>                   # collect a finished run
    python run_phase2.py --postprocess-only --clusters 117935   # re-run postprocess on cached emissions
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "citator-pipeline")
)

from utils.batch_utils import (  # noqa: E402
    EXTRACTION_MODEL_ID,
    build_citation_grouping_record,
    download_to_file,
    extract_citation_grouping_emission,
    generate_run_id,
    list_keys,
    read_manifest,
    s3_phase2_calls_key,
    s3_phase2_input_key,
    s3_phase2_raw_prefix,
    s3_uri,
    submit_batch_job,
    upload_file,
    upload_json,
    wait_for_jobs,
    write_manifest,
)
from utils.bedrock_converse_utils import (  # noqa: E402
    citation_grouping_tool_spec,
    converse_completion,
)
from utils.instructions import citation_grouping  # noqa: E402
from utils.paginate_and_call import (  # noqa: E402
    CallInput,
    deserialize_calls,
    make_record_id,
    parse_record_id,
    prepare_calls,
    serialize_calls,
)
from utils.postprocess_offsets import postprocess  # noqa: E402

HAIKU_MODEL_ID = EXTRACTION_MODEL_ID  # same id; alias for clarity.

DATA_DIR = Path(__file__).parent / "data"
GROUPED_DIR = DATA_DIR / "grouped"
CALLS_DIR = DATA_DIR / "phase2_calls"
RAW_REALTIME_DIR = DATA_DIR / "phase2_raw_realtime"
UNTAGGED_LOG = DATA_DIR / "grouped_untagged_log.jsonl"
UNROUTED_LOG = DATA_DIR / "grouped_unrouted_ids.jsonl"
INVALID_LOG = DATA_DIR / "grouped_invalid_ids.jsonl"
CASENAME_WARNINGS_LOG = DATA_DIR / "grouped_casename_warnings.jsonl"
ID_CONFLICTS_LOG = DATA_DIR / "grouped_id_conflicts.jsonl"
REPORT_PATH = DATA_DIR / "phase2_report.json"
LAST_RUN_ID_PATH = DATA_DIR / "phase2_last_run_id.txt"

DEFAULT_TAGGED_DIR = Path(__file__).parent.parent / "experiments_05262026" / "data" / "tagged"

# Bedrock batch minimum records per input file.
BATCH_MIN_RECORDS = 100


# ── Shared helpers ─────────────────────────────────────────────────

def resolve_cluster_ids(args, tagged_dir: Path) -> list[int]:
    if args.clusters:
        ids = list(args.clusters)
    else:
        ids = sorted(int(p.stem) for p in tagged_dir.glob("*.json"))
    if args.limit:
        ids = ids[: args.limit]
    return ids


def load_artifact(cluster_id: int, tagged_dir: Path) -> dict:
    return json.loads((tagged_dir / f"{cluster_id}.json").read_text())


def init_dirs() -> None:
    GROUPED_DIR.mkdir(parents=True, exist_ok=True)
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_REALTIME_DIR.mkdir(parents=True, exist_ok=True)


def write_report(report: dict, totals: dict, elapsed: float, n_clusters: int) -> None:
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print()
    print("=== Phase 2 summary ===")
    print(f"Clusters processed:    {n_clusters:>8,}")
    print(f"Wall time:             {elapsed:>8.1f}s")
    for k, v in totals.items():
        print(f"{k:>22s}: {v:>10,}")
    print(f"Report:           {REPORT_PATH}")
    print(f"Grouped:          {GROUPED_DIR}")
    print(f"Raw realtime I/O: {RAW_REALTIME_DIR}/  (one .json per call: input + emission + metrics)")
    print(f"Untagged log:     {UNTAGGED_LOG}")
    print(f"Unrouted log:     {UNROUTED_LOG}")
    print(f"Invalid log:      {INVALID_LOG}")
    print(f"caseName warn:    {CASENAME_WARNINGS_LOG}")
    print(f"id conflicts:     {ID_CONFLICTS_LOG}")


def emit_per_cluster_outputs(
    cluster_ids: list[int],
    tagged_dir: Path,
    emissions_by_cluster: dict[int, list[dict]],
    calls_by_cluster: dict[int, list[CallInput]],
    *,
    call_metrics_by_cluster: dict[int, list[dict]] | None = None,
) -> tuple[dict, dict]:
    """Run postprocess for every cluster; write grouped JSON + tallies.

    Returns (per-cluster report dict, totals dict).
    """
    report: dict[str, dict] = {}
    totals = {
        "n_calls": 0, "n_cases": 0, "n_occurrences": 0,
        "n_untagged_accepted": 0, "n_untagged_dropped": 0,
        "n_invalid_ids": 0, "n_casename_warnings": 0,
        "n_id_conflicts": 0,
        "n_unrouted_ids": 0,
        "input_tokens": 0, "output_tokens": 0,
    }

    with open(UNTAGGED_LOG, "w") as ulog, \
         open(UNROUTED_LOG, "w") as rlog, \
         open(INVALID_LOG, "w") as ilog, \
         open(CASENAME_WARNINGS_LOG, "w") as cnlog, \
         open(ID_CONFLICTS_LOG, "w") as iclog:
        for cid in cluster_ids:
            calls = calls_by_cluster.get(cid)
            emissions = emissions_by_cluster.get(cid)
            if calls is None or emissions is None:
                print(f"  {cid}: SKIP — no calls or emissions")
                report[str(cid)] = {"error": "no_emissions"}
                continue
            try:
                artifact = load_artifact(cid, tagged_dir)
                result = postprocess(
                    artifact, calls, emissions,
                    untagged_log=ulog, unrouted_log=rlog, invalid_ids_log=ilog,
                    casename_warnings_log=cnlog,
                    id_conflicts_log=iclog,
                )
            except Exception as e:
                print(f"  {cid}: ERROR — {type(e).__name__}: {e}")
                report[str(cid)] = {"error": f"{type(e).__name__}: {e}"}
                continue

            out_path = GROUPED_DIR / f"{cid}.json"
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

            metrics = call_metrics_by_cluster.get(cid, []) if call_metrics_by_cluster else []
            in_tok = sum(m.get("input_tokens", 0) for m in metrics)
            out_tok = sum(m.get("output_tokens", 0) for m in metrics)

            cluster_report = {
                "n_calls": len(calls),
                "n_cases": result["n_cases"],
                "n_occurrences": result["n_occurrences"],
                "n_untagged_accepted": result["n_untagged_accepted"],
                "n_untagged_dropped": result["n_untagged_dropped"],
                "n_invalid_ids": result["n_invalid_ids"],
                "n_casename_warnings": result["n_casename_warnings"],
                "n_id_conflicts": result["n_id_conflicts"],
                "n_unrouted_ids": result["n_unrouted_ids"],
                "input_tokens": in_tok,
                "output_tokens": out_tok,
            }
            if metrics:
                cluster_report["stop_reasons"] = [m.get("stop_reason") for m in metrics]
            report[str(cid)] = cluster_report

            totals["n_calls"] += len(calls)
            totals["n_cases"] += result["n_cases"]
            totals["n_occurrences"] += result["n_occurrences"]
            totals["n_untagged_accepted"] += result["n_untagged_accepted"]
            totals["n_untagged_dropped"] += result["n_untagged_dropped"]
            totals["n_invalid_ids"] += result["n_invalid_ids"]
            totals["n_casename_warnings"] += result["n_casename_warnings"]
            totals["n_id_conflicts"] += result["n_id_conflicts"]
            totals["n_unrouted_ids"] += result["n_unrouted_ids"]
            totals["input_tokens"] += in_tok
            totals["output_tokens"] += out_tok

            print(
                f"  {cid}: {len(calls)} calls, {result['n_cases']} cases, "
                f"{result['n_occurrences']} occurrences, "
                f"untagged +{result['n_untagged_accepted']}/-{result['n_untagged_dropped']}"
                + (f", tokens {in_tok}/{out_tok}" if metrics else "")
            )
    return report, totals


# ── Realtime mode ──────────────────────────────────────────────────

def call_haiku_realtime(call: CallInput) -> tuple[dict, dict]:
    user_text = json.dumps(call.to_llm_input(), ensure_ascii=False)
    response = converse_completion(
        system_prompt=citation_grouping,
        opinion_text=user_text,
        model_id=HAIKU_MODEL_ID,
        tool_spec_override=citation_grouping_tool_spec,
    )
    emission = response.get("cited_cases", {}) if response else {}
    if not isinstance(emission, dict):
        emission = {}
    metrics = {
        "input_tokens": response.get("input_tokens", 0) if response else 0,
        "output_tokens": response.get("output_tokens", 0) if response else 0,
        "latency_ms": response.get("latency_ms", 0) if response else 0,
        "stop_reason": response.get("stop_reason", "unknown") if response else "no_response",
    }
    return emission, metrics


def run_realtime(cluster_ids: list[int], tagged_dir: Path) -> None:
    print(f"Processing {len(cluster_ids)} clusters from {tagged_dir} (realtime mode)\n")
    calls_by_cluster: dict[int, list[CallInput]] = {}
    emissions_by_cluster: dict[int, list[dict]] = {}
    metrics_by_cluster: dict[int, list[dict]] = {}

    started = time.time()
    for cid in cluster_ids:
        try:
            artifact = load_artifact(cid, tagged_dir)
        except FileNotFoundError:
            print(f"  {cid}: SKIP — no tagged artifact")
            continue
        calls = prepare_calls(artifact)
        # Persist call metadata so --postprocess-only can replay this run
        # without re-calling the model.
        (CALLS_DIR / f"{cid}.json").write_text(
            json.dumps(serialize_calls(calls), ensure_ascii=False)
        )
        emissions: list[dict] = []
        metrics: list[dict] = []
        for call_idx, call in enumerate(calls):
            emission, m = call_haiku_realtime(call)
            # Persist raw input + output per call so the model's literal answer
            # is recoverable for debugging without re-running inference.
            # Includes the system prompt + sha256 so we can verify which
            # prompt version was sent for any given call.
            prompt_sha = hashlib.sha256(citation_grouping.encode("utf-8")).hexdigest()
            raw_path = RAW_REALTIME_DIR / f"{cid}_call_{call_idx}.json"
            raw_path.write_text(json.dumps({
                "cluster_id": cid,
                "call_idx": call_idx,
                "model_id": HAIKU_MODEL_ID,
                "prompt_sha256": prompt_sha,
                "system_prompt": citation_grouping,
                "input": call.to_llm_input(),
                "metrics": m,
                "emission": emission,
            }, ensure_ascii=False, indent=2))
            emissions.append(emission)
            metrics.append(m)
        calls_by_cluster[cid] = calls
        emissions_by_cluster[cid] = emissions
        metrics_by_cluster[cid] = metrics

    report, totals = emit_per_cluster_outputs(
        cluster_ids, tagged_dir, emissions_by_cluster, calls_by_cluster,
        call_metrics_by_cluster=metrics_by_cluster,
    )
    write_report(report, totals, time.time() - started, len(cluster_ids))


# ── Postprocess-only mode ──────────────────────────────────────────

def run_postprocess_only(cluster_ids: list[int], tagged_dir: Path) -> None:
    """Re-run postprocess on cached realtime emissions; no model calls.

    Reads CallInputs from `data/phase2_calls/{cid}.json` (saved by
    `run_realtime`) and emissions from `data/phase2_raw_realtime/{cid}_call_{i}.json`.
    Useful for iterating on postprocess code without paying for inference.

    Clusters missing either input are skipped with a warning. Re-run
    realtime once on those clusters to populate the cache.
    """
    print(f"Re-running postprocess on cached emissions for "
          f"{len(cluster_ids)} clusters (no model calls)\n")
    calls_by_cluster: dict[int, list[CallInput]] = {}
    emissions_by_cluster: dict[int, list[dict]] = {}
    metrics_by_cluster: dict[int, list[dict]] = {}

    started = time.time()
    for cid in cluster_ids:
        calls_path = CALLS_DIR / f"{cid}.json"
        if not calls_path.exists():
            print(f"  {cid}: SKIP — no cached calls at {calls_path}. "
                  f"Run `--mode realtime --clusters {cid}` once to populate.")
            continue
        calls = deserialize_calls(json.loads(calls_path.read_text()))
        emissions: list[dict] = []
        metrics: list[dict] = []
        missing_raw: list[int] = []
        for call_idx in range(len(calls)):
            raw_path = RAW_REALTIME_DIR / f"{cid}_call_{call_idx}.json"
            if not raw_path.exists():
                missing_raw.append(call_idx)
                continue
            raw = json.loads(raw_path.read_text())
            emissions.append(raw.get("emission") or {})
            metrics.append(raw.get("metrics") or {})
        if missing_raw:
            print(f"  {cid}: SKIP — missing raw emissions for call_idx "
                  f"{missing_raw}. Re-run realtime on this cluster to repair.")
            continue
        calls_by_cluster[cid] = calls
        emissions_by_cluster[cid] = emissions
        metrics_by_cluster[cid] = metrics

    report, totals = emit_per_cluster_outputs(
        cluster_ids, tagged_dir, emissions_by_cluster, calls_by_cluster,
        call_metrics_by_cluster=metrics_by_cluster,
    )
    write_report(report, totals, time.time() - started, len(cluster_ids))


# ── Batch mode ─────────────────────────────────────────────────────

def batch_prep(
    cluster_ids: list[int], tagged_dir: Path,
) -> tuple[dict[int, list[CallInput]], list[dict]]:
    """Local-only prep: paginate, build the JSONL and per-cluster calls metadata.

    No S3 upload, no Bedrock submit. Writes:
      DATA_DIR / "phase2_input.jsonl"        — batch input
      CALLS_DIR / "{cluster_id}.json"        — call metadata for collect

    Returns (calls_by_cluster, records) — records is the in-memory list of
    Bedrock batch records, in the same order as JSONL lines.
    """
    init_dirs()
    calls_by_cluster: dict[int, list[CallInput]] = {}
    records: list[dict] = []

    print(f"Paginating {len(cluster_ids)} clusters…")
    for cid in cluster_ids:
        try:
            artifact = load_artifact(cid, tagged_dir)
        except FileNotFoundError:
            print(f"  {cid}: SKIP — no tagged artifact")
            continue
        calls = prepare_calls(artifact)
        calls_by_cluster[cid] = calls
        for call_idx, call in enumerate(calls):
            record = build_citation_grouping_record(
                record_id=make_record_id(cid, call_idx),
                llm_input=call.to_llm_input(),
                system_prompt=citation_grouping,
                model_id=HAIKU_MODEL_ID,
            )
            records.append(record)

    # Persist call metadata locally so collect can reconstruct CallInputs.
    for cid, calls in calls_by_cluster.items():
        (CALLS_DIR / f"{cid}.json").write_text(
            json.dumps(serialize_calls(calls), ensure_ascii=False)
        )

    # Write the JSONL locally.
    jsonl_local = DATA_DIR / "phase2_input.jsonl"
    with open(jsonl_local, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"Built {len(records)} batch records across {len(calls_by_cluster)} clusters.")
    print(f"Wrote {jsonl_local} ({jsonl_local.stat().st_size:,} bytes).")
    return calls_by_cluster, records


def print_prep_summary(
    calls_by_cluster: dict[int, list[CallInput]],
    records: list[dict],
) -> None:
    """Print a sanity-check summary of the prepped batch."""
    import statistics
    n_records = len(records)
    n_clusters = len(calls_by_cluster)
    calls_per_cluster = [len(calls) for calls in calls_by_cluster.values()]
    multi_call = sum(1 for n in calls_per_cluster if n > 1)
    # Token totals from the CallInput items (sum across all calls' items).
    tokens_per_call = [
        sum(it["tokens"] for it in c.items)
        for calls in calls_by_cluster.values() for c in calls
    ]
    total_input_tokens = sum(tokens_per_call)
    # Chunked-opinion stats.
    chunked_clusters = sum(
        1 for calls in calls_by_cluster.values()
        if any(it["total_chunks"] > 1 for c in calls for it in c.items)
    )

    print()
    print("=== Phase 2 batch prep summary ===")
    print(f"Clusters processed:        {n_clusters:>10,}")
    print(f"Total LLM calls (records): {n_records:>10,}")
    print(f"  >= {BATCH_MIN_RECORDS} required for Bedrock batch: "
          f"{'YES' if n_records >= BATCH_MIN_RECORDS else 'NO — use --mode realtime'}")
    if calls_per_cluster:
        print(f"Calls per cluster:         "
              f"min={min(calls_per_cluster)}, "
              f"median={statistics.median(calls_per_cluster):.0f}, "
              f"mean={statistics.mean(calls_per_cluster):.2f}, "
              f"max={max(calls_per_cluster)}")
        print(f"  multi-call clusters:     {multi_call:>10,} "
              f"({100*multi_call/n_clusters:.1f}%)")
    print(f"Chunked-opinion clusters:  {chunked_clusters:>10,}")
    if tokens_per_call:
        print(f"Input tokens (sum):        {total_input_tokens:>10,}")
        print(f"Per-call tokens:           "
              f"min={min(tokens_per_call):,}, "
              f"median={int(statistics.median(tokens_per_call)):,}, "
              f"mean={int(statistics.mean(tokens_per_call)):,}, "
              f"max={max(tokens_per_call):,}")
        # Cost estimate at Haiku 4.5 batch input pricing ($1/M × 50% = $0.50/M).
        est_cost = total_input_tokens * 0.50 / 1_000_000
        print(f"Est. input cost (Haiku batch, $0.50/M): ${est_cost:.4f}  "
              f"(output cost separate and variable)")

    print()
    print("Sample record (first one):")
    if records:
        first = records[0]
        sample = {
            "recordId": first["recordId"],
            "modelInput.system": first["modelInput"]["system"][:120] + "…",
            "modelInput.max_tokens": first["modelInput"]["max_tokens"],
            "modelInput.tool_choice": first["modelInput"]["tool_choice"],
            "modelInput.tools[0].name": first["modelInput"]["tools"][0]["name"],
            "modelInput.messages[0].content[0].text (head)":
                first["modelInput"]["messages"][0]["content"][0]["text"][:200] + "…",
        }
        for k, v in sample.items():
            print(f"  {k}: {v}")

    print()
    print(f"Files:")
    print(f"  JSONL:         {DATA_DIR / 'phase2_input.jsonl'}")
    print(f"  Calls dir:     {CALLS_DIR}/  ({n_clusters} files)")
    print()
    print("Inspect the JSONL with: head -c 4096 data/phase2_input.jsonl | python -m json.tool")
    print("Re-run without --prep-only when ready to upload + submit.")


def batch_submit(cluster_ids: list[int], tagged_dir: Path, run_id: str) -> tuple[str, int]:
    """Full submit: prep locally, then upload to S3 + submit a batch job.
    Returns (job_arn, n_records).
    """
    calls_by_cluster, records = batch_prep(cluster_ids, tagged_dir)
    n_records = len(records)
    if n_records < BATCH_MIN_RECORDS:
        raise SystemExit(
            f"ERROR: only {n_records} records — Bedrock batch requires ≥{BATCH_MIN_RECORDS}. "
            f"Use --mode realtime for smaller runs."
        )

    jsonl_local = DATA_DIR / "phase2_input.jsonl"
    print(f"Uploading {jsonl_local} → s3://…/{s3_phase2_input_key(run_id)}")
    upload_file(str(jsonl_local), s3_phase2_input_key(run_id))

    # Also upload calls metadata to S3 for cross-machine collect resilience.
    calls_metadata = {
        str(cid): serialize_calls(calls) for cid, calls in calls_by_cluster.items()
    }
    upload_json(calls_metadata, s3_phase2_calls_key(run_id))

    # Submit the batch job.
    job_arn = submit_batch_job(
        job_name=f"phase2-citation-grouping-{run_id[:8]}",
        s3_input_uri=s3_uri(s3_phase2_input_key(run_id)),
        s3_output_uri=s3_uri(s3_phase2_raw_prefix(run_id)),
        model_id=HAIKU_MODEL_ID,
    )

    write_manifest(run_id, {
        "phase2": {
            "job_arn": job_arn,
            "n_records": n_records,
            "cluster_ids": list(calls_by_cluster.keys()),
            "model_id": HAIKU_MODEL_ID,
        },
    })
    LAST_RUN_ID_PATH.write_text(run_id)

    print(f"Submitted batch job: {job_arn}")
    print(f"Run id: {run_id}  (saved to {LAST_RUN_ID_PATH})")
    return job_arn, n_records


def batch_collect(run_id: str, tagged_dir: Path) -> None:
    """Download the batch outputs, parse, group by cluster, postprocess."""
    init_dirs()
    print(f"Collecting batch outputs for run_id={run_id}")

    manifest = read_manifest(run_id)
    cluster_ids: list[int] = manifest["phase2"]["cluster_ids"]

    # Locate the batch output JSONL — Bedrock writes one .jsonl.out file alongside
    # the input filename in the raw output prefix.
    raw_prefix = s3_phase2_raw_prefix(run_id)
    out_keys = [k for k in list_keys(raw_prefix) if k.endswith(".jsonl.out")]
    if not out_keys:
        raise SystemExit(f"No .jsonl.out under {raw_prefix} — batch job may not be complete.")
    if len(out_keys) > 1:
        print(f"  (found {len(out_keys)} output files; processing all)")

    # Collect emissions: {cluster_id: {call_idx: emission}}
    emissions_raw: dict[int, dict[int, dict]] = defaultdict(dict)
    n_lines = 0
    n_emissions = 0
    local_out_dir = DATA_DIR / "phase2_raw"
    local_out_dir.mkdir(parents=True, exist_ok=True)
    for out_key in out_keys:
        local_path = local_out_dir / Path(out_key).name
        download_to_file(out_key, str(local_path))
        with open(local_path) as f:
            for line in f:
                n_lines += 1
                rec = json.loads(line)
                parsed = parse_record_id(rec.get("recordId", ""))
                if parsed is None:
                    continue
                cid, call_idx = parsed
                model_output = rec.get("modelOutput") or {}
                emission = extract_citation_grouping_emission(model_output)
                emissions_raw[cid][call_idx] = emission
                n_emissions += 1

    print(f"Parsed {n_emissions} emissions from {n_lines} output lines "
          f"across {len(emissions_raw)} clusters.")

    # Reconstruct CallInputs from local cache.
    calls_by_cluster: dict[int, list[CallInput]] = {}
    for cid in cluster_ids:
        calls_path = CALLS_DIR / f"{cid}.json"
        if not calls_path.exists():
            print(f"  {cid}: SKIP — no local calls metadata; was submit run from this machine?")
            continue
        calls_by_cluster[cid] = deserialize_calls(json.loads(calls_path.read_text()))

    # Order emissions per cluster by call_idx.
    emissions_by_cluster: dict[int, list[dict]] = {}
    for cid, calls in calls_by_cluster.items():
        ordered = [emissions_raw.get(cid, {}).get(i, {}) for i in range(len(calls))]
        emissions_by_cluster[cid] = ordered

    started = time.time()
    report, totals = emit_per_cluster_outputs(
        cluster_ids, tagged_dir, emissions_by_cluster, calls_by_cluster,
    )
    write_report(report, totals, time.time() - started, len(cluster_ids))


def run_batch(cluster_ids: list[int], tagged_dir: Path, *, submit_only: bool) -> None:
    run_id = generate_run_id()
    job_arn, _ = batch_submit(cluster_ids, tagged_dir, run_id)
    if submit_only:
        print(f"--submit-only: not waiting. Resume with:  python run_phase2.py --run-id {run_id}")
        return
    print(f"Waiting for job to complete (polling every 60s)…")
    results = wait_for_jobs([job_arn], poll_interval=60)
    status = results[job_arn]["status"]
    print(f"Job finished with status: {status}")
    if status != "Completed":
        print(f"  message: {results[job_arn].get('message', '')}")
        raise SystemExit(1)
    batch_collect(run_id, tagged_dir)


# ── CLI ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode", choices=("batch", "realtime"), default="batch",
        help="Inference mode (default: batch; needs ≥100 calls and AWS env).",
    )
    parser.add_argument(
        "--tagged-dir", default=str(DEFAULT_TAGGED_DIR),
        help="Directory of Phase 1 tagged JSON artifacts.",
    )
    parser.add_argument(
        "--clusters", nargs="*", type=int, default=None,
        help="Specific cluster IDs to process (default: all in --tagged-dir).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N clusters (smoke testing).",
    )
    parser.add_argument(
        "--prep-only", action="store_true",
        help="Batch mode: paginate + build JSONL + save call metadata locally; "
             "print a summary; do NOT upload to S3 or submit. Use to dry-run "
             "the data prep before paying for inference.",
    )
    parser.add_argument(
        "--postprocess-only", action="store_true",
        help="Skip inference; re-run postprocess on cached realtime emissions "
             "in data/phase2_raw_realtime/ using saved CallInputs from "
             "data/phase2_calls/. Clusters missing either are skipped.",
    )
    parser.add_argument(
        "--submit-only", action="store_true",
        help="Batch mode: submit and exit without waiting. Pair with --run-id later.",
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Batch mode: skip submit and collect outputs for this run_id. "
             "Defaults to the run_id saved by the last submit on this machine.",
    )
    args = parser.parse_args()

    tagged_dir = Path(args.tagged_dir).resolve()
    if not tagged_dir.is_dir():
        sys.exit(f"ERROR: --tagged-dir not found: {tagged_dir}")

    init_dirs()

    if args.postprocess_only:
        cluster_ids = resolve_cluster_ids(args, tagged_dir)
        run_postprocess_only(cluster_ids, tagged_dir)
        return

    if args.mode == "realtime":
        cluster_ids = resolve_cluster_ids(args, tagged_dir)
        run_realtime(cluster_ids, tagged_dir)
        return

    # Batch mode.
    if args.run_id:
        # Collect-only path for an existing run.
        batch_collect(args.run_id, tagged_dir)
        return

    cluster_ids = resolve_cluster_ids(args, tagged_dir)
    if args.prep_only:
        calls_by_cluster, records = batch_prep(cluster_ids, tagged_dir)
        print_prep_summary(calls_by_cluster, records)
        return
    run_batch(cluster_ids, tagged_dir, submit_only=args.submit_only)


if __name__ == "__main__":
    main()
