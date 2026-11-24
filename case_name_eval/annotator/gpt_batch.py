"""Second-model pass with GPT-6.2 Sol over selected records (OpenAI Batch API).

    conda run -n citator uv run python gpt_batch.py export --run NAME --select KIND
    conda run -n citator uv run python gpt_batch.py submit --run NAME
    conda run -n citator uv run python gpt_batch.py wait   --run NAME   # polls until the batch ends
    conda run -n citator uv run python gpt_batch.py fetch  --run NAME   # saves results and loads them

--select picks the records:

- full   Opus full caption differs from production or from the production replay
- short  short name still has no label
- wide   records in ../data/window_8k/ids.json: caption missing or cut off at
         2,000 characters. The request holds the first 8,000 characters.

Records that already have an answer from this model at the same window are
left out, so a second export under a new --run name retries only failures.

Each request holds the annotation guidelines as the system message and the
start of the document as the user message, and asks for both names as strict
JSON. Results are loaded as suggestions under the model's name, then every
name on which this model and Opus agree is auto-accepted. Fields that already
have a label are not touched.

Files live in ../data/gpt_batch/<run>/. The API key is read from OPENAI_KEY.
"""

import argparse
import json
import os
import time
from pathlib import Path

import store
from openai import OpenAI

MODEL = "gpt-6.2-sol"
SOURCE = store.SECOND_MODEL
REASONING_EFFORT = "medium"
MAX_COMPLETION_TOKENS = 6000  # includes reasoning tokens
TERMINAL = ("completed", "failed", "expired", "cancelled")
GUIDELINES = Path(__file__).resolve().parent.parent / "annotation_guidelines.md"

INSTRUCTIONS = """You label court-document captions for an evaluation set. A person reviews every answer.

The user message is the first {window:,} characters of one federal district or bankruptcy court document. Find the caption, write `case_name_full`, then derive `case_name` from it, following the guidelines below.{wide_note}

Rules for the answer:
- Use only the text given. Every word of `case_name_full` must come from it. Do not complete a name from memory.
- Both names are required whenever a caption is present.
- `caption_in_input`: "complete" when the whole caption is in the text, "partial" when the caption is cut off by the {window:,}-character limit (record what is visible), "none" when there is no caption (both names are empty strings).
- `notes`: one short sentence when something needs a person's attention, otherwise an empty string.

# Guidelines

"""
WIDE_NOTE = (
    " In this run the text is longer than usual: wherever the guidelines say"
    " 2,000 characters, read 8,000."
)

SCHEMA = {
    "name": "case_names",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["case_name_full", "case_name", "caption_in_input", "notes"],
        "properties": {
            "case_name_full": {"type": "string"},
            "case_name": {"type": "string"},
            "caption_in_input": {
                "type": "string",
                "enum": list(store.CAPTION_IN_INPUT),
            },
            "notes": {"type": "string"},
        },
    },
}


def run_dir(run: str) -> Path:
    path = store.DATA_DIR / "gpt_batch" / run
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_meta(run: str) -> dict:
    path = run_dir(run) / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_meta(run: str, meta: dict) -> None:
    (run_dir(run) / "meta.json").write_text(json.dumps(meta, indent=1))


def client() -> OpenAI:
    key = os.environ.get("OPENAI_KEY")
    if not key:
        raise SystemExit("OPENAI_KEY is not set (run through `conda run -n citator`).")
    return OpenAI(api_key=key)


def wide_ids() -> set[int]:
    path = store.WINDOW_DIR / "ids.json"
    return set(json.loads(path.read_text())) if path.exists() else set()


def select(conn, kind: str) -> list[dict]:
    """The records for one --select kind, as {cluster_id, text}."""
    wide = wide_ids()
    answered = {
        r["cluster_id"]
        for r in conn.execute(
            "SELECT cluster_id FROM suggestions WHERE source = ?", (SOURCE,)
        )
    }
    rows = conn.execute(
        """
        SELECT r.cluster_id, r.llm_input, r.plain_text, p.case_name_full AS opus_full,
               p.full_agrees, s.case_name_full AS replay_full,
               EXISTS (SELECT 1 FROM field_labels l
                       WHERE l.cluster_id = r.cluster_id AND l.field = 'short') AS short_done
        FROM records r
        JOIN preannotations p USING (cluster_id)
        LEFT JOIN suggestions s
               ON s.cluster_id = r.cluster_id AND s.source = 'GPT-4o-mini replay'
        ORDER BY r.position
        """
    ).fetchall()
    picked = []
    for r in rows:
        cid = r["cluster_id"]
        if kind == "wide":
            if cid in wide:
                picked.append(
                    {
                        "cluster_id": cid,
                        "text": r["plain_text"][: store.WIDE_WINDOW_CHARS],
                    }
                )
            continue
        if cid in wide or cid in answered:
            continue
        differs = not r["full_agrees"] or (
            r["replay_full"] is not None
            and not store.names_agree(r["opus_full"], r["replay_full"])
        )
        if (kind == "full" and differs) or (kind == "short" and not r["short_done"]):
            picked.append({"cluster_id": cid, "text": r["llm_input"]})
    return picked


