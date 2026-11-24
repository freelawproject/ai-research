"""Export the accepted names as the gold set, one file per split.

    uv run python export_gold.py

Writes ../data/gold/validation.jsonl and ../data/gold/test.jsonl. A record is
exported once both of its name fields are accepted, by review or by
auto-acceptance; `full_accepted_by` and `short_accepted_by` say which.
"""

import json

import store

ACCEPTED_BY = {"reviewed": "human", "auto": "auto"}


def main() -> None:
    conn = store.connect()
    store.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    for split in store.SPLITS:
        rows = conn.execute(
            """
            SELECT r.cluster_id, r.court_id, r.jurisdiction, r.stratum, r.doc_type,
                   r.extracted_by_ocr, r.llm_input, r.docket_case_name,
                   f.value AS case_name_full, s.value AS case_name,
                   f.status AS full_status, s.status AS short_status,
                   f.flags AS full_flags, s.flags AS short_flags,
                   f.notes AS full_notes, s.notes AS short_notes
            FROM records r
            JOIN field_labels f ON f.cluster_id = r.cluster_id AND f.field = 'full'
            JOIN field_labels s ON s.cluster_id = r.cluster_id AND s.field = 'short'
            WHERE r.split = ?
              AND f.status IN ('reviewed', 'auto')
              AND s.status IN ('reviewed', 'auto')
            ORDER BY r.position
            """,
            (split,),
        ).fetchall()
        path = store.GOLD_DIR / f"{split}.jsonl"
        with open(path, "w") as out:
            for r in rows:
                item = dict(r)
                item["extracted_by_ocr"] = bool(item["extracted_by_ocr"])
                item["full_accepted_by"] = ACCEPTED_BY[item.pop("full_status")]
                item["short_accepted_by"] = ACCEPTED_BY[item.pop("short_status")]
                item["flags"] = sorted(
                    set(json.loads(item.pop("full_flags")))
                    | set(json.loads(item.pop("short_flags")))
                )
                item["notes"] = " ".join(
                    n for n in (item.pop("full_notes"), item.pop("short_notes")) if n
                )
                out.write(json.dumps(item, ensure_ascii=False) + "\n")
        total = conn.execute(
            "SELECT count(*) FROM records WHERE split = ?", (split,)
        ).fetchone()[0]
        print(f"{split}: {len(rows)} of {total} exported -> {path}")


if __name__ == "__main__":
    main()
