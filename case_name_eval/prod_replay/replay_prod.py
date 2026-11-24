"""Replay the production case-name call on sampled records and keep the raw reply.

    conda run -n citator uv run python replay_prod.py --limit 100

Sends the same request as `classify_case_name_by_llm` in CourtListener: the
production system prompt, the first 2,000 characters as the user message, the
production response model, `gpt-4o-mini` through `instructor`, temperature 0,
300 output tokens. The prompt and response model are imported from unmodified
copies in `prod_code/`. Run it from this folder.

One difference from production: the call uses `create_with_completion` instead
of `create`. It sends the identical request and also returns the raw reply,
which production discards.

`--wide-set` is a separate experiment, not a replay: it sends the first 8,000
characters of the records whose caption is missing or cut off at 2,000.

Reads records from ../data/annotations.db. Appends one JSON line per record to
../data/prod_replay/<run>.jsonl and skips records already there. The API key is
read from OPENAI_KEY.
"""

import argparse
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import instructor
from prod_code.llm_models import CaseNameExtractionResponse
from prod_code.system import CASE_NAME_EXTRACT_SYSTEM

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
MODEL = "openai/gpt-4o-mini"
TEMPERATURE = 0.0
MAX_COMPLETION_TOKENS = 300
WIDE_WINDOW_CHARS = 8000


def call(client, text: str, max_tokens: int = MAX_COMPLETION_TOKENS) -> dict:
    """One production-shaped request. Returns parsed fields plus the raw reply."""
    try:
        parsed, completion = client.chat.completions.create_with_completion(
            messages=[
                {"role": "system", "content": CASE_NAME_EXTRACT_SYSTEM},
                {"role": "user", "content": [{"type": "text", "text": text}]},
            ],
            temperature=TEMPERATURE,
            max_tokens=max_tokens,
            response_model=CaseNameExtractionResponse,
        )
    except (
        Exception
    ) as e:  # the outcome of every record is recorded, including failures
        cause = e.__cause__
        return {
            "error": f"{type(e).__name__}: {e}"[:600],
            "error_cause": f"{type(cause).__name__}: {cause}"[:600] if cause else None,
        }
    choice = completion.choices[0]
    message = choice.message
    return {
        "error": None,
        "returned_case_name": parsed.case_name,
        "returned_case_name_full": parsed.case_name_full,
        # What the task would save: `llm_response.<field> or ""`.
        "saved_case_name": parsed.case_name or "",
        "saved_case_name_full": parsed.case_name_full or "",
        "finish_reason": choice.finish_reason,
        "raw_content": message.content,
        "raw_tool_arguments": [
            t.function.arguments for t in (message.tool_calls or [])
        ],
        "resolved_model": completion.model,
        "prompt_tokens": completion.usage.prompt_tokens,
        "completion_tokens": completion.usage.completion_tokens,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--run", default="gpt-4o-mini_prod-prompt")
    parser.add_argument(
        "--wide-set",
        action="store_true",
        help="Run the records in ../data/window_8k/ids.json on their first "
        "8,000 characters. Not what production sends: production stops at 2,000.",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_COMPLETION_TOKENS,
        help="Output token limit. Production uses 300; raise it only to retry "
        "records that failed on the limit.",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Run again the records whose earlier call failed.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_KEY")
    if not api_key:
        raise SystemExit("OPENAI_KEY is not set (run through `conda run -n citator`).")

    conn = sqlite3.connect(DATA_DIR / "annotations.db")
    if args.wide_set:
        ids = json.loads((DATA_DIR / "window_8k" / "ids.json").read_text())
        marks = ",".join("?" * len(ids))
        records = conn.execute(
            f"SELECT cluster_id, substr(plain_text, 1, {WIDE_WINDOW_CHARS}) FROM records "
            f"WHERE cluster_id IN ({marks}) ORDER BY position",
            ids,
        ).fetchall()
    else:
        records = conn.execute(
            "SELECT cluster_id, llm_input FROM records WHERE split = ? "
            "ORDER BY position LIMIT ?",
            (args.split, args.limit),
        ).fetchall()

    out_path = DATA_DIR / "prod_replay" / f"{args.run}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        kept = [json.loads(line) for line in out_path.read_text().splitlines() if line]
        if args.retry_errors:
            kept = [item for item in kept if not item["error"]]
            out_path.write_text(
                "".join(json.dumps(k, ensure_ascii=False) + "\n" for k in kept)
            )
        done = {item["cluster_id"] for item in kept}
    todo = [(cid, text) for cid, text in records if cid not in done]
    print(
        f"{len(records)} selected, {len(done & {c for c, _ in records})} already done, {len(todo)} to run"
    )

    # Same client construction as production's call_llm.
    client = instructor.from_provider(MODEL, async_client=False, api_key=api_key)

    with (
        ThreadPoolExecutor(max_workers=args.workers) as pool,
        open(out_path, "a") as out,
    ):
        results = pool.map(
            lambda item: (item[0], call(client, item[1], args.max_tokens)), todo
        )
        for n, (cluster_id, result) in enumerate(results, 1):
            row = {"cluster_id": cluster_id, "max_tokens": args.max_tokens, **result}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if n % 20 == 0:
                print(f"  {n}/{len(todo)}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
