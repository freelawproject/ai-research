"""Stream primitives shared by both resolution variants.

Nothing here knows how a dispute is RESOLVED — that is the variants'
business (`tiebreak.py` for the LightOn crop tiebreak, `vote.py` for the
direct three-way vote). This module holds only what both need: the main
stream and its per-item lookups, the key/text accessors, the alignment
of two key streams, and the empty shell of a dispute.
"""

from __future__ import annotations

import difflib


def keys(tokens: list[dict]) -> list[str]:
    return [t["key"] for t in tokens]


def text(tokens: list[dict], i1: int, i2: int, field: str) -> str:
    return " ".join(t[field] for t in tokens[i1:i2])


def opcodes(
    a: list[str], b: list[str]
) -> list[tuple[str, int, int, int, int]]:
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return [(tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes()]


def project(
    ops: list[tuple[str, int, int, int, int]], i1: int, i2: int
) -> tuple[int, int]:
    """Map a main-stream interval [i1, i2) onto the other stream of one
    alignment: the union of the b-ranges of every opcode the interval
    overlaps (equal opcodes clipped position-for-position; a zero-width
    insertion at the interval edge is included — merged intervals own
    their touching insertions)."""
    j1 = j2 = None
    for tag, a1, a2, b1, b2 in ops:
        if tag == "equal":
            lo, hi = max(a1, i1), min(a2, i2)
            if lo >= hi:
                continue
            s, e = b1 + (lo - a1), b1 + (hi - a1)
        elif a1 == a2:  # insertion (zero-width on the main side)
            if not (i1 <= a1 <= i2):
                continue
            s, e = b1, b2
        else:
            if not (a1 < i2 and i1 < a2):
                continue
            s, e = b1, b2
        j1 = s if j1 is None else min(j1, s)
        j2 = e if j2 is None else max(j2, e)
    if j1 is None or j2 is None:  # zero-width interval in an equal run
        for tag, a1, a2, b1, b2 in ops:
            if tag == "equal" and a1 <= i1 <= a2:
                p = b1 + (i1 - a1)
                return (p, p)
        return (0, 0)
    return (j1, j2)


def dispute_blocks(main_tokens: list[dict], i1: int, i2: int) -> list[int]:
    """The main-engine items a dispute lives in, in stream order: the
    items of its tokens plus the second fragment of any wrap-joined
    token (a wrapped dispute crops BOTH bboxes). An insertion (empty
    main side) anchors to the preceding token's item."""
    ids: list[int] = []
    for t in main_tokens[i1:i2]:
        for item in (t["item"], t["wrap"][0] if t.get("wrap") else None):
            if item is not None and item not in ids:
                ids.append(item)
    if not ids and main_tokens:
        anchor = main_tokens[i1 - 1] if i1 > 0 else main_tokens[0]
        ids.append(anchor["item"])
    return ids


class MainStream:
    """The main stream plus its per-item lookups (roles from the
    reconstruction, raw text for the gate, bboxes for crops)."""

    def __init__(
        self,
        tokens: list[dict],
        recon_items: list[dict],
        main_blocks: list[dict],
    ) -> None:
        self.tokens = tokens
        self.keys = keys(tokens)
        self.role_of = {
            it["id"]: it.get("role", "content") for it in recon_items
        }
        self.text_of = {it["id"]: it.get("text", "") for it in recon_items}
        self.bbox_of: dict[int, list[int]] = {
            b["id"]: b["bbox"] for b in main_blocks if b.get("bbox")
        }
        # token indices per item, in stream order (anchor context for
        # the crop read lives INSIDE the cropped blocks)
        self.idx_of_item: dict[int, list[int]] = {}
        for i, t in enumerate(tokens):
            self.idx_of_item.setdefault(t["item"], []).append(i)

    def block_token_indices(self, blocks: list[int]) -> list[int]:
        out: list[int] = []
        for b in blocks:
            out += self.idx_of_item.get(b, [])
        return sorted(out)


def base_dispute(ms: MainStream, i1: int, i2: int) -> dict:
    """The dispute shell: where it sits on the main stream, which items
    it lives in, and the main engine's own reading of it."""
    blocks = dispute_blocks(ms.tokens, i1, i2)
    roles = []
    for b in blocks:
        r = ms.role_of.get(b, "content")
        if r not in roles:
            roles.append(r)
    return {
        "main_span": [i1, i2],
        "blocks": blocks,
        "roles": roles,
        "reads": {"main": text(ms.tokens, i1, i2, "key")},
        "displays": {"main": text(ms.tokens, i1, i2, "display")},
    }
