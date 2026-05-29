"""Phase 2 paginate-and-call: prepare LLM input JSONs from a Phase 1 tagged artifact.

For each cluster, this module:
- Estimates token counts (char-based, chars/3.7).
- Splits any opinion exceeding the chunk threshold into overlapping chunks at
  tag-safe boundaries (no chunk splits inside a `<cited>…</cited>` tag).
- Emits one LLM call per opinion (or per chunk, for long opinions). No
  cross-opinion bin-packing — keeping each call to a single opinion (or
  one chunk thereof) preserves attention.
- Assigns a per-call `opinion_handle` (always 0 under single-opinion calls)
  so the model can echo it back in `untagged_occurrences` for unambiguous
  postprocess resolution.
- Returns a list of `CallInput` objects, each exposing both the LLM input JSON
  (`.to_llm_input()`) and the postprocess metadata (`.meta(handle)`).

See `experiments_05192026/citator_pipeline_migration_plan.md` Phase 2 for the
locked spec (LLM input JSON, pagination params, boundary safety, opinion_handle
semantics).
"""

import bisect
import re
from dataclasses import dataclass, field

# ── Pagination defaults (mirrored from the plan doc) ────────────────────
CHARS_PER_TOKEN = 3.7
# Max content tokens of any single opinion within one call — used as BOTH:
#   (1) the threshold above which an opinion is chunked, AND
#   (2) the size of each chunk when chunking is needed.
# Tuned small (20K) after the 2026-05-28 smoke run showed Haiku 4.5 dropping
# 109/755 ids on a dense SCOTUS opinion held in a single 92K-token chunk.
# Smaller chunks trade calls for attention; postprocess merges across them.
MAX_OPINION_TOKENS = 20_000
CHUNK_OVERLAP_TOKENS = 5_000
BOUNDARY_MAX_WALK_CHARS = 1_000

# Matches our exact tag format from Phase 1.
_OPEN_TAG_RE = re.compile(r'<cited id="\d+" group="g\d+">')
_CLOSE_TAG = "</cited>"


def estimate_tokens(text: str) -> int:
    """Char-based token estimate. Legal text ≈ 3.5–3.8 chars/token."""
    return int(len(text) / CHARS_PER_TOKEN)


# ── Tag-safe boundary search ───────────────────────────────────────────

def _tag_regions(tagged_text: str) -> list[tuple[int, int]]:
    """Sorted (open_start, close_end) ranges occupied by `<cited>` tags.

    Used by `_inside_tag` for O(log n) lookups during boundary search.
    """
    regions: list[tuple[int, int]] = []
    for m in _OPEN_TAG_RE.finditer(tagged_text):
        close_idx = tagged_text.find(_CLOSE_TAG, m.end())
        if close_idx == -1:
            # Defensive: open without close. Treat as extending to text end.
            regions.append((m.start(), len(tagged_text)))
        else:
            regions.append((m.start(), close_idx + len(_CLOSE_TAG)))
    return regions


def _inside_tag(pos: int, regions: list[tuple[int, int]]) -> bool:
    """O(log n) check: is `pos` inside one of the precomputed tag regions?"""
    # Find the rightmost region with start ≤ pos.
    idx = bisect.bisect_right(regions, (pos, len(regions) and regions[-1][1] + 1)) - 1
    if idx >= 0:
        start, end = regions[idx]
        if start <= pos < end:
            return True
    return False


def find_safe_boundary(
    tagged_text: str,
    target: int,
    max_walk: int = BOUNDARY_MAX_WALK_CHARS,
    regions: list[tuple[int, int]] | None = None,
) -> int:
    """Walk from `target` to the nearest safe break position outside any `<cited>` tag.

    Preference order: paragraph break (`\\n\\n`), then sentence break (. ! ?
    followed by whitespace), then any char outside a tag. Searches ±max_walk
    chars from `target`.

    Returns the position AFTER the break (so `tagged_text[start:return_value]`
    includes the break). Returns 0 / len(text) at the extremes.
    """
    if target <= 0:
        return 0
    if target >= len(tagged_text):
        return len(tagged_text)

    if regions is None:
        regions = _tag_regions(tagged_text)
    n = len(tagged_text)

    def candidates():
        """Yield positions ordered by closeness to target, within ±max_walk."""
        yield target
        for delta in range(1, max_walk + 1):
            for sign in (-1, 1):
                yield target + sign * delta

    # Pass 1: paragraph break.
    for pos in candidates():
        if 0 <= pos < n - 1:
            if tagged_text[pos:pos + 2] == "\n\n" and not _inside_tag(pos, regions):
                # Skip past consecutive newlines so we don't leave dangling ones.
                end = pos + 2
                while end < n and tagged_text[end] == "\n":
                    end += 1
                return end

    # Pass 2: sentence break.
    for pos in candidates():
        if 0 <= pos < n - 1:
            if (tagged_text[pos] in ".!?"
                    and tagged_text[pos + 1] in " \n\t"
                    and not _inside_tag(pos, regions)):
                end = pos + 1
                while end < n and tagged_text[end] in " \n\t":
                    end += 1
                return end

    # Pass 3: any char outside a tag.
    for pos in candidates():
        if 0 <= pos < n and not _inside_tag(pos, regions):
            return pos

    # Final fallback (extremely unlikely with our format): return target.
    return target


# ── Chunking ──────────────────────────────────────────────────────────

