"""Tests for wordlevel.py — synthetic offsets, no tokenizer."""



import pytest  # noqa: E402
from tagger.wordlevel import (  # noqa: E402
    MASKED,
    decode_word_spans,
    head_tokens,
    materialize_word_labels,
    merge_adjacent,
    word_tags,
    words,
)

NAMES = ["O", "B-party", "I-party", "B-court", "I-court"]
L2I = {n: i for i, n in enumerate(NAMES)}


def pieces(
    text: str, cuts: list[str], partial: bool = False
) -> list[tuple[int, int]]:
    """Offsets of the given consecutive pieces of ``text``.

    ``partial`` allows the pieces to stop short of the end (truncation).
    """
    out, at = [], 0
    for p in cuts:
        s = text.index(p, at)
        out.append((s, s + len(p)))
        at = s + len(p)
    assert partial or at == len(text), (at, len(text))
    return out


def test_words_skip_markup_and_glue_punctuation():
    text = "<p>Russell Shappy, Jr.; <em>v.</em>, X</p>"
    assert [text[s:e] for s, e in words(text)] == [
        "Russell", "Shappy,", "Jr.;", "v.", ",", "X",
    ]


def test_head_token_is_first_token_of_word():
    text = "<p>TRUST UNDER</p>"
    offs = pieces(text, ["<", "p", ">", "TR", "UST", " UND", "ER", "</", "p", ">"])
    assert head_tokens(text, offs) == [3, 5]


def test_labels_land_on_head_tokens_only():
    text = "<p>TRUST UNDER the TRUST</p>"
    offs = pieces(
        text,
        ["<", "p", ">", "TR", "UST", " UND", "ER", " the", " TR", "UST",
         "</", "p", ">"],
    )
    spans = [{"start": 3, "end": 24, "label": "party"}]
    out = materialize_word_labels(text, spans, offs, L2I)
    assert out["labels"] == [
        MASKED, MASKED, MASKED, L2I["B-party"], MASKED, L2I["I-party"],
        MASKED, L2I["I-party"], L2I["I-party"], MASKED, MASKED, MASKED,
        MASKED,
    ]
    assert out["n_unmaterialized"] == 0
    assert out["n_midword"] == 0


def test_midword_span_owns_the_word_it_mostly_covers():
    text = "<p>TRUST UNDER</p>"
    spans = [{"start": 5, "end": 14, "label": "party"}]  # "UST UNDER": 3 of 5
    tags, mid = word_tags(text, spans)
    assert tags == ["B-party", "I-party"]
    assert mid == 1


def test_minority_cover_leaves_the_word_unlabelled():
    text = "<p>TRUST UNDER</p>"
    spans = [{"start": 7, "end": 14, "label": "party"}]  # "T UNDER": 1 of 5
    tags, mid = word_tags(text, spans)
    assert tags == ["O", "B-party"]
    assert mid == 1


def test_page_mark_is_its_own_word():
    text = "<p>↓47ORDER and ↓48</p>"
    assert [text[s:e] for s, e in words(text)] == ["↓47", "ORDER", "and", "↓48"]
    text = "<p>1007(a) Pre-Miranda 2020 (b)</p>"
    assert [text[s:e] for s, e in words(text)] == ["1007", "(a)", "Pre-Miranda", "2020", "(b)"]


def test_span_on_the_word_after_a_page_mark():
    text = "<p>↓47ORDER</p>"
    spans = [{"start": 6, "end": 11, "label": "heading"}]  # ORDER alone
    tags, mid = word_tags(text, spans)
    assert tags == ["O", "B-heading"]
    assert mid == 0
    offs = pieces(text, ["<", "p", ">", "↓", "47", "ORDER", "</", "p", ">"])
    out = materialize_word_labels(text, spans, offs, {**L2I, "B-heading": 9, "I-heading": 10})
    assert out["labels"][3] == L2I["O"] and out["labels"][5] == 9
    assert out["n_unmaterialized"] == 0


