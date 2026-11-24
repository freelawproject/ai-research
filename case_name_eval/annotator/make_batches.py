"""Write pre-annotation batch files, one per sub-agent run.

    uv run python make_batches.py               # 50 records per batch
    uv run python make_batches.py --size 40

Each batch holds only the cluster id and the first 2,000 characters of the
text: the same input the production model gets. Docket names and production
names are left out, so the pre-annotation is independent of both.

Records that already have a pre-annotation or a reviewed label are skipped,
so re-running after a partial run produces batches for the remainder only.
"""

import argparse
import json
from datetime import datetime

import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=50)
    args = parser.parse_args()

    conn = store.connect()
    rows = conn.execute(
        """
        SELECT r.cluster_id, r.llm_input
        FROM records r
        LEFT JOIN preannotations p USING (cluster_id)
        WHERE p.cluster_id IS NULL
          AND r.cluster_id NOT IN (
              SELECT cluster_id FROM field_labels WHERE status = 'reviewed'
          )
        ORDER BY r.position
        """
    ).fetchall()

    store.BATCH_DIR.mkdir(parents=True, exist_ok=True)
    store.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in store.BATCH_DIR.glob("batch_*.json"):
        old.unlink()
    # Batch numbers restart, so earlier outputs no longer line up with them.
    stale = sorted(store.OUTPUT_DIR.glob("batch_*.json"))
    if stale:
        archive = store.OUTPUT_DIR / f"archive-{datetime.now():%Y%m%d-%H%M%S}"
        archive.mkdir()
        for path in stale:
            path.rename(archive / path.name)
        print(f"Moved {len(stale)} earlier output files to {archive}")

    n_batches = 0
    for start in range(0, len(rows), args.size):
        n_batches += 1
        chunk = rows[start : start + args.size]
        path = store.BATCH_DIR / f"batch_{n_batches:03d}.json"
        path.write_text(
            json.dumps(
                [
                    {"cluster_id": r["cluster_id"], "text": r["llm_input"]}
                    for r in chunk
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
    print(f"{len(rows)} records -> {n_batches} batches in {store.BATCH_DIR}")
    print(f"Outputs go to {store.OUTPUT_DIR}/batch_NNN.json")


if __name__ == "__main__":
    main()
