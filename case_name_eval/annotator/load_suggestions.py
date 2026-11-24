"""Load another model's outputs as alternative suggestions in the annotator.

    uv run python load_suggestions.py \
        --file ../data/prod_replay/gpt-4o-mini_prod-prompt.jsonl \
        --source "GPT-4o-mini replay"

Reads the JSON lines written by ../prod_replay/replay_prod.py. Each record gets
one suggestion row per source; re-loading the same source replaces its rows.
Human annotations and the default pre-annotations are not touched.
"""

import argparse
import json

import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()

    conn = store.connect()
    known = {r[0] for r in conn.execute("SELECT cluster_id FROM records")}
    loaded = skipped = 0
    with open(args.file) as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            if item["cluster_id"] not in known:
                skipped += 1
                continue
            note = ""
            if item.get("error"):
                note = "Call failed: " + item["error"].split(":")[0]
            conn.execute(
                """
                INSERT INTO suggestions VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cluster_id, source) DO UPDATE SET
                    case_name_full = excluded.case_name_full,
                    case_name = excluded.case_name, note = excluded.note,
                    created_at = excluded.created_at
                """,
                (
                    item["cluster_id"],
                    args.source,
                    item.get("saved_case_name_full") or "",
                    item.get("saved_case_name") or "",
                    note,
                    store.now(),
                ),
            )
            loaded += 1
    conn.commit()
    print(
        f"Loaded {loaded} suggestions as {args.source!r}; {skipped} not in the store."
    )


if __name__ == "__main__":
    main()