def test_majority_cover_owns_a_word_cut_inside():
    text = "<p>ATTACHMENT</p>"
    spans = [{"start": 9, "end": 13, "label": "heading"}]  # "MENT": 4 of 10 → minority
    assert word_tags(text, spans)[0] == ["O"]
    spans = [{"start": 5, "end": 13, "label": "heading"}]  # "ACHMENT": 8 of 10
    assert word_tags(text, spans)[0] == ["B-heading"]


def test_same_label_spans_with_words_between_stay_apart():
    text = "<p>A v. B</p>"
    spans = [
        {"start": 3, "end": 4, "label": "party"},
        {"start": 8, "end": 9, "label": "party"},
    ]
    tags, _ = word_tags(text, spans)
    assert tags == ["B-party", "O", "B-party"]


def test_back_to_back_same_label_spans_are_one_for_the_model():
    text = "<p>CITY, Respondent. Commissioner, Respondent.</p>\n<p>X</p>"
    spans = [
        {"start": 3, "end": 20, "label": "party"},    # CITY, Respondent.
        {"start": 21, "end": 46, "label": "party"},   # Commissioner, Respondent.
        {"start": 54, "end": 55, "label": "party"},   # X — next block, stays apart
    ]
    assert text[54:55] == "X" and text[21:46] == "Commissioner, Respondent."
    merged = merge_adjacent(text, spans)
    assert [(s["start"], s["end"]) for s in merged] == [(3, 46), (54, 55)]
    tags, _ = word_tags(text, spans)
    assert tags == ["B-party", "I-party", "I-party", "I-party", "B-party"]


def test_merge_crosses_inline_markup_and_hyphen_but_not_labels():
    text = "<p>09-<em>18</em>-2020 v. heading</p>"
    spans = [
        {"start": 3, "end": 5, "label": "docketnumber"},    # 09
        {"start": 10, "end": 12, "label": "docketnumber"},  # 18
        {"start": 18, "end": 22, "label": "docketnumber"},  # 2020
        {"start": 26, "end": 33, "label": "heading"},
    ]
    merged = merge_adjacent(text, spans)
    assert [(s["start"], s["end"], s["label"]) for s in merged] == [
        (3, 22, "docketnumber"), (26, 33, "heading"),
    ]


def test_decode_cannot_cut_a_word():
    text = "<p>Colorado Court of Appeals</p>"
    offs = pieces(
        text, ["<", "p", ">", "Color", "ado", " Court", " of", " Appeals",
               "</", "p", ">"],
    )
    # the model labels "Color" O and "ado" I-court, then the rest court
    pred = [0, 0, 0, L2I["O"], L2I["I-court"], L2I["I-court"],
            L2I["I-court"], L2I["I-court"], 0, 0, 0]
    got = decode_word_spans(text, offs, pred, NAMES)
    # the word's head token says O, so the span starts at "Court"
    assert [(text[s["start"]:s["end"]], s["label"]) for s in got] == [
        ("Court of Appeals", "court"),
    ]
    pred[3] = L2I["B-court"]
    got = decode_word_spans(text, offs, pred, NAMES)
    assert text[got[0]["start"]:got[0]["end"]] == "Colorado Court of Appeals"


def test_decode_lenient_i_opens_a_span_and_b_splits():
    text = "<p>A B C</p>"
    offs = pieces(text, ["<", "p", ">", "A", " B", " C", "</", "p", ">"])
    pred = [0, 0, 0, L2I["I-party"], L2I["I-party"], L2I["B-party"], 0, 0, 0]
    got = decode_word_spans(text, offs, pred, NAMES)
    assert [text[s["start"]:s["end"]] for s in got] == ["A B", "C"]


def test_truncated_tail_is_unmaterialized():
    text = "<p>A</p>\n<p>B</p>"
    offs = pieces(
        text, ["<", "p", ">", "A", "</", "p", ">"], partial=True
    )  # B cut off
    spans = [
        {"start": 3, "end": 4, "label": "party"},
        {"start": 12, "end": 13, "label": "court"},
    ]
    out = materialize_word_labels(text, spans, offs, L2I)
    assert out["n_unmaterialized"] == 1
    assert out["labels"][3] == L2I["B-party"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
