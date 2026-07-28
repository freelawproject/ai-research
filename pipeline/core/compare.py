"""Compare + resolve: find where the normalized streams
disagree and resolve every disagreement.

The comparison works ONLY on the canonical unit keys (normalization
output): the two
(or three) key streams are diffed, and each run of disagreeing units is
one DISPUTE. Provenance replaces reverse-matching — a dispute maps to
its main-engine block(s) by looking up its tokens' `item` (plus the
second fragment of a wrap-joined word, so a wrapped dispute crops BOTH
source bboxes). There is no heuristic box attribution anywhere.

Tiebreak routes (LightOn in the third slot):
  main vs supplemental diff -> disputes -> main-block lookup -> gates:
  - page_number/heading blocks are MAIN-AUTHORITATIVE (SKIP_ROLES):
    LightOn over-reads short crops, so those disputes never tiebreak;
  - blocks under GATE_MIN_CHARS of main content are never cropped
    (kept as the main reading, flagged low-confidence);
  then the block crop's cached LightOn read is decoded + normalized
  like any engine stream and must pass the ENDING-AGREEMENT check (its
  ending must match the block ending as EITHER stream wrote it —
  decoder repetition and truncation guard; main-only would reject
  legit reads whenever the block's own ending was the dispute) before
  it can vote. The disputed span is located
  inside the read by EXPANDING ANCHORS: take n units of context before
  and after the dispute, widen n until the anchor pair is unique.
  Majority of normalized keys wins; anything short of a majority keeps
  the main reading and flags it low-confidence.

Vote routes (a third model in the third slot):
  the main stream is diffed against BOTH supplementals; disagreement
  regions from the two alignments are merged into intervals on the main
  stream, each supplemental's aligned reading is projected out of its
  own alignment, and a direct 2-of-3 majority resolves the interval. No
  crop is ever sent to a tiebreaker. A three-way split keeps the main
  reading and flags it low-confidence.

Empty-vs-empty is agreement by construction (no units, no diff). Image
blocks contribute no tokens (reconstruction/normalization), so they
can never dispute.
Footnote MARKS are not comparison content: each supplemental's mark
sequence is compared against the main's in its own flow and reported.

The resolution parameters are stamped into every compare stage record.
"""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Callable, Sequence

from pipeline.core import markup, normalize

# ── resolution parameters (stamped into artifacts) ───────────────────────

# A main block with less content than this is never cropped for the
# tiebreaker — the crop would be too small to read reliably.
GATE_MIN_CHARS = 10
# Ending-agreement window: the last N chars of the joined keys of the
# LightOn read must equal the block's ending as main OR the
# supplemental wrote it (repetition/truncation guard).
TAIL_AGREE_CHARS = 12
# Expanding-anchor starting width, in units.
ANCHOR_START = 2
# Roles where the main engine is authoritative on tiebreak routes —
# LightOn over-reads short crops.
SKIP_ROLES = frozenset({"page_number", "heading"})
# A low-confidence dispute is HIGH RISK when the disputed text is
# longer than this many characters on ANY content side (main or a
# supplemental — a long unresolved span matters more than a stray
# punctuation glyph).
HIGH_RISK_CHARS = 5
# A REORDER is a paired deletion + insertion of near-identical text
# (both streams carry the content, they just disagree on where it
# belongs — e.g. physical vs semantic footnote order): both disputes
# resolve to the main's placement and are not low-confidence. Pairs
# need this key-list similarity and at least this
# many units on both sides.
REORDER_SIM = 0.9
REORDER_MIN_UNITS = 5

# A cached crop read for an exact main-block bbox, or None.
CropReader = Callable[[Sequence[int]], str | None]


def params() -> dict:
    return {
        "gate_min_chars": GATE_MIN_CHARS,
        "tail_agree_chars": TAIL_AGREE_CHARS,
        "anchor_start": ANCHOR_START,
        "skip_roles": sorted(SKIP_ROLES),
        "high_risk_chars": HIGH_RISK_CHARS,
        "reorder_sim": REORDER_SIM,
        "reorder_min_units": REORDER_MIN_UNITS,
    }


