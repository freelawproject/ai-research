"""Uniform span normalization for evaluation — one implementation,
applied to GOLD and PREDICTED spans alike, so every comparison is
apples-to-apples (plan.md decisions log 2026-08-19).

Two levels, each producing content-anchored spans over the SAME text:

- normalize(…, punct=False) — CONTENT anchoring: trim whitespace and
  canonical-markup characters off span edges. Gold spans are built
  content-anchored, so this is a near-no-op for gold; for predictions
  it removes BPE-token bleed (tokens carry their leading space and can
  straddle into </p>).
- normalize(…, punct=True) — additionally trim EDGE punctuation
  (quotes, commas, parens, dashes, …). Interior punctuation is never
  touched. This separates "found the right thing, disagreed about the
  trailing comma" from real errors.

`span_prf` computes span-set precision/recall/F1 (exact (start, end,
label) matching after whichever normalization both sides were given) +
per-class breakdowns. Metrics are reported PRE- (punct=False) and
POST- (punct=True) normalization side by side.

`confine_spans` is the BUILD-TIME counterpart: it enforces the
structural invariant that spans never cross a block tag and never
carry dangling markup at their edges (encoder tags live strictly
inside <p>/<blockquote>/<footnotes>; a complete <em>/<sup> element may
sit wholly inside a span). Applied wherever spans are constructed —
the golden serializer, the pipeline transfer, and annotation-fragment
parsing. The resulting extents are authoritative: rendering colors and
label materialization follow them exactly.
"""

from __future__ import annotations

import re

MARKUP_RE = re.compile(r"</?(?:p|blockquote|footnotes|em|sup)>")
BLOCK_MARKUP_RE = re.compile(r"</?(?:p|blockquote|footnotes)>")
EDGE_PUNCT = set(",.;:!?()[]{}<>'\"“”‘’‚‟`´—–‒-−*§¶†‡·…/\\")


def edge_mask(text: str, punct: bool) -> bytearray:
    """1 = char is trimmable at a span EDGE (whitespace, markup chars,
    and — with punct — edge punctuation)."""
    mask = bytearray(len(text))
    for i, ch in enumerate(text):
        if ch.isspace() or (punct and ch in EDGE_PUNCT):
            mask[i] = 1
    for m in MARKUP_RE.finditer(text):
        for i in range(m.start(), m.end()):
            mask[i] = 1
    return mask


def trim_span(mask: bytearray, start: int, end: int) -> tuple[int, int]:
    while start < end and mask[start]:
        start += 1
    while end > start and mask[end - 1]:
        end -= 1
    return start, end


def normalize(text: str, spans: list[dict], punct: bool,
              mask: bytearray | None = None) -> list[dict]:
    """Normalized copies of `spans` (start/end/label kept, other keys
    preserved); empty-after-trim spans drop; duplicates collapse."""
    if mask is None:
        mask = edge_mask(text, punct)
    out: list[dict] = []
    seen: set = set()
    for s in spans:
        a, b = trim_span(mask, s["start"], s["end"])
        if a >= b:
            continue
        key = (a, b, s["label"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**s, "start": a, "end": b})
    return out


STYLE_TAG_RE = re.compile(r"</?(?:em|sup)>")
_STYLE_OPEN = ("<em>", "<sup>")
_STYLE_CLOSE = ("</em>", "</sup>")


def _style_pairs(text: str) -> tuple[dict, dict]:
    """Matching <em>/<sup> pairs over the whole text:
    (opener_start -> (closer_start, closer_end),
     closer_start -> (opener_start, opener_end))."""
    closer_of: dict[int, tuple[int, int]] = {}
    opener_of: dict[int, tuple[int, int]] = {}
    stacks: dict[str, list[tuple[int, int]]] = {"em": [], "sup": []}
    for m in STYLE_TAG_RE.finditer(text):
        name = m.group(0).strip("</>")
        if m.group(0).startswith("</"):
            if stacks[name]:
                o = stacks[name].pop()
                closer_of[o[0]] = (m.start(), m.end())
                opener_of[m.start()] = o
        else:
            stacks[name].append((m.start(), m.end()))
    return opener_of, closer_of