def chunk_opinion(
    tagged_text: str,
    chunk_size_tokens: int = MAX_OPINION_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    max_walk: int = BOUNDARY_MAX_WALK_CHARS,
) -> list[tuple[int, str]]:
    """Split `tagged_text` into overlapping chunks at tag-safe boundaries.

    Returns `[(start_offset_in_original, chunk_text), …]`. Chunks overlap by
    ~`overlap_tokens`; both edges respect `<cited>` tag integrity.
    """
    char_size = int(chunk_size_tokens * CHARS_PER_TOKEN)
    char_overlap = int(overlap_tokens * CHARS_PER_TOKEN)
    n = len(tagged_text)
    regions = _tag_regions(tagged_text)

    chunks: list[tuple[int, str]] = []
    start = 0
    while start < n:
        target_end = start + char_size
        if target_end >= n:
            chunks.append((start, tagged_text[start:]))
            break
        safe_end = find_safe_boundary(tagged_text, target_end, max_walk, regions)
        if safe_end <= start:
            # No safe boundary found ahead — force progress.
            safe_end = min(start + char_size, n)
        chunks.append((start, tagged_text[start:safe_end]))

        next_target = safe_end - char_overlap
        if next_target >= n - 1:
            break
        next_start = find_safe_boundary(tagged_text, next_target, max_walk, regions)
        if next_start <= start:
            next_start = start + 1
        if next_start >= n:
            break
        start = next_start
    return chunks


# ── Public entry point ───────────────────────────────────────────────

@dataclass
class CallInput:
    """One LLM call's input + the postprocess metadata to resolve outputs back
    to opinion offsets.

    Each entry in `items` has:
      opinion_handle, source_opinion_id, opinion_type, chunk_index, total_chunks,
      chunk_start, tagged_text, tokens.

    `opinion_handle` indexes into `items` (handle == list index).
    """

    cluster_id: int
    items: list[dict] = field(default_factory=list)

    def to_llm_input(self) -> dict:
        """Project to just the JSON the model sees."""
        return {
            "opinions": [
                {
                    "opinion_handle": item["opinion_handle"],
                    "opinion_type": item["opinion_type"],
                    "chunk_index": item["chunk_index"],
                    "total_chunks": item["total_chunks"],
                    "tagged_text": item["tagged_text"],
                }
                for item in self.items
            ]
        }

    def meta(self, opinion_handle: int) -> dict:
        """Postprocess lookup: full item dict for a given handle."""
        return self.items[opinion_handle]


def make_record_id(cluster_id: int, call_idx: int) -> str:
    """Stable id for a Bedrock batch record: `{cluster_id}_call_{idx}`."""
    return f"{cluster_id}_call_{call_idx}"


def parse_record_id(record_id: str) -> tuple[int, int] | None:
    """Inverse of `make_record_id`. Returns `(cluster_id, call_idx)` or None
    if the string isn't a valid Phase 2 record id."""
    if "_call_" not in record_id:
        return None
    cluster_str, _, idx_str = record_id.rpartition("_call_")
    try:
        return int(cluster_str), int(idx_str)
    except ValueError:
        return None


def serialize_calls(calls: list["CallInput"]) -> list[dict]:
    """Project `CallInput` list to plain dicts for JSON persistence."""
    return [{"cluster_id": c.cluster_id, "items": c.items} for c in calls]


def deserialize_calls(serialized: list[dict]) -> list["CallInput"]:
    """Reconstruct `CallInput`s from `serialize_calls` output."""
    return [CallInput(cluster_id=s["cluster_id"], items=s["items"]) for s in serialized]


def _build_item(opinion: dict, chunk_index: int, total_chunks: int,
                chunk_start: int, tagged_text: str) -> dict:
    return {
        "source_opinion_id": opinion["source_opinion_id"],
        "opinion_type": opinion["opinion_type"],
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "chunk_start": chunk_start,
        "tagged_text": tagged_text,
        "tokens": estimate_tokens(tagged_text),
    }


def prepare_calls(
    tagged_artifact: dict,
    max_opinion_tokens: int = MAX_OPINION_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> list[CallInput]:
    """Read a Phase 1 tagged artifact; return one call per opinion (or per chunk).

    Rules:

    1. Every opinion produces at least one call. No cross-opinion packing —
       each call sees exactly one opinion (or one chunk of one opinion).
    2. An opinion at or under `max_opinion_tokens` becomes one whole-opinion
       call (`chunk_index=1`, `total_chunks=1`).
    3. An opinion above `max_opinion_tokens` is chunked into overlapping
       pieces (`chunk_size = max_opinion_tokens`, overlap = `overlap_tokens`).
       Each chunk becomes its own single-opinion call.
    4. `010combined` and non-combined opinions are treated identically under
       this rule. The combined-vs-non-combined isolation that earlier
       packing logic enforced is automatic now: every call already sees
       only one opinion.
    5. `opinion_handle` is always 0 within a single-opinion call. The field
       is kept in the output schema for compatibility with the model
       contract.
    """
    cluster_id = tagged_artifact["cluster_id"]
    opinions = tagged_artifact.get("opinions", [])

    calls: list[CallInput] = []
    for opinion in opinions:
        text = opinion["tagged_text"]
        tokens = estimate_tokens(text)
        if tokens <= max_opinion_tokens:
            item = {**_build_item(opinion, 1, 1, 0, text), "opinion_handle": 0}
            calls.append(CallInput(cluster_id=cluster_id, items=[item]))
        else:
            chunks = chunk_opinion(text, max_opinion_tokens, overlap_tokens)
            n_chunks = len(chunks)
            for i, (start, chunk_text) in enumerate(chunks):
                item = {
                    **_build_item(opinion, i + 1, n_chunks, start, chunk_text),
                    "opinion_handle": 0,
                }
                calls.append(CallInput(cluster_id=cluster_id, items=[item]))
    return calls
