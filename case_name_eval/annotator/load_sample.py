"""Load the sampled clusters into the store and assign the two splits.

    uv run python load_sample.py            # reads ../data/sample_2k.csv
    uv run python load_sample.py --replace  # swap in a re-pulled sample

Split: records are ordered by (stratum, hash of cluster id) and assigned
alternately, so each split gets half of every stratum and the result is the
same on every run of the same CSV.

Field labels are never touched. `--replace` refuses to drop a record that
already has one.
"""

import argparse
import csv
import hashlib
import sys

import store

SEED = "casename-eval-split-v1"


def stable_hash(cluster_id: str, salt: str) -> str:
    return hashlib.md5(f"{cluster_id}|{salt}".encode()).hexdigest()


def read_rows(path) -> list[dict]:
    csv.field_size_limit(sys.maxsize)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    ids = [r["cluster_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate cluster_id values in the sample CSV")
    return rows


def assign_splits(rows: list[dict]) -> None:
    """Add `split` and `position` to each row, in place."""
    by_stratum = sorted(
        rows, key=lambda r: (r["stratum"], stable_hash(r["cluster_id"], SEED))
    )
    for i, row in enumerate(by_stratum):
        row["split"] = store.SPLITS[i % 2]
    # Work order mixes strata, so any stretch of annotation covers all of them.
    for position, row in enumerate(
        sorted(rows, key=lambda r: stable_hash(r["cluster_id"], SEED + "|order"))
    ):
        row["position"] = position


def to_bool(value: str) -> int:
    return 1 if value in ("t", "true", "True", "1") else 0


def to_int(value: str) -> int | None:
    return int(value) if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", default=store.SAMPLE_CSV)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Remove stored records that are not in this CSV",
    )
    args = parser.parse_args()

    rows = read_rows(args.csv)
    assign_splits(rows)
    new_ids = {int(r["cluster_id"]) for r in rows}

    store.backup()
    conn = store.connect()
    old_ids = {r[0] for r in conn.execute("SELECT cluster_id FROM records")}
    stale = old_ids - new_ids
    if stale and not args.replace:
        raise SystemExit(
            f"{len(stale)} stored records are not in this CSV. "
            "Re-run with --replace to drop them."
        )
    if stale:
        marks = ",".join("?" * len(stale))
        annotated = conn.execute(
            f"SELECT count(*) FROM field_labels WHERE cluster_id IN ({marks})",
            list(stale),
        ).fetchone()[0]
        if annotated:
            raise SystemExit(
                f"{annotated} labels exist on the records to drop. Nothing was changed."
            )
        conn.execute(
            f"DELETE FROM preannotations WHERE cluster_id IN ({marks})",
            list(stale),
        )
        conn.execute(f"DELETE FROM records WHERE cluster_id IN ({marks})", list(stale))

    conn.executemany(
        """
        INSERT INTO records (
            cluster_id, opinion_id, docket_id, recap_document_id,
            court_id, jurisdiction, docket_number, nature_of_suit,
            date_created, extracted_by_ocr, stratum, in_quota,
            prod_case_name, prod_case_name_full, docket_case_name,
            pre_update_case_name, llm_input, plain_text,
            plain_text_length, split, position
        ) VALUES (
            :cluster_id, :opinion_id, :docket_id, :recap_document_id,
            :court_id, :jurisdiction, :docket_number, :nature_of_suit,
            :date_created, :extracted_by_ocr, :stratum, :in_quota,
            :prod_case_name, :prod_case_name_full, :docket_case_name,
            :pre_update_case_name, :llm_input, :plain_text,
            :plain_text_length, :split, :position
        )
        ON CONFLICT(cluster_id) DO UPDATE SET
            stratum = excluded.stratum, in_quota = excluded.in_quota,
            prod_case_name = excluded.prod_case_name,
            prod_case_name_full = excluded.prod_case_name_full,
            docket_case_name = excluded.docket_case_name,
            pre_update_case_name = excluded.pre_update_case_name,
            llm_input = excluded.llm_input, plain_text = excluded.plain_text,
            plain_text_length = excluded.plain_text_length,
            split = excluded.split, position = excluded.position
        """,
        [
            {
                "cluster_id": int(r["cluster_id"]),
                "opinion_id": to_int(r["opinion_id"]),
                "docket_id": to_int(r["docket_id"]),
                "recap_document_id": to_int(r["recap_document_id"]),
                "court_id": r["court_id"],
                "jurisdiction": r["jurisdiction"],
                "docket_number": r["docket_number"],
                "nature_of_suit": r["nature_of_suit"],
                "date_created": r["date_created"],
                "extracted_by_ocr": to_bool(r["extracted_by_ocr"]),
                "stratum": r["stratum"],
                "in_quota": to_bool(r["in_quota"]),
                "prod_case_name": r["current_case_name"],
                "prod_case_name_full": r["current_case_name_full"],
                "docket_case_name": r["docket_case_name"],
                "pre_update_case_name": r["pre_update_case_name"],
                "llm_input": r["llm_input"],
                "plain_text": r["plain_text"],
                "plain_text_length": to_int(r["plain_text_length"]),
                "split": r["split"],
                "position": r["position"],
            }
            for r in rows
        ],
    )
    conn.commit()

    print(f"Loaded {len(rows)} records into {store.DB_PATH}")
    for split, stratum, n in conn.execute(
        "SELECT split, stratum, count(*) FROM records GROUP BY 1, 2 ORDER BY 1, 2"
    ):
        print(f"  {split:<11}{stratum:<24}{n:>5}")


if __name__ == "__main__":
    main()
