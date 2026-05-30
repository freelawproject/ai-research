import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd

from json_repair import repair_json

from utils.bedrock_converse_utils import converse_completion
from utils.instructions import reevaluator

logger = logging.getLogger(__name__)

REEVALUATION_SYSTEM_PROMPT = reevaluator
BATCH_SIZE = 5

reevaluation_schema = {
    "type": "object",
    "properties": {
        "reassessments": {
            "type": "array",
            "description": "One reassessment per Cited Case, in the same order as provided.",
            "items": {
                "type": "object",
                "properties": {
                    "citedCaseId": {
                        "type": "integer",
                        "description": "The 1-based index of the Cited Case from the input.",
                    },
                    "treatment": {
                        "type": "string",
                        "description": "The reassessed treatment label.",
                        "maxLength": 100,
                    },
                    "rationale": {
                        "type": "string",
                        "description": "A 1-sentence explanation.",
                        "maxLength": 300,
                    },
                },
                "required": ["citedCaseId", "treatment", "rationale"],
            },
        },
    },
    "required": ["reassessments"],
}

reevaluation_tool_spec = {
    "toolSpec": {
        "name": "reassess_treatments",
        "description": "Reassess whether each Cited Case should receive a negative treatment.",
        "inputSchema": {"json": reevaluation_schema},
    }
}


def _format_batch_user_text(rows):
    """Format a batch of rows into a single user prompt."""
    parts = [
        "Re-evaluate each of the following Cited Cases. For each one, independently analyze "
        "the directionality and determine the correct treatment. The model's original treatment "
        "and rationale may be wrong.\n"
    ]
    for i, (_, row) in enumerate(rows, 1):
        parts.append(
            f"--- Cited Case {i} ---\n"
            f"Cited Case Name: {row.get('caseName', 'N/A')}\n"
            f"Citation: {row.get('mainCitationString', 'N/A')}\n"
            f"Model's Assigned Treatment: {row.get('treatment', 'N/A')}\n"
            f"Quote from Opinion: {row.get('quote', 'N/A')}\n"
            f"Model's Rationale: {row.get('rationale', 'N/A')}\n"
        )
    return "\n".join(parts)


def extract_reassessments(obj):
    """Extract a list of {citedCaseId, treatment, rationale} dicts from
    whatever shape the model returned. Used by both the on-demand re-eval path
    (where the input is `result["cited_cases"]` from converse_completion) and
    the Bedrock batch re-eval path (where the input is the parsed JSON from a
    .jsonl.out record).
    """
    # tool_use returns the schema object directly
    if isinstance(obj, dict):
        return obj.get("reassessments", [])

    if isinstance(obj, list):
        # Could be the reassessments array directly
        if len(obj) > 0 and isinstance(obj[0], dict):
            if "reassessments" in obj[0]:
                return obj[0]["reassessments"]
            # Already a list of reassessment dicts
            if "citedCaseId" in obj[0]:
                return obj
        return []

    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except json.JSONDecodeError:
            if repair_json:
                parsed = repair_json(obj, return_objects=True)
            else:
                return []
        if isinstance(parsed, dict):
            return parsed.get("reassessments", [])
        return []

    return []


def _parse_batch_result(result):
    """On-demand wrapper: converse_completion stuffs the model's JSON under
    `cited_cases`, so dig in there before extracting."""
    return extract_reassessments(result.get("cited_cases"))