def _mark_reorders(disputes: list[dict]) -> None:
    """Pair a deletion (main has text, a supplemental has none) with
    an insertion of near-identical text elsewhere: the content exists
    in both streams, the disagreement is only WHERE it belongs. Both
    disputes become resolution="reorder" — the main's placement is
    kept, nothing is inserted, and neither is low-confidence. On vote
    routes the pairing only applies when every OTHER supplemental
    agrees with the main side (otherwise a real disagreement would be
    hidden behind the move)."""
    labels = {
        k for d in disputes for k in d["reads"] if k not in ("main", "lighton")
    }

    def _clean(d: dict, label: str, deletion: bool) -> bool:
        for k, v in d["reads"].items():
            if k in ("main", "lighton", label):
                continue
            if v != (d["reads"]["main"] if deletion else ""):
                return False
        return True

    for label in sorted(labels):
        dels = [
            d
            for d in disputes
            if d["reads"]["main"]
            and label in d["reads"]
            and not d["reads"][label]
            and _clean(d, label, deletion=True)
        ]
        inss = [
            d
            for d in disputes
            if not d["reads"]["main"]
            and d["reads"].get(label)
            and _clean(d, label, deletion=False)
        ]
        used: set[int] = set()
        for dd in dels:
            a = dd["reads"]["main"].split()
            if len(a) < REORDER_MIN_UNITS:
                continue
            best: tuple[float, dict] | None = None
            for di in inss:
                if id(di) in used:
                    continue
                b = di["reads"][label].split()
                if len(b) < REORDER_MIN_UNITS:
                    continue
                r = difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()
                if r >= REORDER_SIM and (best is None or r > best[0]):
                    best = (r, di)
            if best is not None:
                used.add(id(best[1]))
                for d in (dd, best[1]):
                    d.update(
                        verdict="main",
                        resolution="reorder",
                        reason=None,
                        low_confidence=False,
                    )


def _is_high_risk(d: dict) -> bool:
    """Low-confidence AND the disputed text exceeds HIGH_RISK_CHARS on
    some content side (the lighton read is a voter, not a side)."""
    if not d["low_confidence"]:
        return False
    longest = max(
        (len(v) for k, v in d["reads"].items() if k != "lighton"),
        default=0,
    )
    return longest > HIGH_RISK_CHARS


# ── expanding-anchor localization (the ONE localization mechanism) ───────


def _occurrences(hay: list[str], needle: list[str]) -> list[int]:
    if not needle:
        return []
    n = len(needle)
    return [i for i in range(len(hay) - n + 1) if hay[i : i + n] == needle]


def locate(
    hay: list[str],
    pre: list[str],
    post: list[str],
    start_n: int = ANCHOR_START,
) -> tuple[int, int] | None:
    """Locate a disputed span inside `hay` (a key stream) by its
    surrounding context: the last n units of `pre` and the first n of
    `post` are the anchor pair; if the pair matches more than one place
    the anchors WIDEN until unique. An empty side anchors to the stream
    edge. Returns the (start, end) unit span between the anchors, or
    None when no unique location exists."""
    n = start_n
    while True:
        p = pre[len(pre) - min(n, len(pre)) :]
        q = post[: min(n, len(post))]
        starts = [i + len(p) for i in _occurrences(hay, p)] if p else [0]
        ends = _occurrences(hay, q) if q else [len(hay)]
        cands = [(s, e) for s in starts for e in ends if e >= s]
        if len(cands) == 1:
            return cands[0]
        if not cands or n >= max(len(pre), len(post)):
            return None  # anchors absent, or ambiguous at full context
        n += 1


# ── alignment helpers ─────────────────────────────────────────────────────


