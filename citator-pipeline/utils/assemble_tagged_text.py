"""Phase 1 of the citator pipeline: assemble per-opinion tagged artifacts.

Consumes the raw CL fetch for one cluster (Opinion records' html_with_citations
+ UnmatchedCitation rows) and produces a tagged artifact: per-opinion plain text
with `<cited id="N" group="gM">` tags, plus an id_to_metadata_map and groups map.

The load-bearing path walks the BeautifulSoup parse tree of html_with_citations
once, recording each citation span's offset directly (no string matching, so short
cites like "Id." and "supra" get exact positions). All HTML is stripped to plain
text except the citation tags we insert. An additive str.find() safety net catches
UnmatchedCitation rows that drifted out of html_with_citations.

See experiments_05192026/citator_pipeline_migration_plan.md, Phase 1, for the spec.

Input (one cluster's cl_fetch JSON)::

    {
      "cluster_id": 12345,
      "opinions": [
        {"id": 6789, "type": "020lead", "html_with_citations": "..."},
        ...
      ],
      "unmatched_citations": [
        {"id": 111, "citing_opinion_id": 6789, "citation_string": "..."},
        ...
      ]
    }

Output (one cluster's tagged artifact)::

    {
      "cluster_id": 12345,
      "opinions": [{"opinion_type": "020lead", "source_opinion_id": 6789, "tagged_text": "..."}, ...],
      "id_to_metadata_map": {"0": {"opinion_type", "source_opinion_id", "offset", "citation_string", "group_id"}, ...},
      "groups": {"g0": [0, 3, 5], ...},
      "n_tags": int,
      "n_unmatched_misses": int,
      "unmatched_miss_details": [{"citing_opinion_id", "unmatched_citation_id",
                                  "citation_string", "opinion_type"}, ...],
      "empty_html": ["030concurrence", ...]
    }
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# Conventional reading order for opinion types (CL `Opinion.type` strings).
# Lower sort key = earlier. Anything unlisted sorts after, by type string.
# NOTE: "015unamimous" is CL's literal value (sic — typo in the source enum).
OPINION_TYPE_ORDER = {
    "020lead": 0,
    "025plurality": 1,
    "030concurrence": 2,
    "035concurrenceinpart": 3,
    "040dissent": 4,
    "010combined": 5,
    "015unamimous": 6,
}
_FALLBACK_ORDER = 100

# Block-level tags whose content is a paragraph (emit `\n\n` after).
PARAGRAPH_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
# Line-break tags (emit a single `\n`).
LINEBREAK_TAGS = {"br"}


@dataclass
class RecordedCitation:
    """One citation occurrence recorded during the tree walk / safety net.

    `offset` is the position in the per-opinion PLAIN text (pre-tag-insertion).
    `cited_id`, `group_id`, and `final_offset` are assigned later, at the
    cluster-level enumeration + tag-insertion step.
    """

    offset: int
    citation_string: str
    data_id: str | None
    cited_id: int = -1
    group_id: str = ""
    final_offset: int = -1


class _PlainTextBuilder:
    """Accumulates plain text while tracking length and trailing newlines.

    Trailing-newline tracking lets paragraph breaks collapse to exactly two
    newlines so nested/stacked blocks don't produce runaway separator chains —
    without a post-hoc regex pass that would invalidate recorded offsets.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.length = 0
        self._trailing_nl = 0

    def emit(self, s: str) -> None:
        if not s:
            return
        self._parts.append(s)
        self.length += len(s)
        n_trailing = len(s) - len(s.rstrip("\n"))
        if n_trailing == len(s):
            # Entire string is newlines — extend the running trailing count.
            self._trailing_nl += n_trailing
        else:
            self._trailing_nl = n_trailing

    def paragraph_break(self) -> None:
        """Ensure the text ends with exactly two newlines."""
        for _ in range(max(0, 2 - self._trailing_nl)):
            self.emit("\n")

    def text(self) -> str:
        return "".join(self._parts)


def _opinion_sort_key(opinion_type: str) -> tuple[int, str]:
    return (OPINION_TYPE_ORDER.get(opinion_type, _FALLBACK_ORDER), opinion_type)


