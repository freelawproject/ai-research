"""The eval-set file for one page: its shape and how it is built.

A row is one entry position on the page. It is accepted automatically when
the two models agree, or holds the reviewer's choice ("picked" one model's
version, or "edited" it), or is still "open".
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from toc.schema import Entry

Level = Literal["same", "cosmetic", "field", "ocr", "hallucination"]
LEVELS = ("same", "cosmetic", "field", "ocr", "hallucination")
REVIEWER_STATUSES = ("picked", "edited")


class Judgment(BaseModel):
    """How far one model's version is from the accepted entry."""

    level: Level  # the reviewer's tier (same as `suggested` unless changed)
    suggested: Level  # the tier the rules proposed


class EvalRow(BaseModel):
    status: Literal["auto", "picked", "edited", "open"]
    source: str | None
    entry: Entry | None
    judgments: dict[str, Judgment] = {}


class EvalPage(BaseModel):
    engines: list[str]
    rows: list[EvalRow]


class EvalConflict(ValueError):
    """The save would silently discard choices the reviewer already made."""


def build_record(page: int, body: EvalPage, existing: dict | None) -> dict:
    """Turn the reviewer's rows for one page into the file to store.

    Raises ValueError for an accepted row without an entry, and EvalConflict
    when the save would drop more than one earlier choice.
    """
    if any(row.status != "open" and row.entry is None for row in body.rows):
        raise ValueError("An accepted row needs an entry.")
    if existing:
        had = sum(r["status"] in REVIEWER_STATUSES for r in existing["rows"])
        has = sum(r.status in REVIEWER_STATUSES for r in body.rows)
        # One undo at a time is normal; losing several choices is not.
        if has < had - 1:
            raise EvalConflict(
                f"Not saved: this would drop {had - has} of your choices "
                f"on page {page}. Reload the page."
            )
    accepted = [row for row in body.rows if row.status != "open"]
    open_rows = len(body.rows) - len(accepted)
    return {
        "page": page,
        "engines": body.engines,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": len(body.rows),
        "open": open_rows,
        "complete": open_rows == 0,
        "levels": {
            engine: {
                level: sum(
                    engine in row.judgments and row.judgments[engine].level == level
                    for row in accepted
                )
                for level in LEVELS
            }
            for engine in body.engines
        },
        "entries": [row.entry.model_dump() for row in accepted if row.entry],
        "rows": [row.model_dump() for row in body.rows],
    }
