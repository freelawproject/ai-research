"""The shape of one parsed page, shared by the runners, viewer and scorer.

Each prompt version has its own response schema (what the model returns).
Every response is converted to the same stored form, `Page`, so the viewer
and scorer read one format whatever prompt produced it.
"""

from pydantic import BaseModel

TEXT_FIELDS = ("name", "court", "history", "topics")
FLAG_FIELDS = ("continued_from_previous_page", "continues_on_next_page")


class HistoryItem(BaseModel):
    """One later-history phrase and the citations that belong to it."""

    action: str
    case_name: str | None
    citations: list[str]


class Entry(BaseModel):
    """Stored form of one entry."""

    name: str
    court: str | None
    citations: list[str]
    history: str | None  # the whole history as one string, in printed order
    history_items: list[HistoryItem] = []  # the same history, split (v2 on)
    topics: str | None = None  # null when the prompt does not extract topics
    continued_from_previous_page: bool
    continues_on_next_page: bool


class Page(BaseModel):
    running_head: str | None
    topics_extracted: bool = True
    entries: list[Entry]


class EntryV1(BaseModel):
    name: str
    court: str | None
    citations: list[str]
    history: str | None
    topics: str | None
    continued_from_previous_page: bool
    continues_on_next_page: bool


class PageV1(BaseModel):
    running_head: str | None
    entries: list[EntryV1]


class EntryV2(BaseModel):
    name: str
    court: str | None
    citations: list[str]
    history: list[HistoryItem]
    continued_from_previous_page: bool
    continues_on_next_page: bool


class PageV2(BaseModel):
    running_head: str | None
    entries: list[EntryV2]


# prompt name -> the schema the model is asked to fill
RESPONSE_SCHEMAS: dict[str, type[PageV1] | type[PageV2]] = {
    "table_of_cases_v1": PageV1,
    "table_of_cases_v2": PageV2,
    "table_of_cases_v3": PageV2,  # same shape; one item per run of actions
}


def history_text(items: list[HistoryItem]) -> str | None:
    """Join split history back into one string, in printed order."""
    parts = []
    for item in items:
        pieces = [item.action, item.case_name or "", ", ".join(item.citations)]
        parts.append(" ".join(piece for piece in pieces if piece))
    return ", ".join(parts) or None


def to_page(response: PageV1 | PageV2) -> Page:
    """Convert a model response to the stored form and apply clean-up rules."""
    entries = []
    for item in response.entries:
        if isinstance(item, EntryV2):
            fields = item.model_dump(exclude={"history"})
            entry = Entry(
                **fields, history=history_text(item.history), history_items=item.history
            )
        else:
            entry = Entry(**item.model_dump())
        if entry.history:
            # The full stop that closes the history is not part of it.
            entry.history = entry.history.strip().removesuffix(".").strip() or None
        entries.append(entry)
    return Page(
        running_head=response.running_head,
        topics_extracted=isinstance(response, PageV1),
        entries=entries,
    )