def _is_citation_span(node: object) -> bool:
    """True for any <span class="citation ...">, covering matched / no-link /
    multiple-matches variants uniformly."""
    if not isinstance(node, Tag) or node.name != "span":
        return False
    classes = node.get("class") or []
    return "citation" in classes


def walk_opinion(html: str) -> tuple[str, list[RecordedCitation]]:
    """Walk an opinion's html_with_citations tree once.

    Returns (plain_text, records). Each record's `offset` is its position in
    `plain_text`. `data_id` is CL's `data-id` grouping signal (None for
    no-link / multiple-matches spans).
    """
    soup = BeautifulSoup(html, "lxml")
    builder = _PlainTextBuilder()
    records: list[RecordedCitation] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if _is_citation_span(child):
                offset = builder.length
                inner_text = child.get_text()
                data_id = child.get("data-id")
                records.append(
                    RecordedCitation(
                        offset=offset,
                        citation_string=inner_text,
                        data_id=data_id,
                    )
                )
                builder.emit(inner_text)
            elif isinstance(child, Tag):
                if child.name in PARAGRAPH_TAGS:
                    walk(child)
                    builder.paragraph_break()
                elif child.name in LINEBREAK_TAGS:
                    builder.emit("\n")
                else:
                    # Inline non-citation tag (<em>, <a> outside a citation,
                    # etc.) — transparent; recurse without altering the text.
                    walk(child)
            elif isinstance(child, NavigableString):
                builder.emit(str(child))

    walk(soup)
    return builder.text(), records


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and a_end > b_start


def apply_unmatched_safety_net(
    plain_text: str,
    records: list[RecordedCitation],
    unmatched_rows: list[dict],
) -> tuple[list[RecordedCitation], list[dict]]:
    """Add UnmatchedCitation rows that aren't already covered by the tree walk.

    Returns (added_records, miss_details). A "miss" is a citation_string that
    `str.find()` cannot locate at all (recorded in miss_details + skipped). A
    citation that IS found but overlaps an already-recorded span is silently
    skipped (already captured) — not counted as a miss.
    """
    added: list[RecordedCitation] = []
    miss_details: list[dict] = []
    # Occupied ranges: existing tree-walk records + safety-net adds so far.
    occupied = [
        (r.offset, r.offset + len(r.citation_string)) for r in records
    ]

    for row in unmatched_rows:
        cite = row.get("citation_string") or ""
        if not cite:
            continue

        if plain_text.find(cite) == -1:
            # Genuine miss — the citation text isn't in the opinion at all
            # (line-wrap / NBSP / encoding drift). Recorded, not raised.
            miss_details.append(
                {
                    "citing_opinion_id": row.get("citing_opinion_id"),
                    "unmatched_citation_id": row.get("id"),
                    "citation_string": cite,
                }
            )
            continue

        # Found at least once; take the first occurrence that doesn't overlap
        # an already-occupied range.
        chosen = -1
        search_from = 0
        while True:
            idx = plain_text.find(cite, search_from)
            if idx == -1:
                break
            m_start, m_end = idx, idx + len(cite)
            if any(
                _ranges_overlap(m_start, m_end, o_start, o_end)
                for o_start, o_end in occupied
            ):
                search_from = idx + 1
                continue
            chosen = idx
            break

        if chosen == -1:
            # Every occurrence overlaps an existing span → already captured by
            # the tree walk. Skip (not a miss).
            continue

        added.append(
            RecordedCitation(
                offset=chosen, citation_string=cite, data_id=None
            )
        )
        occupied.append((chosen, chosen + len(cite)))

    return added, miss_details


def _insert_tags(plain_text: str, records: list[RecordedCitation]) -> str:
    """Build the final tagged_text by inserting <cited> tags at recorded offsets.

    Mutates each record's `final_offset` to the position of its opening `<` in
    the returned string. Records must already carry `cited_id` + `group_id`.
    """
    out: list[str] = []
    out_len = 0
    cursor = 0

    for rec in sorted(records, key=lambda r: r.offset):
        if rec.offset < cursor:
            # Defensive: overlapping records should have been filtered by the
            # safety net's overlap check. Skip to avoid malformed nesting.
            logger.error(
                "Overlapping citation at offset %d (cursor %d); skipping id=%d",
                rec.offset,
                cursor,
                rec.cited_id,
            )
            continue

        segment = plain_text[cursor : rec.offset]
        out.append(segment)
        out_len += len(segment)

        open_tag = f'<cited id="{rec.cited_id}" group="{rec.group_id}">'
        rec.final_offset = out_len
        out.append(open_tag)
        out_len += len(open_tag)

        cite_len = len(rec.citation_string)
        cite_text = plain_text[rec.offset : rec.offset + cite_len]
        out.append(cite_text)
        out_len += len(cite_text)

        out.append("</cited>")
        out_len += len("</cited>")

        cursor = rec.offset + cite_len

    out.append(plain_text[cursor:])
    return "".join(out)


