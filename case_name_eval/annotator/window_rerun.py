"""Re-read, with a wider window, the records whose caption is missing or cut off at 2,000 characters.

    uv run python window_rerun.py batches    # pick the records, write Opus batch files
    # one Opus sub-agent run per batch, as for the first pass
    uv run python window_rerun.py collect    # check the outputs and make them the Opus values

`batches` picks every record that Opus marked `none` or `partial` at 2,000
characters and that has more text to read, saves the ids to
../data/window_8k/ids.json (gpt_batch.py and the production replay read the
same list), and writes batches holding the first 8,000 characters.

`collect` replaces the Opus value of those records with the 8,000-character
one. The 2,000-character values are kept in `preannotations_2k` and
`suggestions_2k`. Auto-accepted labels of these records are dropped and
decided again from the new values; labels saved by a person are not touched.
"""

import argparse
import json

import store
from collect_preannotations import read_outputs, report

BATCH_DIR = store.WINDOW_DIR / "batches"
OUTPUT_DIR = store.WINDOW_DIR / "outputs"
IDS = store.WINDOW_DIR / "ids.json"
BATCH_SIZE = 25


def cmd_batches() -> None:
    with store.session() as conn:
        rows = conn.execute(
            """
            SELECT r.cluster_id, r.plain_text
            FROM records r JOIN preannotations p USING (cluster_id)
            WHERE p.window = ? AND p.caption_in_input IN ('none', 'partial')
              AND r.plain_text_length > ?
            ORDER BY r.position
            """,
            (store.MODEL_INPUT_CHARS, store.MODEL_INPUT_CHARS),
        ).fetchall()
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IDS.write_text(json.dumps([r["cluster_id"] for r in rows]))
    batches = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batches += 1
        chunk = [
            {
                "cluster_id": r["cluster_id"],
                "text": r["plain_text"][: store.WIDE_WINDOW_CHARS],
            }
            for r in rows[start : start + BATCH_SIZE]
        ]
        path = BATCH_DIR / f"batch_{batches:03d}.json"
        path.write_text(json.dumps(chunk, ensure_ascii=False, indent=1))
    print(f"{len(rows)} records -> {batches} batches in {BATCH_DIR}")


def replace_value(conn, item: dict) -> bool:
    """Make the 8,000-character answer the Opus value of one record.

    Returns False when the record was already replaced by an earlier run.
    """
    cluster_id = item["cluster_id"]
    old = conn.execute(
        "SELECT window, caption_in_input FROM preannotations WHERE cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if old["window"] == store.WIDE_WINDOW_CHARS:
        return False
    conn.execute(
        "INSERT INTO preannotations_2k SELECT * FROM preannotations WHERE cluster_id = ?",
        (cluster_id,),
    )
    # The second model's 2,000-character answer no longer compares like with like.
    second = (cluster_id, store.SECOND_MODEL)
    conn.execute(
        "INSERT INTO suggestions_2k SELECT * FROM suggestions "
        "WHERE cluster_id = ? AND source = ?",
        second,
    )
    conn.execute(
        "DELETE FROM suggestions WHERE cluster_id = ? AND source = ? "
        "AND note NOT LIKE '8,000-character window.%'",
        second,
    )
    conn.execute(
        """
        UPDATE preannotations SET
            case_name_full = ?, case_name = ?, caption_in_input = ?,
            confidence = ?, notes = ?, created_at = ?,
            window = ?, caption_at_2k = ?
        WHERE cluster_id = ?
        """,
        (
            item["case_name_full"].strip(),
            item["case_name"].strip(),
            item["caption_in_input"],
            item["confidence"],
            str(item.get("notes", "")).strip(),
            store.now(),
            store.WIDE_WINDOW_CHARS,
            old["caption_in_input"],
            cluster_id,
        ),
    )
    conn.execute(
        "DELETE FROM field_labels WHERE cluster_id = ? AND status = 'auto'",
        (cluster_id,),
    )
    return True


def cmd_collect() -> None:
    items, problems, pending = read_outputs(BATCH_DIR, OUTPUT_DIR)
    with store.session() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS preannotations_2k AS
                SELECT * FROM preannotations WHERE 0;
            CREATE TABLE IF NOT EXISTS suggestions_2k AS
                SELECT * FROM suggestions WHERE 0;
            """
        )
        replaced = sum(replace_value(conn, item) for item in items)
        conn.commit()
        marks = store.refresh_agreement(conn)
        accepted = store.auto_accept(conn)
        by_status = conn.execute(
            "SELECT caption_in_input, count(*) FROM preannotations "
            "WHERE window = ? GROUP BY 1",
            (store.WIDE_WINDOW_CHARS,),
        ).fetchall()

    print(
        f"Replaced the Opus value of {replaced} records with the 8,000-character one."
    )
    for status, n in by_status:
        print(f"  caption at 8,000 characters: {status:<9}{n}")
    print(f"Agreement marks: {marks}")
    print(f"Auto-accepted: {accepted or 'nothing new'}")
    report(problems, pending)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["batches", "collect"])
    args = parser.parse_args()
    {"batches": cmd_batches, "collect": cmd_collect}[args.command]()


if __name__ == "__main__":
    main()
