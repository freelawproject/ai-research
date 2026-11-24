"""Check the sub-agents' output files and load them as pre-annotations.

    uv run python collect_preannotations.py --source opus-subagent

For each batch the output must cover exactly the batch's cluster ids, with the
expected fields. Valid records are loaded; everything else is listed so the
batch can be re-run. Fields a person has labeled are never touched.
"""

import argparse
import json
from pathlib import Path

import store

REQUIRED = (
    "cluster_id",
    "case_name_full",
    "case_name",
    "caption_in_input",
    "confidence",
)


def check(item: dict) -> str | None:
    """The reason an output record is not usable, or None when it is."""
    missing = [k for k in REQUIRED if k not in item]
    if missing:
        return f"missing fields {missing}"
    full, short = item["case_name_full"], item["case_name"]
    if not isinstance(full, str) or not isinstance(short, str):
        return "names must be strings"
    if item["caption_in_input"] not in store.CAPTION_IN_INPUT:
        return f"caption_in_input={item['caption_in_input']!r}"
    if item["confidence"] not in store.CONFIDENCE:
        return f"confidence={item['confidence']!r}"
    filled = [bool(full.strip()), bool(short.strip())]
    if item["caption_in_input"] == "none" and any(filled):
        return "caption_in_input is 'none' but a name is filled"
    if item["caption_in_input"] != "none" and not all(filled):
        return "both names are required when a caption is present"
    return None


def read_outputs(
    batch_dir: Path, output_dir: Path
) -> tuple[list[dict], list[str], list[str]]:
    """Read every batch's output file.

    Returns the usable records, the problems found, and the names of the
    batches that have no output yet. A usable record passed `check` and its
    cluster id belongs to the batch, once.
    """
    items, problems, pending = [], [], []
    for batch_path in sorted(batch_dir.glob("batch_*.json")):
        expected = {r["cluster_id"] for r in json.loads(batch_path.read_text())}
        out_path = output_dir / batch_path.name
        if not out_path.exists():
            pending.append(batch_path.name)
            continue
        try:
            output = json.loads(out_path.read_text())
        except json.JSONDecodeError as e:
            problems.append(f"{out_path.name}: not valid JSON ({e})")
            continue
        seen = set()
        for item in output if isinstance(output, list) else []:
            error = check(item) if isinstance(item, dict) else "not an object"
            cluster_id = item.get("cluster_id") if isinstance(item, dict) else None
            if error or cluster_id not in expected or cluster_id in seen:
                reason = error or "unexpected or repeated id"
                problems.append(f"{out_path.name}: cluster {cluster_id}: {reason}")
                continue
            seen.add(cluster_id)
            items.append(item)
        absent = expected - seen
        if absent:
            problems.append(
                f"{out_path.name}: {len(absent)} records missing: {sorted(absent)[:5]}"
            )
    return items, problems, pending


def report(problems: list[str], pending: list[str]) -> None:
    if pending:
        print(f"\n{len(pending)} batches have no output yet: {', '.join(pending)}")
    if problems:
        print(f"\n{len(problems)} problems to fix by re-running the batch:")
        for problem in problems:
            print("  " + problem)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="opus-subagent")
    args = parser.parse_args()

    items, problems, pending = read_outputs(store.BATCH_DIR, store.OUTPUT_DIR)
    with store.session() as conn:
        inputs = {
            r["cluster_id"]: r["llm_input"]
            for r in conn.execute("SELECT cluster_id, llm_input FROM records")
        }
        stray = 0
        for item in items:
            full = item["case_name_full"].strip()
            in_input = store.words(inputs[item["cluster_id"]])
            if store.words(full) - in_input - store.IGNORED_WORDS:
                stray += 1
            conn.execute(
                """
                INSERT INTO preannotations (
                    cluster_id, case_name_full, case_name, caption_in_input,
                    confidence, notes, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cluster_id) DO UPDATE SET
                    case_name_full = excluded.case_name_full,
                    case_name = excluded.case_name,
                    caption_in_input = excluded.caption_in_input,
                    confidence = excluded.confidence, notes = excluded.notes,
                    source = excluded.source, created_at = excluded.created_at
                -- A value already re-read from the wider window is kept.
                WHERE preannotations.window = ?
                """,
                (
                    item["cluster_id"],
                    full,
                    item["case_name"].strip(),
                    item["caption_in_input"],
                    item["confidence"],
                    str(item.get("notes", "")).strip(),
                    args.source,
                    store.now(),
                    store.MODEL_INPUT_CHARS,
                ),
            )
        conn.commit()
        marks = store.refresh_agreement(conn)
        accepted = store.auto_accept(conn)
        total = conn.execute("SELECT count(*) FROM records").fetchone()[0]
        have = conn.execute("SELECT count(*) FROM preannotations").fetchone()[0]

    print(f"Loaded {len(items)} pre-annotations; {have} of {total} records have one.")
    print(f"{stray} have a word in the full caption that is not in the model input.")
    print(f"Full caption matches production: {marks['full']} of {marks['total']}.")
    print(f"Short name matches the docket name: {marks['short']} of {marks['total']}.")
    print(f"Auto-accepted this run: {accepted or 'nothing new'}")
    report(problems, pending)


if __name__ == "__main__":
    main()
