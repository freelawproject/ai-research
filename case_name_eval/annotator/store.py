"""SQLite store shared by the annotator app and its scripts.

Tables, kept apart so machine output can never overwrite human work:

- records         the sampled clusters (from the replica CSV) and their split
- preannotations  the Opus values and their agreement marks, one per record
- suggestions     other model outputs offered as alternatives, by source
- review_holds    fields put back in the review queue: why, and the value saved
                  before. Never auto-accepted; removed when the field is saved
- field_labels    the accepted value of each name field, one row per
                  (record, field). `reviewed` and `skipped` rows are written
                  by the annotator app; `auto` rows are Opus values confirmed
                  by a second source (see auto_accept).
"""

import json
import os
import re
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(
    os.environ.get(
        "CASE_NAME_DATA_DIR", Path(__file__).resolve().parent.parent / "data"
    )
)
DB_PATH = DATA_DIR / "annotations.db"
BACKUP_DIR = DATA_DIR / "backups"
SAMPLE_CSV = DATA_DIR / "sample_2k.csv"
BATCH_DIR = DATA_DIR / "preannotate" / "batches"
OUTPUT_DIR = DATA_DIR / "preannotate" / "outputs"
GOLD_DIR = DATA_DIR / "gold"

MODEL_INPUT_CHARS = 2000
WIDE_WINDOW_CHARS = 8000  # used when the caption is missing or cut off at 2,000
WINDOW_DIR = DATA_DIR / "window_8k"
SPLITS = ("validation", "test")
FIELDS = ("full", "short")
FLAGS = {
    "no_caption": "No case name in the document",
    "caption_beyond_input": "Caption is not in the first 2,000 characters",
    "caption_cut_off": "Caption is cut off at 2,000 characters",
    "multiple_cases": "More than one case in the caption",
    "ocr_error": "Text contains OCR error",
    "garbled_text": "Text is garbled (not readable)",
    "unsure": "Unsure, revisit",
}
CAPTION_IN_INPUT = ("complete", "partial", "none")
CONFIDENCE = ("high", "medium", "low")
KEEP_BACKUPS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    cluster_id            INTEGER PRIMARY KEY,
    opinion_id            INTEGER,
    docket_id             INTEGER,
    recap_document_id     INTEGER,
    court_id              TEXT,
    jurisdiction          TEXT,
    docket_number         TEXT,
    nature_of_suit        TEXT,
    date_created          TEXT,
    extracted_by_ocr      INTEGER,
    stratum               TEXT,
    in_quota              INTEGER,
    prod_case_name        TEXT,
    prod_case_name_full   TEXT,
    docket_case_name      TEXT,
    pre_update_case_name  TEXT,
    llm_input             TEXT,
    plain_text            TEXT,
    plain_text_length     INTEGER,
    split                 TEXT NOT NULL,
    position              INTEGER NOT NULL,
    doc_type              TEXT
);
CREATE TABLE IF NOT EXISTS preannotations (
    cluster_id        INTEGER PRIMARY KEY REFERENCES records(cluster_id),
    case_name_full    TEXT NOT NULL,
    case_name         TEXT NOT NULL,
    caption_in_input  TEXT NOT NULL,
    confidence        TEXT NOT NULL,
    notes             TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    full_agrees       INTEGER,          -- full caption matches production
    short_agrees      INTEGER,          -- short name matches the docket name
    window            INTEGER NOT NULL DEFAULT 2000,  -- characters the value was read from
    caption_at_2k     TEXT              -- caption_in_input at 2,000, kept when re-read wider
);
CREATE TABLE IF NOT EXISTS suggestions (
    cluster_id      INTEGER NOT NULL REFERENCES records(cluster_id),
    source          TEXT NOT NULL,
    case_name_full  TEXT NOT NULL DEFAULT '',
    case_name       TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    PRIMARY KEY (cluster_id, source)
);
CREATE TABLE IF NOT EXISTS review_holds (
    cluster_id  INTEGER NOT NULL REFERENCES records(cluster_id),
    field       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    previous_value TEXT,
    PRIMARY KEY (cluster_id, field)
);
CREATE TABLE IF NOT EXISTS field_labels (
    cluster_id  INTEGER NOT NULL REFERENCES records(cluster_id),
    field       TEXT NOT NULL,          -- 'full' or 'short'
    value       TEXT NOT NULL,
    status      TEXT NOT NULL,          -- 'reviewed', 'auto' or 'skipped'
    flags       TEXT NOT NULL DEFAULT '[]',
    notes       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cluster_id, field)
);

