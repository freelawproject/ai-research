from toc.schema import (
    EntryV1,
    EntryV2,
    HistoryItem,
    PageV1,
    PageV2,
    history_text,
    to_page,
)

BASE = {
    "name": "Doe v. Roe",
    "court": "SDNY",
    "citations": ["1 F3d 1"],
    "continued_from_previous_page": False,
    "continues_on_next_page": False,
}


def v1(history):
    return EntryV1(**BASE, history=history, topics="Evid 5.")


def test_v1_history_loses_its_closing_full_stop():
    page = to_page(
        PageV1(running_head=None, entries=[v1("reh den."), v1("."), v1(None)])
    )
    assert [e.history for e in page.entries] == ["reh den", None, None]
    assert page.topics_extracted


def test_full_stops_inside_a_history_are_kept():
    page = to_page(PageV1(running_head=None, entries=[v1("aff 1 F.3d 2")]))
    assert page.entries[0].history == "aff 1 F.3d 2"


def test_split_history_joins_back_in_printed_order():
    items = [
        HistoryItem(action="reh den", case_name=None, citations=[]),
        HistoryItem(
            action="cert den",
            case_name="Poe v. Bank",
            citations=["1 SCt 2", "3 LEd2d 4"],
        ),
    ]
    assert history_text(items) == "reh den, cert den Poe v. Bank 1 SCt 2, 3 LEd2d 4"
    assert history_text([]) is None


def test_v2_keeps_the_split_history_and_has_no_topics():
    item = HistoryItem(action="aff", case_name=None, citations=["9 F3d 9"])
    page = to_page(PageV2(running_head="X", entries=[EntryV2(**BASE, history=[item])]))
    entry = page.entries[0]
    assert entry.history == "aff 9 F3d 9"
    assert entry.history_items == [item]
    assert entry.topics is None
    assert not page.topics_extracted
