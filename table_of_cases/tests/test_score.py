from toc.score import align, reviewer_tier, score_citations, without_joiners


def entry(name, citations, history=None, topics=None):
    return {
        "name": name,
        "court": "SDNY",
        "citations": citations,
        "history": history,
        "topics": topics,
        "continued_from_previous_page": False,
        "continues_on_next_page": False,
    }


def test_align_pairs_near_misses_and_marks_missing_and_extra():
    gold = [entry("A v. B", ["1 F3d 1"]), entry("C v. D", ["2 F3d 2"])]
    model = [
        entry("A v. B", ["1 F3d 1"]),
        entry("C v. Dx", ["2 F3d 2"]),
        entry("E v. F", ["3 F3d 3"]),
    ]
    pairs = align(gold, model)
    assert [(g and g["name"], m and m["name"]) for g, m in pairs] == [
        ("A v. B", "A v. B"),
        ("C v. D", "C v. Dx"),
        (None, "E v. F"),
    ]
    assert align(gold, model[:1])[1] == (gold[1], None)


def test_a_misread_citation_counts_as_found_not_missed():
    tally = score_citations(
        entry("A v. B", ["100 FSupp2d 918"]), entry("A v. B", ["100 FSupp2d 913"])
    )
    assert tally["citations_misread"] == 1
    assert tally["citations_missed"] == 0
    assert tally["citations_invented"] == 0


def test_added_citations_split_into_invented_and_misplaced():
    want = entry("A v. B", ["1 F3d 1"], history="cert den 50 SCt 60")
    got = entry("A v. B", ["1 F3d 1", "cert den 50 SCt 60", "9 F3d 9"])
    tally = score_citations(got, want)
    assert tally["citations_exact"] == 1
    assert tally["citations_misplaced"] == 1
    assert tally["citations_invented"] == 1


def test_a_dropped_citation_is_missed():
    tally = score_citations(entry("A v. B", []), entry("A v. B", ["1 F3d 1"]))
    assert tally["citations_missed"] == 1


def test_joining_and_between_history_phrases_is_dropped():
    assert without_joiners("reh den, and cert den") == "reh den, cert den"
    assert without_joiners("reh and reh den") == "reh and reh den"
    assert without_joiners(None) is None


def test_only_a_tier_the_reviewer_changed_overrides_the_rules():
    row = {
        "judgments": {
            "a": {"level": "field", "suggested": "hallucination"},
            "b": {"level": "ocr", "suggested": "ocr"},
        }
    }
    assert reviewer_tier(row, "a") == "field"
    assert reviewer_tier(row, "b") is None
    assert reviewer_tier(row, "c") is None
