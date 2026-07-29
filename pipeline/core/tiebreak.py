"""The LightOn tiebreak variant: resolve a two-engine disagreement by
reading the disputed region's crop with a third model.

EVERYTHING specific to that variant lives here — its gates, its
degeneration guards, its expanding-anchor localization, its retry policy
and its resolver. The direct three-way vote is `vote.py`; primitives
both share are `streams.py`. Dropping the tiebreaker means dropping this
module, its tests, and the crop export/ingest commands — nothing else.

The flow for one dispute:
  - page_number/heading blocks are MAIN-AUTHORITATIVE (SKIP_ROLES):
    LightOn over-reads short crops, so those disputes never tiebreak;
  - a dispute holding ANY block under GATE_MIN_CHARS of main content is
    never cropped (kept as the main reading, flagged low-confidence) —
    every block of a dispute is cropped, and a crop of a few glyphs is
    read as invented content;
  - the block crop's cached read is decoded + normalized like any engine
    stream and must pass two degeneration guards: LENGTH PLAUSIBILITY
    (no longer than READ_LEN_RATIO times either engine's reading of the
    same blocks) and ENDING-AGREEMENT (its ending must match the block
    ending as EITHER stream wrote it — main-only would reject legit
    reads whenever the block's own ending was the dispute);
  - the disputed span is located inside the read by EXPANDING ANCHORS:
    take n units of context before and after, widen n until the anchor
    pair is unique;
  - a read that fails any of those steps costs the crop ONE RETRY (the
    same crop re-decoded, see engines.lighton.retry_decode). Until that
    retry is run the dispute is retry-pending; a retry that fails too is
    final.
Majority of normalized keys wins; anything short of a majority keeps the
main reading and flags it low-confidence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pipeline.core import markup, normalize, streams

# ── resolution parameters (stamped into artifacts) ───────────────────────

# A dispute holding a main block with less content than this is never
# cropped for the tiebreaker — that crop is too small to read reliably,
# and a crop of a few pixels makes the reader invent content outright.
# Measured per BLOCK, not over the dispute: every block in the dispute
# is cropped, so one tiny block is one unreadable crop.
GATE_MIN_CHARS = 10
# Ending-agreement window: the last N chars of the joined keys of the
# LightOn read must equal the block's ending as main OR the
# supplemental wrote it (repetition/truncation guard).
TAIL_AGREE_CHARS = 12
# Length plausibility: the crop covers whole blocks, so a read this many
# times longer than either engine's reading of those blocks is decoder
# degeneration rather than a reading (a thin crop makes the reader emit
# pages of invented LaTeX). Legitimate reads sit at ~1.05x and have
# never been observed past 2.3x.
READ_LEN_RATIO = 3.0
# Expanding-anchor starting width, in units.
ANCHOR_START = 2
# Roles where the main engine is authoritative on tiebreak routes —
# LightOn over-reads short crops.
SKIP_ROLES = frozenset({"page_number", "heading"})

# A cached crop read for an exact main-block bbox (second argument: the
# retry read rather than the first), or None when it has not been run.
CropReader = Callable[[Sequence[int], bool], str | None]


def params() -> dict:
    """This variant's parameters, stamped into every compare record."""
    return {
        "gate_min_chars": GATE_MIN_CHARS,
        "tail_agree_chars": TAIL_AGREE_CHARS,
        "read_len_ratio": READ_LEN_RATIO,
        "anchor_start": ANCHOR_START,
        "skip_roles": sorted(SKIP_ROLES),
    }


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


# ── the crop read ────────────────────────────────────────────────────────


def _read_tokens(text: str) -> list[dict]:
    """A cached crop read, decoded and normalized exactly like any
    engine stream (lighton-markup decode + the shared rules)."""
    items = [{"id": 0, "role": "content", "html": markup.lighton_markup(text)}]
    return normalize.normalize_items(items, "lighton")["tokens"]


def _crop_parts(
    bboxes: Sequence[Sequence[int]],
    read_crop: CropReader,
    read_cache: dict[tuple[tuple[int, ...], bool], list[dict] | None],
    retry: bool,
) -> list[list[dict]] | None:
    """The cached read of every crop of one dispute, tokenized — None as
    soon as any crop has no read for this attempt."""
    parts: list[list[dict]] = []
    for bbox in bboxes:
        key = (tuple(int(c) for c in bbox), retry)
        if key not in read_cache:
            text = read_crop(bbox, retry)
            read_cache[key] = _read_tokens(text) if text is not None else None
        part = read_cache[key]
        if part is None:
            return None
        parts.append(part)
    return parts


