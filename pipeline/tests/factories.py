"""Fixtures shared by the compare-stage tests (both variants)."""

from __future__ import annotations

from collections.abc import Sequence

BBOX = {0: [10, 10, 200, 60], 1: [10, 70, 200, 120], 2: [10, 130, 200, 180]}


def toks(text: str, item: int = 0, **extra: object) -> list[dict]:
    """One token per whitespace word (keys == display). Spans are the
    words' real char offsets, like the tokenizer's — whitespace-
    separated words never touch, so none of them GLUE at assembly."""
    out = []
    pos = 0
    for w in text.split():
        start = text.index(w, pos)
        pos = start + len(w)
        t: dict = {
            "display": w,
            "key": w,
            "item": item,
            "span": [start, pos],
        }
        t.update(extra)
        out.append(t)
    return out


def norm_rec(main: list[dict], *supps: tuple[str, str, list[dict]]) -> dict:
    return {
        "main": {"engine": "dots", "tokens": main},
        "supplementals": [
            {"engine": e, "unit": u, "tokens": t} for e, u, t in supps
        ],
    }


def recon_rec(items: list[tuple[int, str, str]]) -> dict:
    return {
        "main": {
            "items": [
                {"id": i, "role": role, "text": text, "html": text}
                for i, role, text in items
            ]
        }
    }


def blocks_for(ids: Sequence[int]) -> list[dict]:
    return [{"id": i, "bbox": BBOX[i]} for i in ids]
