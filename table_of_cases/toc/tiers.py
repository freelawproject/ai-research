"""How far one version of an entry is from another.

Tiers, mildest first:
    same           letters and digits match (punctuation and spacing ignored)
    field          the text is right but sits in the wrong field, a list is
                   split at the wrong place (including the history split),
                   or a page-break flag is wrong
    ocr            look-alike characters swapped (918 for 913, Sce for See)
    hallucination  any other change: a word or citation added, dropped or
                   altered

The viewer has the same rules in viewer/ui/static/tiers.js. Both are checked
against tests/tier_cases.json, so change the two together.
"""

import re
from difflib import SequenceMatcher

from toc.schema import FLAG_FIELDS, TEXT_FIELDS

# Characters that are easily mistaken for one another in print.
LOOKALIKES = (
    "035689",
    "17",
    "1lIi",
    "ceo",
    "ae",
    "rt",
    "ft",
    "nu",
    "mn",
    "hb",
    "vy",
    "gq",
    "5S",
    "8B",
    "2Z",
    "0OoQD",
)
MAX_SWAPS = 2


def norm(value: str | None) -> str:
    """Letters and digits only: the form two values are compared in."""
    return re.sub(r"[^A-Za-z0-9]", "", value or "")


def words(value: str | None) -> list[str]:
    return [w for w in (norm(part) for part in (value or "").split()) if w]


def is_misread(word: str, target: str) -> bool:
    """True when `word` is `target` with one or two look-alike swaps."""
    if len(word) != len(target):
        return False
    swaps = 0
    for mine, theirs in zip(word, target, strict=True):
        if mine == theirs:
            continue
        if not any(mine in group and theirs in group for group in LOOKALIKES):
            return False
        swaps += 1
    return 0 < swaps <= MAX_SWAPS


def field_level(model_text: str | None, gold_text: str | None) -> str:
    """Tier for one piece of text: "same", "ocr" or "hallucination"."""
    if norm(model_text) == norm(gold_text):
        return "same"
    mine, theirs = words(model_text), words(gold_text)
    if len(mine) == len(theirs):
        # Same number of words: compare position by position.
        changed = [(a, b) for a, b in zip(mine, theirs, strict=True) if a != b]
        return "ocr" if all(is_misread(a, b) for a, b in changed) else "hallucination"
    level = "ocr"
    matcher = SequenceMatcher(None, mine, theirs, autojunk=False)
    for tag, m0, m1, g0, g1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        extra, wanted = mine[m0:m1], theirs[g0:g1]
        if len(extra) != len(wanted) or not all(
            is_misread(word, target) for word, target in zip(extra, wanted, strict=True)
        ):
            level = "hallucination"
    return level


def whole_text(entry: dict) -> str:
    """All the text of an entry in printed order, as one string."""
    parts = [entry["name"], entry["court"], " ".join(entry["citations"])]
    parts += [entry["history"], entry["topics"]]
    return " ".join(part or "" for part in parts)


def split_key(entry: dict) -> list:
    """The history split in comparable form (letters and digits only)."""
    return [
        [
            norm(item["action"]),
            norm(item["case_name"]),
            [norm(c) for c in item["citations"]],
        ]
        for item in entry.get("history_items") or []
    ]


def split_shape(entry: dict) -> list:
    """How the history is divided, ignoring how its text was read."""
    return [
        [len(words(item["action"])), bool(item["case_name"]), len(item["citations"])]
        for item in entry.get("history_items") or []
    ]


def entry_level(got: dict, want: dict, compare_split: bool = False) -> str:
    """Tier for one entry.

    The wording is judged on the entry as a whole, so text that only moved
    between fields counts as "field", not as invented or missing. With
    `compare_split`, a history divided into different items is also "field".
    """
    wording = field_level(whole_text(got), whole_text(want))
    if wording != "same":
        return wording
    pairs = [(got[f], want[f]) for f in TEXT_FIELDS]
    pairs.append((" ".join(got["citations"]), " ".join(want["citations"])))
    if any(field_level(mine, theirs) != "same" for mine, theirs in pairs):
        return "field"
    if len(got["citations"]) != len(want["citations"]):
        return "field"
    if compare_split and split_key(got) != split_key(want):
        return "field"
    if any(bool(got[f]) != bool(want[f]) for f in FLAG_FIELDS):
        return "field"
    return "same"
