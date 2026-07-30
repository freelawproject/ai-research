"""The final read of a region: what the engines agree on.

Two levels, because most regions never need the second one. Over the
unredacted corpus, 54% of regions come back IDENTICAL from all three
engines and another 27% from two of three — those resolve whole, and
the winner's HTML carries through with its styling intact.

The remaining ~18% differ everywhere, almost always by a character or
two in a long paragraph. Picking one engine for those would quietly
hand ~1 region in 6 to whichever engine happened to be first, so they
are voted WORD BY WORD instead: the engines are aligned against a base
read, each position takes the reading a majority share, and a position
with no majority keeps the base's word and is marked low-confidence.
Word voting cannot carry styling, so those regions render as marked
plain text — visibly different from a region that resolved whole.

`PRIORITY` breaks every tie the vote cannot: the same ranking the route
pipeline uses for its main engine, so the two surfaces agree on who
speaks when nobody agrees.
"""

from __future__ import annotations

import html
from collections import Counter
from difflib import SequenceMatcher

# Who to believe when the vote is tied — a fixed ranking, so the same
# region always resolves the same way.
PRIORITY = ("dots", "mistral", "surya")

UNANIMOUS = "unanimous"
MAJORITY = "majority"
VOTED = "voted"
SINGLE = "single"


def _rank(engine: str) -> int:
    return PRIORITY.index(engine) if engine in PRIORITY else len(PRIORITY)


def _base_engine(engines: dict[str, dict]) -> str:
    return min(engines, key=_rank)


def _marked(word: str) -> str:
    return f'<mark class="low-confidence">{html.escape(word)}</mark>'


def _candidates(
    base: list[str], other: list[str]
) -> tuple[dict[int, list[str]], dict[int, list[list[str]]]]:
    """One other engine's reading of each base word, plus the runs it
    has that the base does not.

    Substitutions map position-for-position when the two spans are the
    same length; otherwise the whole span is that engine's reading of
    the base's first position, which keeps a 2-vs-1 word split from
    silently dropping words.
    """
    at: dict[int, list[str]] = {}
    inserted: dict[int, list[list[str]]] = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        None, base, other, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                at.setdefault(k, []).append(base[k])
        elif tag == "replace":
            if i2 - i1 == j2 - j1:
                for offset in range(i2 - i1):
                    at.setdefault(i1 + offset, []).append(other[j1 + offset])
            else:
                at.setdefault(i1, []).append(" ".join(other[j1:j2]))
                for k in range(i1 + 1, i2):
                    at.setdefault(k, []).append("")
        elif tag == "delete":
            for k in range(i1, i2):
                at.setdefault(k, []).append("")
        elif tag == "insert":
            inserted.setdefault(i1, []).append(other[j1:j2])
    return at, inserted


def vote_words(
    base: list[str], others: list[list[str]]
) -> tuple[list[str], int]:
    """Majority word at each position. Returns (html fragments, number
    of positions with no majority)."""
    votes: list[dict[int, list[str]]] = []
    inserts: list[dict[int, list[list[str]]]] = []
    for other in others:
        at, ins = _candidates(base, other)
        votes.append(at)
        inserts.append(ins)

    # a strict majority of ALL len(others) + 1 readings, the base's own
    # included — the same quorum as the whole-region vote, so two
    # engines can never settle a word they disagree on
    quorum = (len(others) + 3) // 2
    out: list[str] = []
    disputed = 0
    for position in range(len(base) + 1):
        # a run the other engines have here, and the base does not
        runs = [tuple(run) for ins in inserts for run in ins.get(position, [])]
        for run, n in Counter(runs).items():
            if n >= quorum:
                out.append(" ".join(_marked(w) for w in run))
        if position == len(base):
            break
        # one entry per other engine — opcodes partition the base range,
        # so each contributes at most one reading of this position
        readings = [base[position]] + [
            word for v in votes for word in v.get(position, [])
        ]
        winner, count = Counter(readings).most_common(1)[0]
        if count >= quorum:
            if winner:  # "" = the majority dropped this word
                out.append(html.escape(winner))
        else:
            disputed += 1
            out.append(_marked(base[position]))
    return out, disputed


def resolve(engines: dict[str, dict]) -> dict:
    """The final read of one aligned region.

    `engines` is the group's per-engine merge ({text, html, ...}).
    Returns {agreement, source, agreeing, html, text, n_low_confidence}.
    """
    present = sorted(engines, key=_rank)
    base = _base_engine(engines)
    if len(present) == 1:
        merged = engines[base]
        return {
            "agreement": SINGLE,
            "source": base,
            "agreeing": [base],
            "html": merged["html"],
            "text": merged["text"],
            "n_low_confidence": 0,
        }

    counts = Counter(engines[e]["text"] for e in present)
    winner, votes = counts.most_common(1)[0]
    quorum = (len(present) + 2) // 2
    if votes >= quorum:
        # resolved whole: keep the winner's HTML, styling and all
        agreeing = [e for e in present if engines[e]["text"] == winner]
        return {
            "agreement": UNANIMOUS if votes == len(present) else MAJORITY,
            "source": agreeing[0],
            "agreeing": agreeing,
            "html": engines[agreeing[0]]["html"],
            "text": winner,
            "n_low_confidence": 0,
        }

    fragments, disputed = vote_words(
        engines[base]["text"].split(),
        [engines[e]["text"].split() for e in present if e != base],
    )
    joined = " ".join(f for f in fragments if f)
    return {
        "agreement": VOTED,
        "source": base,
        "agreeing": [],
        "html": joined,
        "text": html.unescape(_strip_marks(joined)),
        "n_low_confidence": disputed,
    }


def _strip_marks(fragment: str) -> str:
    return fragment.replace('<mark class="low-confidence">', "").replace(
        "</mark>", ""
    )