def _opcodes(
    a: list[str], b: list[str]
) -> list[tuple[str, int, int, int, int]]:
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return [(tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes()]


def _merge_intervals(
    regions: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Merge overlapping AND touching disagreement intervals on the main
    stream (a zero-width insertion at another region's edge belongs with
    it — the two alignments produced them independently)."""
    out: list[list[int]] = []
    for a, b in sorted(regions):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _project(
    opcodes: list[tuple[str, int, int, int, int]], i1: int, i2: int
) -> tuple[int, int]:
    """Map a main-stream interval [i1, i2) onto the other stream of one
    alignment: the union of the b-ranges of every opcode the interval
    overlaps (equal opcodes clipped position-for-position; a zero-width
    insertion at the interval edge is included — merged intervals own
    their touching insertions)."""
    j1 = j2 = None
    for tag, a1, a2, b1, b2 in opcodes:
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
        for tag, a1, a2, b1, b2 in opcodes:
            if tag == "equal" and a1 <= i1 <= a2:
                p = b1 + (i1 - a1)
                return (p, p)
        return (0, 0)
    return (j1, j2)


# ── dispute assembly ─────────────────────────────────────────────────────


def _keys(tokens: list[dict]) -> list[str]:
    return [t["key"] for t in tokens]


def _text(tokens: list[dict], i1: int, i2: int, field: str) -> str:
    return " ".join(t[field] for t in tokens[i1:i2])


def _dispute_blocks(main_tokens: list[dict], i1: int, i2: int) -> list[int]:
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


class _MainStream:
    """The main stream plus its per-item lookups (roles from the
    reconstruction, raw text for the gate, bboxes for crops)."""

    def __init__(
        self,
        tokens: list[dict],
        recon_items: list[dict],
        main_blocks: list[dict],
    ) -> None:
        self.tokens = tokens
        self.keys = _keys(tokens)
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


# ── the LightOn tiebreak ─────────────────────────────────────────────────


def _lighton_tokens(text: str) -> list[dict]:
    """A cached crop read, decoded and normalized exactly like any
    engine stream (lighton-markup decode + the shared rules)."""
    items = [{"id": 0, "role": "content", "html": markup.lighton_markup(text)}]
    return normalize.normalize_items(items, "lighton")["tokens"]


def _tiebreak_one(
    ms: _MainStream,
    i1: int,
    i2: int,
    blocks: list[int],
    read_crop: CropReader,
    read_cache: dict[tuple[int, ...], list[dict] | None],
    supp_keys: list[str],
    ops: list[tuple[str, int, int, int, int]],
) -> dict:
    """Attempt the LightOn tiebreak for one dispute. Returns the
    tiebreak record; `keys` is set only when a usable, located read
    exists (the LightOn vote)."""
    rec: dict = {
        "bboxes": [],
        "cached": False,
        "accepted": None,
        "accepted_by": None,
        "located": None,
        "read": None,
        "keys": None,
        "display": None,
    }
    bboxes = [ms.bbox_of[b] for b in blocks if b in ms.bbox_of]
    if len(bboxes) != len(blocks):
        return rec  # a block without a bbox cannot be cropped
    rec["bboxes"] = bboxes
    parts: list[list[dict]] = []
    for bbox in bboxes:
        key = tuple(bbox)
        if key not in read_cache:
            text = read_crop(bbox)
            read_cache[key] = (
                _lighton_tokens(text) if text is not None else None
            )
        part = read_cache[key]
        if part is None:
            return rec  # no cached read for this exact crop
        parts.append(part)
    rec["cached"] = True
    hay_tokens = [t for part in parts for t in part]
    hay = _keys(hay_tokens)
    rec["read"] = " ".join(hay)
    # ending-agreement: the read must reach the block's ending as MAIN
    # or the SUPPLEMENTAL wrote it (main-only would reject legit reads
    # whenever the block's own ending IS the dispute). The
    # supplemental's block ending is its aligned
    # projection of the main block's token range.
    block_idx = ms.block_token_indices(blocks)
    block_keys = [ms.keys[i] for i in block_idx]
    tail_read = "".join(hay)[-TAIL_AGREE_CHARS:]
    tail_main = "".join(block_keys)[-TAIL_AGREE_CHARS:]
    tail_supp = ""
    if block_idx:
        s1, s2 = _project(ops, block_idx[0], block_idx[-1] + 1)
        tail_supp = "".join(supp_keys[s1:s2])[-TAIL_AGREE_CHARS:]
    if hay and tail_read == tail_main:
        rec["accepted_by"] = "main"
    elif hay and tail_supp and tail_read == tail_supp:
        rec["accepted_by"] = "supp1"
    rec["accepted"] = rec["accepted_by"] is not None
    if not rec["accepted"]:
        rec["tails"] = {
            "read": tail_read,
            "main": tail_main,
            "supp": tail_supp,
        }
        return rec
    # expanding anchors: context comes from inside the cropped blocks
    n_before = sum(1 for i in block_idx if i < i1)
    n_inside = sum(1 for i in block_idx if i1 <= i < i2)
    pre = block_keys[:n_before]
    post = block_keys[n_before + n_inside :]
    span = locate(hay, pre, post)
    rec["located"] = span is not None
    if span is None:
        return rec
    s, e = span
    rec["keys"] = hay[s:e]
    rec["display"] = " ".join(t["display"] for t in hay_tokens[s:e])
    return rec


# ── route resolution ─────────────────────────────────────────────────────


def _base_dispute(ms: _MainStream, i1: int, i2: int) -> dict:
    blocks = _dispute_blocks(ms.tokens, i1, i2)
    roles = []
    for b in blocks:
        r = ms.role_of.get(b, "content")
        if r not in roles:
            roles.append(r)
    return {
        "main_span": [i1, i2],
        "blocks": blocks,
        "roles": roles,
        "reads": {"main": _text(ms.tokens, i1, i2, "key")},
        "displays": {"main": _text(ms.tokens, i1, i2, "display")},
    }


def _resolve_tiebreak(
    ms: _MainStream,
    supp_tokens: list[dict],
    read_crop: CropReader | None,
) -> list[dict]:
    supp_keys = _keys(supp_tokens)
    ops = _opcodes(ms.keys, supp_keys)
    read_cache: dict[tuple[int, ...], list[dict] | None] = {}
    disputes: list[dict] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        d = _base_dispute(ms, i1, i2)
        d["spans"] = {"supp1": [j1, j2]}
        d["reads"]["supp1"] = " ".join(supp_keys[j1:j2])
        d["displays"]["supp1"] = _text(supp_tokens, j1, j2, "display")
        blocks = d["blocks"]
        if all(r in SKIP_ROLES for r in d["roles"]):
            d.update(
                verdict="main",
                resolution="authoritative",
                reason="role-authoritative",
                low_confidence=False,
            )
        elif (
            sum(len(ms.text_of.get(b, "").strip()) for b in blocks)
            < GATE_MIN_CHARS
        ):
            d.update(
                verdict="main",
                resolution="fallback",
                reason="under-gate",
                low_confidence=True,
            )
        elif read_crop is None:
            d.update(
                verdict="main",
                resolution="fallback",
                reason="no-cached-read",
                low_confidence=True,
            )
        else:
            tb = _tiebreak_one(
                ms, i1, i2, blocks, read_crop, read_cache, supp_keys, ops
            )
            d["tiebreak"] = tb
            if tb["keys"] is None:
                reason = (
                    "no-cached-read"
                    if not tb["cached"]
                    else "read-rejected"
                    if not tb["accepted"]
                    else "not-located"
                )
                d.update(
                    verdict="main",
                    resolution="fallback",
                    reason=reason,
                    low_confidence=True,
                )
            else:
                d["reads"]["lighton"] = " ".join(tb["keys"])
                d["displays"]["lighton"] = tb["display"]
                if tb["keys"] == supp_keys[j1:j2]:
                    d.update(
                        verdict="supp1",
                        resolution="majority",
                        reason=None,
                        low_confidence=False,
                    )
                elif tb["keys"] == ms.keys[i1:i2]:
                    d.update(
                        verdict="main",
                        resolution="majority",
                        reason=None,
                        low_confidence=False,
                    )
                else:
                    d.update(
                        verdict="main",
                        resolution="fallback",
                        reason="three-way-split",
                        low_confidence=True,
                    )
        disputes.append(d)
    return disputes


def _resolve_vote(
    ms: _MainStream,
    supps: list[tuple[str, list[dict]]],
) -> list[dict]:
    """Direct vote: merged disagreement intervals on the main stream,
    each supplemental's reading projected from its own alignment,
    2-of-3 majority. With one supplemental stream missing (degraded)
    there is no third voter — every dispute keeps main, low-confidence."""
    aligned = [(label, tokens, _keys(tokens)) for label, tokens in supps]
    ops = {label: _opcodes(ms.keys, keys) for label, _, keys in aligned}
    regions = [
        (i1, i2)
        for label, _, _ in aligned
        for tag, i1, i2, _, _ in ops[label]
        if tag != "equal"
    ]
    disputes: list[dict] = []
    for i1, i2 in _merge_intervals(regions):
        d = _base_dispute(ms, i1, i2)
        d["spans"] = {}
        main_read = ms.keys[i1:i2]
        supp_reads: list[tuple[str, list[str]]] = []
        for label, tokens, keys in aligned:
            j1, j2 = _project(ops[label], i1, i2)
            d["spans"][label] = [j1, j2]
            d["reads"][label] = " ".join(keys[j1:j2])
            d["displays"][label] = _text(tokens, j1, j2, "display")
            supp_reads.append((label, keys[j1:j2]))
        if len(supp_reads) < 2:
            d.update(
                verdict="main",
                resolution="fallback",
                reason="no-vote",
                low_confidence=True,
            )
        elif supp_reads[0][1] == supp_reads[1][1]:
            # the supplementals agree; main is outvoted (or, on a
            # merged interval, everyone agrees after all — still the
            # supplementals' shared reading, identical to main's)
            winner = (
                supp_reads[0][0] if supp_reads[0][1] != main_read else "main"
            )
            d.update(
                verdict=winner,
                resolution="majority",
                reason=None,
                low_confidence=False,
            )
        elif main_read in (supp_reads[0][1], supp_reads[1][1]):
            d.update(
                verdict="main",
                resolution="majority",
                reason=None,
                low_confidence=False,
            )
        else:
            d.update(
                verdict="main",
                resolution="fallback",
                reason="three-way-split",
                low_confidence=True,
            )
        disputes.append(d)
    return disputes


# ── marks flow (report-only: marks are never comparison content) ─────────


def _mark_sequence(tokens: list[dict]) -> list[str]:
    return [m for t in tokens for m in t.get("marks", ())]


def _marks_report(
    main_tokens: list[dict], supps: list[tuple[str, list[dict]]]
) -> list[dict]:
    main_marks = _mark_sequence(main_tokens)
    out = []
    for label, tokens in supps:
        supp_marks = _mark_sequence(tokens)
        diffs = [
            {"main": main_marks[i1:i2], "supp": supp_marks[j1:j2]}
            for tag, i1, i2, j1, j2 in _opcodes(main_marks, supp_marks)
            if tag != "equal"
        ]
        out.append(
            {
                "label": label,
                "main": main_marks,
                "supp": supp_marks,
                "agree": not diffs,
                "diffs": diffs,
            }
        )
    return out


# ── entry point ──────────────────────────────────────────────────────────


def compare_streams(
    norm: dict,
    recon: dict,
    main_blocks: list[dict],
    tiebreak: str | None,
    read_crop: CropReader | None = None,
) -> dict:
    """The compare record for one page: disputes with verdicts, the
    marks report, and the page metrics. `norm`/`recon` are the
    normalize/reconstruct artifact records; `main_blocks` provides the main engine's bboxes;
    `read_crop` returns the cached LightOn read for an exact bbox."""
    ms = _MainStream(
        norm["main"]["tokens"], recon["main"]["items"], main_blocks
    )
    supp_recs = norm["supplementals"]
    labels = [f"supp{i + 1}" for i in range(len(supp_recs))]
    streams = [{"label": "main", "engine": norm["main"]["engine"]}] + [
        {"label": lb, "engine": s["engine"], "unit": s["unit"]}
        for lb, s in zip(labels, supp_recs)
    ]
    degraded = [
        lb for lb, s in zip(labels, supp_recs) if not s["tokens"] and ms.tokens
    ]
    if not ms.tokens and any(s["tokens"] for s in supp_recs):
        degraded.insert(0, "main")

    disputes: list[dict] = []
    if "main" not in degraded:
        if tiebreak is not None:
            if "supp1" not in degraded:
                disputes = _resolve_tiebreak(
                    ms, supp_recs[0]["tokens"], read_crop
                )
        else:
            # with one stream missing the remaining pair still
            # compares — there is just no third voter
            live = [
                (lb, s["tokens"])
                for lb, s in zip(labels, supp_recs)
                if lb not in degraded
            ]
            if live:
                disputes = _resolve_vote(ms, live)
    _mark_reorders(disputes)
    for n, d in enumerate(disputes):
        d["id"] = n
        d["high_risk"] = _is_high_risk(d)

    reasons = Counter(d["reason"] for d in disputes if d.get("reason"))
    resolutions = Counter(d["resolution"] for d in disputes)
    tb_recs = [d["tiebreak"] for d in disputes if "tiebreak" in d]
    metrics = {
        "main_units": len(ms.tokens),
        "disputed_units": sum(
            d["main_span"][1] - d["main_span"][0] for d in disputes
        ),
        "n_disputes": len(disputes),
        "by_resolution": dict(resolutions),
        "by_reason": dict(reasons),
        "n_low_confidence": sum(1 for d in disputes if d["low_confidence"]),
        "n_high_risk": sum(1 for d in disputes if d["high_risk"]),
    }
    if tiebreak is not None:
        metrics["tiebreak"] = {
            "attempted": len(tb_recs),
            "cached": sum(1 for t in tb_recs if t["cached"]),
            "accepted": sum(1 for t in tb_recs if t["accepted"]),
            "voted": sum(1 for t in tb_recs if t["keys"] is not None),
        }
    return {
        "params": params(),
        "streams": streams,
        "degraded": degraded,
        "disputes": disputes,
        "marks": _marks_report(
            ms.tokens,
            [(lb, s["tokens"]) for lb, s in zip(labels, supp_recs)],
        ),
        "metrics": metrics,
    }
