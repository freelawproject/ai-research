"""Word-level labels and decoding for the block tagger.

The tokenizer is byte-level BPE, so a word is often several tokens
(``TR|UST``, ``Color|ado``). The shipped model was trained with a BIO
label on every token and decoded token by token, so one wrong piece
opens or cuts a span inside a word. This module moves the unit to the
word, on both sides of the model:

- :func:`materialize_word_labels` puts each word's BIO label on its
  first token and masks (−100) every other token, the standard
  ``label_all_tokens=False`` setting. The model is never asked to label
  a continuation piece.
- :func:`decode_word_spans` reads one prediction per word, from that
  same first token, and extends every span to whole words. A span then
  cannot start or end inside a word, by construction.

A word is a maximal run of characters that are neither whitespace nor
inside a markup tag (``<p>``, ``<em>``, ...). Punctuation glued to a
word belongs to it (``Jr.;`` is one word); punctuation after a closing
tag (``</em>,``) is a word of its own. The same rule serves both
functions, so what training supervises is what decoding reads.

No tokenizer here: both functions take the ``offset_mapping`` the
tokenizer returns, one ``(start, end)`` per token, with ``(0, 0)`` for
special tokens.
"""

from __future__ import annotations

import re

MARKUP_RE = re.compile(r"</?(?:p|blockquote|footnotes|em|sup)>")
MASKED = -100
#: A page number glued to the word after it in the big-set seeds: an
#: OCR mark (``↓47ORDER``) or bare digits before a bracket
#: (``1007(a) Pre-Miranda``). The number is a word of its own, so a
#: span can start at ``ORDER`` or ``(a)``.
PAGE_MARK_RE = re.compile(r"^(?:↓\d+|\d{2,4}(?=[(\[]))")
#: What may lie between two spans of one label for the model to see
#: them as one: spaces, edge punctuation, inline markup. Never a block
#: boundary (``\n``).
_INLINE_MARKUP_RE = re.compile(r"</?(?:em|sup)>")
_EDGE_GAP_RE = re.compile(r"^[ \t.,;:()\[\]\-–—/]*$")


def merge_adjacent(text: str, spans: list[dict]) -> list[dict]:
    """Return the spans with back-to-back spans of one label joined.

    The gold keeps every element apart (two ``party`` elements on one
    line, a docket number the seeds cut at a hyphen), but the model is
    shown one continuous span: ``B`` once, then ``I`` to the end. Two
    spans join when they carry the same label, sit in the same block,
    and nothing but spaces, edge punctuation or inline markup lies
    between them. The decoder returns the joined span, so eval merges
    the gold the same way before comparing (Rachel, 2026-10-28).
    """
    out: list[dict] = []
    for sp in sorted(spans, key=lambda s: (s["start"], s["end"])):
        if out:
            prev = out[-1]
            gap = text[prev["end"]:sp["start"]]
            if (
                prev["label"] == sp["label"]
                and "\n" not in gap
                and _EDGE_GAP_RE.match(_INLINE_MARKUP_RE.sub("", gap))
            ):
                prev["end"] = max(prev["end"], sp["end"])
                continue
        out.append(dict(sp))
    return out


def content_mask(text: str) -> bytearray:
    """Return one byte per character: 1 for a word character.

    Whitespace and every character of a markup tag are 0.
    """
    mask = bytearray(ch not in " \n\t\r" for ch in text)
    for m in MARKUP_RE.finditer(text):
        mask[m.start() : m.end()] = bytes(m.end() - m.start())
    return mask