def assemble_cluster(cl_fetch: dict) -> dict:
    """Assemble one cluster's tagged artifact from its raw CL fetch.

    See module docstring for input/output schemas.
    """
    cluster_id = cl_fetch["cluster_id"]
    opinion_records = cl_fetch.get("opinions", [])
    unmatched_rows = cl_fetch.get("unmatched_citations", [])

    unmatched_by_opinion: dict[int, list[dict]] = defaultdict(list)
    for row in unmatched_rows:
        unmatched_by_opinion[row["citing_opinion_id"]].append(row)

    sorted_opinions = sorted(
        opinion_records, key=lambda o: _opinion_sort_key(o["type"])
    )

    empty_html: list[str] = []
    unmatched_miss_details: list[dict] = []
    # Per opinion, in reading order: (opinion_type, source_opinion_id,
    # plain_text, records). source_opinion_id (CL Opinion.pk) is the unique key
    # linking a citation back to its opinion — opinion_type is NOT unique within
    # a cluster (e.g., multiple concurrences / dissents).
    walked: list[tuple[str, int, str, list[RecordedCitation]]] = []

    for op in sorted_opinions:
        html = op.get("html_with_citations") or ""
        if not html.strip():
            empty_html.append(op["type"])
            continue

        plain_text, records = walk_opinion(html)
        added, miss_details = apply_unmatched_safety_net(
            plain_text, records, unmatched_by_opinion.get(op["id"], [])
        )
        for miss in miss_details:
            miss["opinion_type"] = op["type"]
            miss["source_opinion_id"] = op["id"]
        unmatched_miss_details.extend(miss_details)
        records.extend(added)
        records.sort(key=lambda r: r.offset)
        walked.append((op["type"], op["id"], plain_text, records))

    # Cluster-level enumeration: assign global ids + group ids across all
    # opinions in reading order, document order within each opinion.
    next_id = 0
    next_group = 0
    data_id_to_group: dict[str, str] = {}
    for _opinion_type, _source_opinion_id, _plain_text, records in walked:
        for rec in records:
            rec.cited_id = next_id
            next_id += 1
            if rec.data_id is not None:
                if rec.data_id not in data_id_to_group:
                    data_id_to_group[rec.data_id] = f"g{next_group}"
                    next_group += 1
                rec.group_id = data_id_to_group[rec.data_id]
            else:
                rec.group_id = f"g{next_group}"
                next_group += 1

    # Tag insertion + artifact build.
    opinions_out: list[dict] = []
    id_to_metadata_map: dict[str, dict] = {}
    groups: dict[str, list[int]] = defaultdict(list)
    n_tags = 0

    for opinion_type, source_opinion_id, plain_text, records in walked:
        tagged_text = _insert_tags(plain_text, records)
        opinions_out.append(
            {
                "opinion_type": opinion_type,
                "source_opinion_id": source_opinion_id,
                "tagged_text": tagged_text,
            }
        )
        for rec in records:
            id_to_metadata_map[str(rec.cited_id)] = {
                "opinion_type": opinion_type,
                "source_opinion_id": source_opinion_id,
                "offset": rec.final_offset,
                "citation_string": rec.citation_string,
                "group_id": rec.group_id,
            }
            groups[rec.group_id].append(rec.cited_id)
            n_tags += 1

    return {
        "cluster_id": cluster_id,
        "opinions": opinions_out,
        "id_to_metadata_map": id_to_metadata_map,
        "groups": dict(groups),
        "n_tags": n_tags,
        "n_unmatched_misses": len(unmatched_miss_details),
        "unmatched_miss_details": unmatched_miss_details,
        "empty_html": empty_html,
    }