def _attempt_read(
    ms: streams.MainStream,
    i1: int,
    i2: int,
    blocks: list[int],
    read_crop: CropReader,
    read_cache: dict[tuple[tuple[int, ...], bool], list[dict] | None],
    supp_keys: list[str],
    ops: list[tuple[str, int, int, int, int]],
) -> dict:
    """Attempt the LightOn tiebreak for one dispute — the first read,
    then ONE re-decoded retry if that read does not survive the guards.
    Returns the tiebreak record; `keys` is set only when a usable,
    located read exists (the LightOn vote)."""
    rec: dict = {
        "bboxes": [],
        "cached": False,
        "attempt": None,
        "retry_pending": False,
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
    parts = _crop_parts(bboxes, read_crop, read_cache, retry=False)
    if parts is None:
        return rec  # no cached read for this exact crop
    rec["cached"] = True
    # the same blocks as each engine wrote them: main's own tokens, and
    # the supplemental's aligned projection of that token range.
    block_idx = ms.block_token_indices(blocks)
    block_keys = [ms.keys[i] for i in block_idx]
    supp_block_keys: list[str] = []
    if block_idx:
        s1, s2 = streams.project(ops, block_idx[0], block_idx[-1] + 1)
        supp_block_keys = supp_keys[s1:s2]
    engine_len = max(
        len("".join(block_keys)), len("".join(supp_block_keys)), 1
    )
    # expanding anchors: context comes from inside the cropped blocks
    n_before = sum(1 for i in block_idx if i < i1)
    n_inside = sum(1 for i in block_idx if i1 <= i < i2)
    pre = block_keys[:n_before]
    post = block_keys[n_before + n_inside :]
    for attempt in (1, 2):
        if attempt == 2:
            parts = _crop_parts(bboxes, read_crop, read_cache, retry=True)
            if parts is None:
                rec["retry_pending"] = True  # the retry has not been run
                return rec
        rec.update(attempt=attempt, implausible=False, located=None)
        rec.pop("tails", None)
        hay_tokens = [t for part in parts for t in part]
        hay = streams.keys(hay_tokens)
        rec["read"] = " ".join(hay)
        # length plausibility: a read far longer than EITHER engine's
        # reading of these blocks is degeneration, not a reading
        read_len = len("".join(hay))
        rec["length_ratio"] = round(read_len / engine_len, 2)
        if read_len > READ_LEN_RATIO * engine_len:
            rec.update(accepted=False, implausible=True)
            continue
        # ending-agreement: the read must reach the block's ending as
        # MAIN or the SUPPLEMENTAL wrote it (main-only would reject
        # legit reads whenever the block's own ending IS the dispute).
        tail_read = "".join(hay)[-TAIL_AGREE_CHARS:]
        tail_main = "".join(block_keys)[-TAIL_AGREE_CHARS:]
        tail_supp = "".join(supp_block_keys)[-TAIL_AGREE_CHARS:]
        rec["accepted_by"] = None
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
            continue
        span = locate(hay, pre, post)
        rec["located"] = span is not None
        if span is None:
            continue
        s, e = span
        rec["keys"] = hay[s:e]
        rec["display"] = " ".join(t["display"] for t in hay_tokens[s:e])
        return rec
    return rec  # both attempts failed a guard


# ── the resolver ─────────────────────────────────────────────────────────


def resolve(
    ms: streams.MainStream,
    supp_tokens: list[dict],
    read_crop: CropReader | None,
) -> list[dict]:
    """Every disagreement between the main and the one supplemental
    stream, resolved through the crop tiebreak."""
    supp_keys = streams.keys(supp_tokens)
    ops = streams.opcodes(ms.keys, supp_keys)
    read_cache: dict[tuple[tuple[int, ...], bool], list[dict] | None] = {}
    disputes: list[dict] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        d = streams.base_dispute(ms, i1, i2)
        d["spans"] = {"supp1": [j1, j2]}
        d["reads"]["supp1"] = " ".join(supp_keys[j1:j2])
        d["displays"]["supp1"] = streams.text(supp_tokens, j1, j2, "display")
        blocks = d["blocks"]
        if all(r in SKIP_ROLES for r in d["roles"]):
            d.update(
                verdict="main",
                resolution="authoritative",
                reason="role-authoritative",
                low_confidence=False,
            )
        elif (
            min(
                (len(ms.text_of.get(b, "").strip()) for b in blocks),
                default=0,
            )
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
            tb = _attempt_read(
                ms, i1, i2, blocks, read_crop, read_cache, supp_keys, ops
            )
            d["tiebreak"] = tb
            if tb["keys"] is None:
                reason = (
                    "no-cached-read"
                    if not tb["cached"]
                    else "retry-pending"
                    if tb["retry_pending"]
                    else "read-implausible"
                    if tb.get("implausible")
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


def metrics(disputes: list[dict]) -> dict:
    """The tiebreak funnel for the page metrics."""
    recs = [d["tiebreak"] for d in disputes if "tiebreak" in d]
    return {
        "attempted": len(recs),
        "cached": sum(1 for t in recs if t["cached"]),
        "accepted": sum(1 for t in recs if t["accepted"]),
        "voted": sum(1 for t in recs if t["keys"] is not None),
    }