def _save_batch_incremental(batch_idx, reassessments, raw_info, output_dir):
    """Save a single batch's re-evaluation results to disk immediately."""
    incremental_dir = os.path.join(output_dir, "reevaluation_incremental")
    os.makedirs(incremental_dir, exist_ok=True)
    path = os.path.join(incremental_dir, f"batch_{batch_idx}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"reassessments": reassessments, "raw_info": raw_info}, f)


def _load_incremental_reevaluations(output_dir):
    """Load any previously saved incremental re-evaluation batch results."""
    incremental_dir = os.path.join(output_dir, "reevaluation_incremental")
    results = {}
    if not os.path.isdir(incremental_dir):
        return results
    for fname in os.listdir(incremental_dir):
        if fname.startswith("batch_") and fname.endswith(".json"):
            try:
                batch_idx = int(fname.replace("batch_", "").replace(".json", ""))
                path = os.path.join(incremental_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results[batch_idx] = (data.get("reassessments", []), data.get("raw_info", {}))
            except Exception as e:
                logger.warning(f"Failed to load incremental re-eval {fname}: {e}")
    return results


def _reevaluate_batch(rows, model_id):
    """Call the model to re-evaluate a batch of flagged Cited Cases.

    Returns (reassessments_list, raw_info_dict) where raw_info_dict
    contains model, stop_reason, input_tokens, output_tokens, latency_ms.
    """
    user_text = _format_batch_user_text(rows)
    try:
        result = converse_completion(
            REEVALUATION_SYSTEM_PROMPT,
            user_text,
            model_id=model_id,
            tool_spec_override=reevaluation_tool_spec,
        )
        reassessments = _parse_batch_result(result)
        raw_info = {
            "model": result.get("model", ""),
            "stop_reason": result.get("stop_reason", ""),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
            "latency_ms": result.get("latency_ms", 0),
            "raw_output": result.get("cited_cases", {}),
        }
        return reassessments, raw_info
    except Exception as e:
        citations = [r.get("mainCitationString", "?") for _, r in rows]
        logger.error(f"Re-evaluation batch failed for {citations}: {e}")
        return [], {}


def reevaluate_flagged(flagged_df, model_id, output_dir=None, max_workers=5, resume=False):
    """Re-evaluate flagged results using the model.

    Sends cited cases to the model in batches of up to BATCH_SIZE to
    amortize the cost of the long system prompt.

    Returns the flagged_df with updated treatment and rationale columns.
    """
    if flagged_df.empty:
        return flagged_df

    # Build batches of (index, row) tuples
    all_rows = list(flagged_df.iterrows())
    batches = [all_rows[i:i + BATCH_SIZE] for i in range(0, len(all_rows), BATCH_SIZE)]

    total_batches = len(batches)
    total_rows = len(flagged_df)
    logger.info(
        f"Re-evaluating {total_rows} flagged results in {total_batches} batches "
        f"(batch size={BATCH_SIZE})"
    )

    # Resume from any previously saved incremental results
    batch_results = {}  # batch index → (batch, reassessments, raw_info)
    already_done = {}
    if resume and output_dir:
        already_done = _load_incremental_reevaluations(output_dir)
        for batch_idx, (reassessments, raw_info) in already_done.items():
            if batch_idx < len(batches):
                batch_results[batch_idx] = (batches[batch_idx], reassessments, raw_info)

    remaining = [(idx, b) for idx, b in enumerate(batches) if idx not in already_done]
    if already_done:
        logger.info(f"Resuming: {len(already_done)} batches already completed, {len(remaining)} remaining")

    processed = len(already_done)
    save_lock = Lock()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_batch = {
            executor.submit(_reevaluate_batch, batch, model_id): (batch_idx, batch)
            for batch_idx, batch in remaining
        }
        for future in as_completed(future_to_batch):
            batch_idx, batch = future_to_batch[future]
            processed += 1
            try:
                reassessments, raw_info = future.result()
                batch_results[batch_idx] = (batch, reassessments, raw_info)

                # Save immediately to disk
                if output_dir:
                    with save_lock:
                        _save_batch_incremental(batch_idx, reassessments, raw_info, output_dir)

                logger.info(f"Re-evaluated batch {processed}/{total_batches}")
            except Exception as e:
                logger.error(f"Re-evaluation batch {batch_idx} error: {e}")
                batch_results[batch_idx] = (batch, [], {})

    # Save consolidated raw results
    raw_rows = []
    for batch_idx in sorted(batch_results.keys()):
        _, reassessments, raw_info = batch_results[batch_idx]
        if raw_info:
            raw_rows.append({
                "batch": batch_idx,
                "batch_size": len(batches[batch_idx]) if batch_idx < len(batches) else 0,
                "model": raw_info.get("model", ""),
                "stop_reason": raw_info.get("stop_reason", ""),
                "input_tokens": raw_info.get("input_tokens", 0),
                "output_tokens": raw_info.get("output_tokens", 0),
                "latency_ms": raw_info.get("latency_ms", 0),
                "raw_output": raw_info.get("raw_output", {}),
            })
    if raw_rows and output_dir:
        os.makedirs(output_dir, exist_ok=True)
        raw_df = pd.DataFrame(raw_rows)
        path = os.path.join(output_dir, "reevaluation_raw_results.csv")
        raw_df.to_csv(path, index=False)
        logger.info(f"Saved re-evaluation raw results ({len(raw_df)} batches) to {path}")

    # Collect parsed re-evaluation results (before applying to flagged_df)
    parsed_rows = []
    for batch_idx in sorted(batch_results.keys()):
        batch, reassessments, _ = batch_results[batch_idx]
        reeval_by_id = {r.get("citedCaseId"): r for r in reassessments if isinstance(r, dict)}
        for position, (df_idx, row) in enumerate(batch, 1):
            reeval = reeval_by_id.get(position, {})
            parsed_rows.append({
                "citing_cluster_id": row.get("citing_cluster_id", ""),
                "mainCitationString": row.get("mainCitationString", ""),
                "caseName": row.get("caseName", ""),
                "original_treatment": row.get("treatment", ""),
                "original_rationale": row.get("rationale", ""),
                "original_quote": row.get("quote", ""),
                "reevaluated_treatment": reeval.get("treatment", ""),
                "reevaluated_rationale": reeval.get("rationale", ""),
            })
    if parsed_rows and output_dir:
        parsed_reeval_df = pd.DataFrame(parsed_rows)
        path = os.path.join(output_dir, "reevaluation_parsed_results.csv")
        parsed_reeval_df.to_csv(path, index=False)
        logger.info(f"Saved re-evaluation parsed results ({len(parsed_reeval_df)} rows) to {path}")

    # Apply results back to the dataframe
    updated = flagged_df.copy()
    for batch_idx in sorted(batch_results.keys()):
        batch, reassessments, _ = batch_results[batch_idx]
        reeval_by_id = {r.get("citedCaseId"): r for r in reassessments if isinstance(r, dict)}
        for position, (df_idx, row) in enumerate(batch, 1):
            reeval = reeval_by_id.get(position, {})
            new_treatment = reeval.get("treatment", "")
            new_rationale = reeval.get("rationale", "")
            if new_treatment and new_treatment != "Cited by":
                updated.at[df_idx, "treatment"] = new_treatment
                updated.at[df_idx, "rationale"] = f"[Re-evaluated] {new_rationale}"
                logger.info(
                    f"Reassigned {updated.at[df_idx, 'mainCitationString']}: "
                    f"{row.get('treatment', '')} → {new_treatment}"
                )

    n_changed = (updated["treatment"] != flagged_df["treatment"]).sum()
    logger.info(f"Re-evaluation complete: {n_changed}/{total_rows} treatments changed")
    return updated
