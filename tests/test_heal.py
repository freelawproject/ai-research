

from tagger.heal import heal_disposition  # noqa: E402

T = ("<p>III.</p>\n<p>For the reasons stated, we find that the petitions "
     "for review of the Order are properly before this Court.</p>\n<p>AFFIRMED.</p>")


def sp(label, s, e):
    return {"label": label, "start": s, "end": e}


def test_pieces_become_the_block():
    a = T.index("For the"); b = T.index("</p>", a)
    pieces = [sp("disposition", T.index("the reasons"), T.index(" stated")),
              sp("disposition", T.index("we find"), T.index(" the Order")),
              sp("disposition", T.index("are properly"), b)]
    healed, log = heal_disposition(T, pieces + [sp("heading", 3, 7)])
    assert healed == [sp("heading", 3, 7), sp("disposition", a, b)]
    assert len(log) == 1 and len(log[0]["pieces"]) == 3 and log[0]["dropped"] == []


def test_single_span_untouched():
    a = T.index("AFFIRMED"); spans = [sp("disposition", a, a + 9)]
    assert heal_disposition(T, spans) == (spans, [])


def test_other_spans_inside_block_dropped_and_logged():
    a = T.index("For the"); b = T.index("</p>", a)
    spans = [sp("disposition", a, a + 7), sp("heading", a + 8, a + 15), sp("disposition", a + 20, b)]
    healed, log = heal_disposition(T, spans)
    assert healed == [sp("disposition", a, b)]
    assert log[0]["dropped"] == [("heading", T[a + 8:a + 15])]


def test_inline_markup_edges_trimmed():
    # edge markup is left outside the span, as the gold convention and
    # spannorm's content anchoring do; inner markup stays
    t = "<p><em>We</em> affirm. <em>So</em> ordered.</p>"
    spans = [sp("disposition", 7, 9), sp("disposition", 25, 32)]
    healed, _ = heal_disposition(t, spans)
    assert t[healed[0]["start"]:healed[0]["end"]] == "We</em> affirm. <em>So</em> ordered."
