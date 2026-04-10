"""Utilities for splitting opinion texts into labeled sections.

Sections are paragraph-based chunks of ~2000 characters, split at double-newline
boundaries. Each section gets a label (S1, S2, ...) that Haiku uses to report
where citations appear, and that we use to retrieve context for Kimi.
"""

import re

PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
DEFAULT_TARGET_SIZE = 2000


def split_into_sections(opinion_text, target_size=DEFAULT_TARGET_SIZE):
    """Split opinion text into labeled sections at paragraph boundaries.

    Accumulates paragraphs until reaching ~target_size chars, then starts a new
    section. Returns a list of dicts with id, text, start_char, end_char.
    """
    # Split into paragraphs at double-newline boundaries
    parts = PARAGRAPH_BREAK.split(opinion_text)
    if not parts:
        return [{"id": "S1", "text": opinion_text, "start_char": 0, "end_char": len(opinion_text)}]

    sections = []
    current_paragraphs = []
    current_len = 0
    # Track position in original text for start/end chars
    char_pos = 0
    section_start = 0
    section_num = 1

    for i, para in enumerate(parts):
        para_stripped = para.strip()
        if not para_stripped:
            # Skip empty paragraphs but advance position
            # Find this paragraph's position in the original text
            idx = opinion_text.find(para, char_pos)
            if idx >= 0:
                char_pos = idx + len(para)
            continue

        # Find this paragraph in the original text to track position
        idx = opinion_text.find(para, char_pos)
        if idx < 0:
            idx = char_pos
        para_end = idx + len(para)

        if current_len > 0 and current_len + len(para_stripped) > target_size:
            # Flush current section
            section_text = "\n\n".join(current_paragraphs)
            sections.append({
                "id": f"S{section_num}",
                "text": section_text,
                "start_char": section_start,
                "end_char": char_pos,
            })
            section_num += 1
            current_paragraphs = []
            current_len = 0
            section_start = idx

        current_paragraphs.append(para_stripped)
        current_len += len(para_stripped)
        char_pos = para_end

    # Flush remaining paragraphs
    if current_paragraphs:
        section_text = "\n\n".join(current_paragraphs)
        sections.append({
            "id": f"S{section_num}",
            "text": section_text,
            "start_char": section_start,
            "end_char": len(opinion_text),
        })

    # Edge case: empty text
    if not sections:
        return [{"id": "S1", "text": opinion_text, "start_char": 0, "end_char": len(opinion_text)}]

    return sections


def annotate_opinion_with_sections(opinion_text, sections):
    """Insert [Section Sn] markers at the start of each section.

    Returns the annotated string that Haiku receives as input.
    """
    parts = []
    for section in sections:
        parts.append(f"[Section {section['id']}]\n{section['text']}")
    return "\n\n".join(parts)


def get_section_context(sections, section_ids, include_neighbors=True):
    """Build context text from the given sections plus their neighbors.

    Args:
        sections: Full list of section dicts from split_into_sections().
        section_ids: List of section IDs (e.g., ["S3", "S5"]) where a citation appears.
        include_neighbors: If True, include the section before and after each target section.

    Returns:
        Combined text of the relevant sections with [Section Sn] markers.
    """
    # Build index map
    id_to_idx = {s["id"]: i for i, s in enumerate(sections)}
    n = len(sections)

    # Collect all indices we need (target sections + neighbors)
    target_indices = set()
    for sid in section_ids:
        idx = id_to_idx.get(sid)
        if idx is None:
            continue
        target_indices.add(idx)
        if include_neighbors:
            if idx > 0:
                target_indices.add(idx - 1)
            if idx < n - 1:
                target_indices.add(idx + 1)

    # Build combined text in order
    parts = []
    for idx in sorted(target_indices):
        s = sections[idx]
        parts.append(f"[Section {s['id']}]\n{s['text']}")

    return "\n\n".join(parts)
