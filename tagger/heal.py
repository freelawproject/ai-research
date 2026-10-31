"""Prediction repairs applied after decoding.

``heal_disposition``: when a decoder returns two or more disposition
spans inside one ``<p>`` block, the sentence was returned in pieces
(the issue-#458 fragment shape). A dispositive block is dispositive as
a whole, so the pieces are replaced by ONE disposition span over the
block's inner text; any other span that sits entirely inside that block
is dropped with it. Blocks with a single disposition span are left
alone. Pure function over (text, spans); no model involved.
"""

from __future__ import annotations

import re

BLOCK_RE = re.compile(r"<p>(.*?)</p>", re.S)
INNER_TAG_RE = re.compile(r"^(?:\s|<[^>]+>)+|(?:\s|<[^>]+>)+$")


def _inner(text: str, a: int, b: int) -> tuple[int, int]:
    """Trim whitespace and inline markup off the block's edges."""
    seg = text[a:b]
    m = re.match(r"(?:\s|<[^>]+>)*", seg)
    lead = m.end() if m else 0
    m2 = re.search(r"(?:\s|<[^>]+>)*$", seg)
    trail = len(seg) - m2.start() if m2 else 0
    return a + lead, max(a + lead, b - trail)


def heal_disposition(text: str, spans: list[dict], label: str = "disposition",
                     min_pieces: int = 2) -> tuple[list[dict], list[dict]]:
    """Return (healed spans, a log row per healed block with the pieces
    replaced and the other spans dropped)."""
    out = list(spans)
    log: list[dict] = []
    for m in BLOCK_RE.finditer(text):
        a, b = m.start(1), m.end(1)
        pieces = [s for s in out if s["label"] == label and a <= s["start"] and s["end"] <= b]
        if len(pieces) < min_pieces:
            continue
        others = [s for s in out if s["label"] != label and a <= s["start"] and s["end"] <= b]
        ia, ib = _inner(text, a, b)
        out = [s for s in out if s not in pieces and s not in others]
        out.append({"label": label, "start": ia, "end": ib})
        log.append({"block": text[ia:ib], "pieces": [text[s["start"]:s["end"]] for s in pieces],
                    "dropped": [(s["label"], text[s["start"]:s["end"]]) for s in others]})
    out.sort(key=lambda s: (s["start"], s["end"]))
    return out, log
