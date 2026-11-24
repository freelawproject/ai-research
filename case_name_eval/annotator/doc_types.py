"""Assign a document type to every record, from text rules.

    uv run python doc_types.py

Reads the first 8,000 characters of each document and writes `records.doc_type`.
The rules look for the words a document uses to name itself ("Dear Judge",
"STANDING ORDER", "REPORT AND RECOMMENDATION", ...). The first rule that
matches wins. This is a first pass: the rules have not been checked against
hand labels.
"""

import re
from collections import Counter

import store

TYPES = (
    "letter to the court",
    "letter from the court",
    "standing order",
    "minutes",
    "report and recommendation",
    "opinion",
    "judgment",
    "order",
    "other",
)

LETTER = re.compile(
    r"\bDEAR (JUDGE|MAGISTRATE|CHIEF|HON|COUNSEL|MR|MS|PARTIES|SIR|MADAM|CLERK)"
)
LETTER_HEADING = re.compile(
    r"LETTER TO (ALL )?(COUNSEL|THE PARTIES)|LETTER (ORDER|OPINION)|MEMO(RANDUM)? ENDORSE"
)
FROM_COURT = re.compile(
    r"DEAR (COUNSEL|PARTIES|MR|MS)|LETTER TO (ALL )?(COUNSEL|THE PARTIES)|LETTER (ORDER|OPINION)"
)
HEADINGS = (
    (
        "standing order",
        re.compile(r"STANDING ORDER|GENERAL ORDER|INDIVIDUAL (RULES|PRACTICES)"),
    ),
    (
        "minutes",
        re.compile(r"CIVIL MINUTES|CRIMINAL MINUTES|MINUTE (ORDER|ENTRY)|MINUTES OF"),
    ),
    (
        "report and recommendation",
        re.compile(
            r"REPORT (AND|&) RECOMMENDATION|FINDINGS AND RECOMMENDATION|RECOMMENDED DISPOSITION"
        ),
    ),
    (
        "opinion",
        re.compile(
            r"MEMORANDUM OPINION|OPINION (AND|&) ORDER|MEMORANDUM (AND|&) ORDER|"
            r"MEMORANDUM (OF )?DECISION|DECISION AND ORDER|FINDINGS OF FACT|\bOPINION\b"
        ),
    ),
)
JUDGMENT = re.compile(r"\bJUDGMENT\b")
NOT_A_JUDGMENT = re.compile(r"SUMMARY JUDGMENT|MOTION FOR .{0,40}JUDGMENT")
ORDER = re.compile(r"\bORDER\b")


def doc_type(text: str) -> str:
    """The document type, from the headings in the start of the text."""
    start = re.sub(r"\s+", " ", text[: store.WIDE_WINDOW_CHARS].upper())
    top = re.sub(r"\s+", " ", text[:3000].upper())
    if LETTER.search(start) or LETTER_HEADING.search(top):
        return (
            "letter from the court"
            if FROM_COURT.search(start)
            else "letter to the court"
        )
    for name, pattern in HEADINGS:
        if pattern.search(top):
            return name
    if JUDGMENT.search(top) and not NOT_A_JUDGMENT.search(top):
        return "judgment"
    if ORDER.search(top):
        return "order"
    return "other"


def main() -> None:
    conn = store.connect()
    counts = Counter()
    for row in conn.execute("SELECT cluster_id, plain_text FROM records").fetchall():
        kind = doc_type(row["plain_text"])
        counts[kind] += 1
        conn.execute(
            "UPDATE records SET doc_type = ? WHERE cluster_id = ?",
            (kind, row["cluster_id"]),
        )
    conn.commit()
    for kind in TYPES:
        print(f"{kind:<28}{counts[kind]:>6}")


if __name__ == "__main__":
    main()