CREATE INDEX IF NOT EXISTS records_position ON records(position);
"""


def connect() -> sqlite3.Connection:
    """Open the store, creating the file and tables if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """A connection that is closed when the block ends. Use for one request or one script."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


IGNORED_WORDS = {"v", "in", "re", "et", "al", "and", "the", "of", "ex", "rel", "vs"}


def words(text: str) -> set[str]:
    """The lowercase words of a text, without punctuation."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def comparable(name: str) -> str:
    """A name reduced to its letters and digits, for agreement checks.

    Ignores capitalization, spacing and punctuation, so `In re: AMBER SAENZ`
    and `In re Amber Saenz` compare equal.
    """
    return "".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def names_agree(first: str, second: str) -> bool:
    """True when both names exist and are the same once normalized."""
    return bool(comparable(first)) and comparable(first) == comparable(second)


def refresh_agreement(conn: sqlite3.Connection) -> dict:
    """Recompute the two agreement marks on every pre-annotation.

    full_agrees:  the full name matches the full name in production.
    short_agrees: the short name matches the original docket name.
    """
    rows = conn.execute(
        "SELECT p.cluster_id, p.case_name_full, p.case_name, "
        "r.prod_case_name_full, r.docket_case_name "
        "FROM preannotations p JOIN records r USING (cluster_id)"
    ).fetchall()
    counts = {"full": 0, "short": 0, "both": 0, "total": len(rows)}
    for row in rows:
        full = names_agree(row["case_name_full"], row["prod_case_name_full"])
        short = names_agree(row["case_name"], row["docket_case_name"])
        counts["full"] += full
        counts["short"] += short
        counts["both"] += full and short
        conn.execute(
            "UPDATE preannotations SET full_agrees = ?, short_agrees = ? "
            "WHERE cluster_id = ?",
            (int(full), int(short), row["cluster_id"]),
        )
    conn.commit()
    return counts


SECOND_MODEL = "GPT-6.2 Sol"
WIDE_REPLAY = "GPT-4o-mini (8K window)"
# Sources that can confirm an Opus value, per field, besides the built-in
# comparison with production (full) and with the docket name (short).
CONFIRMING_SOURCES = {
    "full": (SECOND_MODEL, WIDE_REPLAY),
    "short": (SECOND_MODEL,),
}


def auto_accept(conn: sqlite3.Connection) -> dict:
    """Accept each Opus value that a second source confirms.

    full:  matches the full name in production, or a confirming source.
    short: matches the original docket name, or a confirming source.

    Writes a field label with status 'auto' and the confirming source in its
    notes. A value read from the wider window also carries the flag that says
    why the 2,000-character input was not enough. A field that already has a
    label is left alone, so human work is never replaced.
    """
    # Fields that are labeled, or held for a person to confirm, are skipped.
    labeled = {
        (r["cluster_id"], r["field"])
        for r in conn.execute(
            "SELECT cluster_id, field FROM field_labels "
            "UNION SELECT cluster_id, field FROM review_holds"
        )
    }
    others = {}
    for r in conn.execute(
        "SELECT cluster_id, source, case_name_full, case_name FROM suggestions"
    ):
        others[(r["cluster_id"], r["source"])] = {
            "full": r["case_name_full"],
            "short": r["case_name"],
        }
    added = {}
    for p in conn.execute("SELECT * FROM preannotations").fetchall():
        wide = p["window"] == WIDE_WINDOW_CHARS
        flags = []
        if wide:
            flags = [
                "caption_beyond_input"
                if p["caption_at_2k"] == "none"
                else "caption_cut_off"
            ]
        for field, column, mark, builtin in (
            ("full", "case_name_full", "full_agrees", "production"),
            ("short", "case_name", "short_agrees", "docket name"),
        ):
            if (p["cluster_id"], field) in labeled:
                continue
            confirmed_by = builtin if p[mark] else None
            for source in CONFIRMING_SOURCES[field]:
                if confirmed_by:
                    break
                other = others.get((p["cluster_id"], source))
                if other and names_agree(p[column], other[field]):
                    confirmed_by = source
            if not confirmed_by:
                continue
            note = f"Opus = {confirmed_by}" + (
                ", 8,000-character window" if wide else ""
            )
            conn.execute(
                "INSERT INTO field_labels VALUES (?, ?, ?, 'auto', ?, ?, ?)",
                (p["cluster_id"], field, p[column], json.dumps(flags), note, now()),
            )
            added[f"{field}: {confirmed_by}"] = (
                added.get(f"{field}: {confirmed_by}", 0) + 1
            )
    conn.commit()
    return added


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def backup() -> Path | None:
    """Copy the store to the backup folder. Skips an empty or missing store."""
    if not DB_PATH.exists():
        return None
    with session() as conn:
        n = conn.execute("SELECT count(*) FROM field_labels").fetchone()[0]
    if n == 0:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"annotations-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(DB_PATH, target)
    for old in sorted(BACKUP_DIR.glob("annotations-*.db"))[:-KEEP_BACKUPS]:
        old.unlink()
    return target
