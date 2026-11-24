import pytest

from toc.evalset import EvalConflict, EvalPage, build_record

ENTRY = {
    "name": "Doe v. Roe",
    "court": "SDNY",
    "citations": ["1 F3d 1"],
    "history": None,
    "topics": None,
    "continued_from_previous_page": False,
    "continues_on_next_page": False,
}


def page(*statuses):
    rows = []
    for status in statuses:
        row = {"status": status, "source": None, "entry": None}
        if status != "open":
            row["entry"] = ENTRY
            row["judgments"] = {
                "a": {"level": "same", "suggested": "same"},
                "b": {"level": "ocr", "suggested": "ocr"},
            }
        rows.append(row)
    return EvalPage(engines=["a", "b"], rows=rows)


def test_record_counts_open_rows_and_levels():
    record = build_record(7, page("auto", "picked", "open"), None)
    assert (record["total"], record["open"], record["complete"]) == (3, 1, False)
    assert len(record["entries"]) == 2
    assert record["levels"]["b"]["ocr"] == 2


def test_page_is_complete_when_nothing_is_open():
    assert build_record(7, page("auto", "edited"), None)["complete"]


def test_an_accepted_row_needs_an_entry():
    body = EvalPage(
        engines=["a", "b"], rows=[{"status": "picked", "source": "a", "entry": None}]
    )
    with pytest.raises(ValueError, match="needs an entry"):
        build_record(7, body, None)


def test_a_save_that_drops_several_choices_is_refused():
    existing = build_record(7, page("picked", "picked", "edited"), None)
    with pytest.raises(EvalConflict):
        build_record(7, page("open", "open", "open"), existing)
    # A single undo goes through.
    build_record(7, page("picked", "picked", "open"), existing)
