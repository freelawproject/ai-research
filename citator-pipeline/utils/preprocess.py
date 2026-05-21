import logging
import re

logger = logging.getLogger(__name__)

# Page splitting thresholds.
# Sized for Haiku 4.5 in the two-stage batch pipeline: 200K-token input
# context, and Stage 1 output is much smaller per citation than the
# single-stage Sonnet pipeline (no quote/rationale/treatment, just
# mainCitationString + caseName + section_ids ≈ ~40 tokens/citation).
# 600K chars ≈ 150K input tokens leaves headroom under the 200K limit.
PAGE_SIZE_CHARS = 600_000
OVERLAP_CHARS = 20_000

# Paragraph boundary pattern (double newline or more)
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def split_opinion_to_pages(opinion_text, page_size=PAGE_SIZE_CHARS, overlap=OVERLAP_CHARS):
    """Split a long opinion into overlapping pages, breaking at paragraph boundaries.

    Returns a list of page strings. If the opinion fits in one page, returns [opinion_text].
    """
    text_len = len(opinion_text)
    if text_len <= page_size:
        logger.info(f"Opinion ({text_len:,} chars) fits in 1 page — no splitting needed")
        return [opinion_text]

    pages = []
    start = 0
    step = page_size - overlap

    while start < text_len:
        end = min(start + page_size, text_len)

        # If this isn't the last page, try to break at a paragraph boundary
        if end < text_len:
            # Search for paragraph break near the target end point
            # Look within the last 20% of the page for a clean break
            search_start = max(start, end - page_size // 5)
            breaks = list(PARAGRAPH_BREAK.finditer(opinion_text, search_start, end))
            if breaks:
                # Use the last paragraph break before the end
                end = breaks[-1].end()

        page = opinion_text[start:end]
        pages.append(page)

        if end >= text_len:
            break

        # Move start forward by step, but also try to align to paragraph boundary
        next_start = start + step
        # Look for a paragraph break near the overlap start point
        search_start = max(start, next_start - overlap // 2)
        search_end = min(text_len, next_start + overlap // 2)
        breaks = list(PARAGRAPH_BREAK.finditer(opinion_text, search_start, search_end))
        if breaks:
            # Use .end() so the new page starts at the beginning of the next paragraph,
            # not in the middle of whitespace between paragraphs
            new_start = breaks[0].end()
        else:
            new_start = next_start

        # Guard: ensure start always advances to prevent infinite loop
        if new_start <= start:
            start = next_start
        else:
            start = new_start

    logger.info(
        f"Split opinion ({text_len:,} chars) into {len(pages)} pages: "
        f"{[len(p) for p in pages]}"
    )
    return pages