def _trim_confined(text: str, a: int, b: int, mask: bytearray,
                   opener_of: dict, closer_of: dict) -> tuple[int, int]:
    """Trim one piece's edges to a fixpoint: whitespace + block markup
    always; a styling tag only when DANGLING (its partner lies outside
    the piece). A balanced edge tag stays — the span wraps the complete
    styled element, keeping proper nesting."""
    while True:
        a0, b0 = a, b
        while a < b and mask[a]:
            a += 1
        while b > a and mask[b - 1]:
            b -= 1
        m = STYLE_TAG_RE.match(text, a)
        if m and m.end() <= b:
            if m.group(0).startswith("</"):
                a = m.end()  # closes an element opened before the span
            else:
                cl = closer_of.get(a)
                if cl is None or cl[1] > b:  # dangling opener
                    a = m.end()
        for t in _STYLE_OPEN + _STYLE_CLOSE:
            if b - a >= len(t) and text.startswith(t, b - len(t)):
                if t in _STYLE_OPEN:
                    b -= len(t)  # opens an element closing after span
                else:
                    op = opener_of.get(b - len(t))
                    if op is None or op[0] < a:  # dangling closer
                        b -= len(t)
                break
        if (a, b) == (a0, b0):
            break
    # extend outward to COMPLETE styled elements the span already
    # contains (content-anchored transfers stop at the last content
    # char, leaving the element's closer just outside): swallow an
    # adjacent closer whose opener is inside, and an adjacent opener
    # whose closer is inside — proper nesting, never past content.
    while True:
        a0, b0 = a, b
        m = STYLE_TAG_RE.match(text, b)
        if m and m.group(0).startswith("</"):
            op = opener_of.get(b)
            if op is not None and op[0] >= a:
                b = m.end()
        for t in _STYLE_OPEN:
            if a >= len(t) and text.startswith(t, a - len(t)):
                cl = closer_of.get(a - len(t))
                if cl is not None and cl[1] <= b:
                    a -= len(t)
                break
        if (a, b) == (a0, b0):
            return a, b


def confine_spans(text: str, spans: list[dict]) -> list[dict]:
    """Structural confinement of DATA spans (build-time, not an eval
    transform): a span's extent IS what gets colored and labeled, so it
    must sit strictly inside the structural markup. Every span is split
    at <p>/<blockquote>/<footnotes> tags; each piece's edges are trimmed
    of whitespace, block markup, and DANGLING styling tags — but a
    complete <em>/<sup> element at an edge stays inside the span
    (proper nesting: <heading>B. <em>x</em></heading>). Markup chars
    inside a span are never labeled (masked at materialization).

    Other keys copy to every piece; continuation pieces of a split span
    carry ``cont: True`` so count gates can reconcile against source
    elements. Empty pieces drop; records without an extent (lost
    transfers) pass through untouched."""
    mask = bytearray(len(text))
    for i, ch in enumerate(text):
        if ch.isspace():
            mask[i] = 1
    for m in BLOCK_MARKUP_RE.finditer(text):
        for i in range(m.start(), m.end()):
            mask[i] = 1
    opener_of, closer_of = _style_pairs(text)
    out: list[dict] = []
    for s in spans:
        if "start" not in s:
            out.append(s)
            continue
        cuts = [s["start"]]
        for m in BLOCK_MARKUP_RE.finditer(text, s["start"], s["end"]):
            cuts += [m.start(), m.end()]
        cuts.append(s["end"])
        first = True
        for a, b in zip(cuts[::2], cuts[1::2]):
            a, b = _trim_confined(text, a, b, mask, opener_of, closer_of)
            if a >= b:
                continue
            piece = {**s, "start": a, "end": b}
            piece.pop("cont", None)
            if not first:
                piece["cont"] = True
            out.append(piece)
            first = False
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def span_prf(gold: list[dict], pred: list[dict]) -> dict:
    """Span-set exact-match metrics; both sides must have been
    normalized IDENTICALLY beforehand."""
    gset = {(s["start"], s["end"], s["label"]) for s in gold}
    pset = {(s["start"], s["end"], s["label"]) for s in pred}
    tp = len(gset & pset)
    prec = tp / len(pset) if pset else 0.0
    rec = tp / len(gset) if gset else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    out = {
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "n_gold": len(gset),
        "n_pred": len(pset),
        "n_match": tp,
    }
    labels = sorted({k[2] for k in gset | pset})
    for lab in labels:
        g = {k for k in gset if k[2] == lab}
        p = {k for k in pset if k[2] == lab}
        t = len(g & p)
        pr = t / len(p) if p else 0.0
        rc = t / len(g) if g else 0.0
        out[f"{lab}_f1"] = round(
            2 * pr * rc / (pr + rc) if pr + rc else 0.0, 4
        )
        out[f"{lab}_support"] = len(g)
    return out