def words(text: str, mask: bytearray | None = None) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` of every word of ``text``, in order.

    A word is a maximal run of word characters (:func:`content_mask`),
    except that a page mark (:data:`PAGE_MARK_RE`) glued to the front
    of a run is a word of its own.
    """
    mask = content_mask(text) if mask is None else mask
    runs: list[tuple[int, int]] = []
    start = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    out: list[tuple[int, int]] = []
    for s, e in runs:
        # match on the slice: ``^`` anchors at the string start, not ``pos``
        m = PAGE_MARK_RE.match(text[s:e])
        if m and m.end() < e - s:
            out.append((s, s + m.end()))
            out.append((s + m.end(), e))
        else:
            out.append((s, e))
    return out


def word_of(text: str, mask: bytearray | None = None) -> list[int]:
    """Return the word index of every character, −1 outside words."""
    mask = content_mask(text) if mask is None else mask
    idx = [-1] * len(text)
    for k, (s, e) in enumerate(words(text, mask)):
        for c in range(s, e):
            idx[c] = k
    return idx


def head_tokens(
    text: str, offsets: list[tuple[int, int]], mask: bytearray | None = None
) -> list[int | None]:
    """Return, per word, the index of the token that carries its label.

    The head token of a word is the first token whose first word
    character lies in that word. A token that holds no word character
    (a special token, whitespace, a markup tag) heads nothing. A word
    without a head token (truncated away) maps to ``None``.
    """
    mask = content_mask(text) if mask is None else mask
    idx = word_of(text, mask)
    heads: list[int | None] = [None] * (max(idx, default=-1) + 1)
    for t, (ts, te) in enumerate(offsets):
        first = next(
            (c for c in range(ts, min(te, len(text))) if mask[c]), None
        )
        if first is None:
            continue
        w = idx[first]
        if heads[w] is None:
            heads[w] = t
    return heads


def word_tags(
    text: str, spans: list[dict], mask: bytearray | None = None
) -> tuple[list[str], int]:
    """Return one BIO tag per word, and how many spans start mid-word.

    Back-to-back spans of one label are one span first
    (:func:`merge_adjacent`). A word belongs to the span that covers
    most of its characters (a tie goes to the earlier span); a word no
    span covers more than half of is ``O``. So a gold span cut inside a
    word, or one drawn on purpose over ``ORDER`` in the OCR junk
    ``↓47ORDER``, still labels the word it mostly covers. A word is
    ``B`` when the word before it is not in the same span, ``I``
    otherwise. The count of spans that start inside a word is returned
    for the build gates.
    """
    mask = content_mask(text) if mask is None else mask
    spans = merge_adjacent(text, spans)
    ws = words(text, mask)
    owner = [-1] * len(text)
    for k, sp in enumerate(spans):
        for c in range(sp["start"], min(sp["end"], len(text))):
            if mask[c]:
                owner[c] = k
    tags: list[str] = []
    prev = -1
    for s, e in ws:
        counts: dict[int, int] = {}
        for c in range(s, e):
            if owner[c] != -1:
                counts[owner[c]] = counts.get(owner[c], 0) + 1
        k = -1
        if counts:
            best = max(counts.values())
            if best * 2 > e - s:
                k = min(i for i, n in counts.items() if n == best)
        if k == -1:
            tags.append("O")
        elif k == prev:
            tags.append(f"I-{spans[k]['label']}")
        else:
            tags.append(f"B-{spans[k]['label']}")
        prev = k
    starts = {s for s, _ in ws}
    mid = sum(
        1
        for sp in spans
        if next(
            (c for c in range(sp["start"], min(sp["end"], len(text)))
             if mask[c]),
            None,
        ) not in starts
    )
    return tags, mid


def _span_index_at(spans: list[dict], s: int, e: int, mask: bytearray) -> int:
    """The span that owns the word ``(s, e)`` under the rule of
    :func:`word_tags`, or −1."""
    counts: dict[int, int] = {}
    for k, sp in enumerate(spans):
        n = sum(1 for c in range(max(s, sp["start"]), min(e, sp["end"])) if mask[c])
        if n:
            counts[k] = counts.get(k, 0) + n
    if not counts:
        return -1
    best = max(counts.values())
    if best * 2 <= e - s:
        return -1
    return min(k for k, n in counts.items() if n == best)


def materialize_word_labels(
    text: str,
    spans: list[dict],
    offsets: list[tuple[int, int]],
    label2id: dict[str, int],
) -> dict:
    """Per-token label ids with one labelled token per word.

    :returns: ``{"labels", "n_unmaterialized", "n_midword"}``:
        ``labels`` holds one id per token (−100 everywhere but the head
        token of each word); ``n_unmaterialized`` counts spans whose
        every word lost its head token (truncation); ``n_midword``
        counts spans that start inside a word.
    """
    mask = content_mask(text)
    spans = merge_adjacent(text, spans)  # what the model is shown
    tags, mid = word_tags(text, spans, mask)
    heads = head_tokens(text, offsets, mask)
    labels = [MASKED] * len(offsets)
    for w, t in enumerate(heads):
        if t is not None:
            labels[t] = label2id[tags[w]]
    # a span is materialized when some word it owns has a head token
    ws = words(text, mask)
    seen_b: set[int] = set()
    k_prev = -1
    for w, tag in enumerate(tags):
        if tag.startswith("B-"):
            k_prev = _span_index_at(spans, ws[w][0], ws[w][1], mask)
        if tag != "O" and heads[w] is not None and k_prev != -1:
            seen_b.add(k_prev)
    return {
        "labels": labels,
        "n_unmaterialized": len(spans) - len(seen_b),
        "n_midword": mid,
    }


def decode_word_spans(
    text: str,
    offsets: list[tuple[int, int]],
    pred_ids: list[int],
    names: list[str],
) -> list[dict]:
    """Per-token predictions → whole-word character spans.

    One prediction per word, read from its head token. ``B-x`` opens a
    span; ``I-x`` continues a span of ``x`` or, after anything else,
    opens one (the lenient rule of the worker: a dropped ``B`` must not
    lose the span); ``O`` closes. Words with no head token (truncated)
    are skipped.
    """
    mask = content_mask(text)
    ws = words(text, mask)
    heads = head_tokens(text, offsets, mask)
    spans: list[dict] = []
    cur: dict | None = None
    for w, (s, e) in enumerate(ws):
        t = heads[w]
        if t is None or t >= len(pred_ids):
            continue
        tag = names[pred_ids[t]]
        if tag == "O":
            cur = None
            continue
        prefix, label = tag.split("-", 1)
        if prefix == "I" and cur is not None and cur["label"] == label:
            cur["end"] = e
            continue
        cur = {"label": label, "start": s, "end": e}
        spans.append(cur)
    return spans