def cmd_export(args) -> None:
    conn = store.connect()
    wide = args.select == "wide"
    window = store.WIDE_WINDOW_CHARS if wide else store.MODEL_INPUT_CHARS
    system = (
        INSTRUCTIONS.format(window=window, wide_note=WIDE_NOTE if wide else "")
        + GUIDELINES.read_text()
    )
    rows = select(conn, args.select)
    path = run_dir(args.run) / "requests.jsonl"
    with open(path, "w") as out:
        for r in rows:
            body = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": r["text"]},
                ],
                "max_completion_tokens": MAX_COMPLETION_TOKENS,
                "reasoning_effort": REASONING_EFFORT,
                "response_format": {"type": "json_schema", "json_schema": SCHEMA},
            }
            out.write(
                json.dumps(
                    {
                        "custom_id": str(r["cluster_id"]),
                        "method": "POST",
                        "url": "/v1/chat/completions",
                        "body": body,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    write_meta(
        args.run,
        {
            "model": MODEL,
            "select": args.select,
            "window": window,
            "requests": len(rows),
            "created": store.now(),
        },
    )
    print(f"{len(rows)} requests -> {path} ({path.stat().st_size / 1e6:.1f} MB)")


def cmd_submit(args) -> None:
    meta = read_meta(args.run)
    if meta.get("batch_id"):
        raise SystemExit(
            f"Run {args.run!r} was already submitted as {meta['batch_id']}."
        )
    c = client()
    with open(run_dir(args.run) / "requests.jsonl", "rb") as f:
        upload = c.files.create(file=f, purpose="batch")
    batch = c.batches.create(
        input_file_id=upload.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"task": "case_name_eval", "run": args.run},
    )
    meta.update(
        {
            "input_file_id": upload.id,
            "batch_id": batch.id,
            "status": batch.status,
            "submitted": store.now(),
        }
    )
    write_meta(args.run, meta)
    print(
        f"Submitted batch {batch.id} ({batch.status}), {meta['requests']} requests, model {MODEL}"
    )


def refresh(c, run: str) -> dict:
    meta = read_meta(run)
    batch = c.batches.retrieve(meta["batch_id"])
    counts = batch.request_counts
    meta.update(
        {
            "status": batch.status,
            "completed": counts.completed if counts else None,
            "failed": counts.failed if counts else None,
            "output_file_id": batch.output_file_id,
            "error_file_id": batch.error_file_id,
            "checked": store.now(),
        }
    )
    write_meta(run, meta)
    return meta


def cmd_status(args) -> None:
    meta = refresh(client(), args.run)
    print(
        f"{meta['status']}: {meta['completed']} completed, {meta['failed']} failed of {meta['requests']}"
    )


def cmd_wait(args) -> None:
    c = client()
    while True:
        meta = refresh(c, args.run)
        print(
            f"{meta['checked']} {meta['status']}: {meta['completed']}/{meta['requests']}",
            flush=True,
        )
        if meta["status"] in TERMINAL:
            return
        time.sleep(args.every)


def cmd_fetch(args) -> None:
    c = client()
    meta = refresh(c, args.run)
    if meta["status"] not in TERMINAL:
        raise SystemExit(f"Batch is still {meta['status']}.")
    folder = run_dir(args.run)
    for key, name in (
        ("output_file_id", "output.jsonl"),
        ("error_file_id", "errors.jsonl"),
    ):
        if meta.get(key):
            (folder / name).write_bytes(c.files.content(meta[key]).read())

    conn = store.connect()
    note_prefix = "8,000-character window. " if meta.get("select") == "wide" else ""
    loaded, problems, tokens = 0, [], {"prompt": 0, "completion": 0}
    output = folder / "output.jsonl"
    for line in output.read_text().splitlines() if output.exists() else []:
        item = json.loads(line)
        cluster_id = int(item["custom_id"])
        body = (item.get("response") or {}).get("body") or {}
        choice = (body.get("choices") or [{}])[0]
        usage = body.get("usage") or {}
        tokens["prompt"] += usage.get("prompt_tokens", 0)
        tokens["completion"] += usage.get("completion_tokens", 0)
        try:
            answer = json.loads(choice["message"]["content"])
            full, short = answer["case_name_full"].strip(), answer["case_name"].strip()
        except (KeyError, TypeError, json.JSONDecodeError):
            problems.append(
                (cluster_id, choice.get("finish_reason") or item.get("error"))
            )
            continue
        conn.execute(
            """
            INSERT INTO suggestions VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id, source) DO UPDATE SET
                case_name_full = excluded.case_name_full,
                case_name = excluded.case_name, note = excluded.note,
                created_at = excluded.created_at
            """,
            (
                cluster_id,
                SOURCE,
                full,
                short,
                note_prefix + answer.get("notes", ""),
                store.now(),
            ),
        )
        loaded += 1
    conn.commit()
    print(f"Loaded {loaded} of {meta['requests']} as {SOURCE!r}.")
    print(f"Tokens: {tokens['prompt']:,} prompt, {tokens['completion']:,} completion.")
    if problems:
        print(f"{len(problems)} without a usable answer: {problems[:10]}")
    print(f"Auto-accepted: {store.auto_accept(conn) or 'nothing new'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=["export", "submit", "status", "wait", "fetch"]
    )
    parser.add_argument("--run", required=True)
    parser.add_argument("--select", choices=["full", "short", "wide"], default="full")
    parser.add_argument(
        "--every", type=int, default=120, help="seconds between polls for `wait`"
    )
    args = parser.parse_args()
    {
        "export": cmd_export,
        "submit": cmd_submit,
        "status": cmd_status,
        "wait": cmd_wait,
        "fetch": cmd_fetch,
    }[args.command](args)


if __name__ == "__main__":
    main()
