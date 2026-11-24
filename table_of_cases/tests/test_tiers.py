import json
from pathlib import Path

import pytest

from toc.tiers import entry_level, is_misread, norm

FIXTURE = json.loads((Path(__file__).parent / "tier_cases.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_entry_level_matches_shared_cases(case):
    got = {**FIXTURE["base"], **case["model"]}
    want = {**FIXTURE["base"], **case["gold"]}
    # Scoring ignores punctuation, so a cosmetic difference scores as "same".
    expected = "same" if case["tier"] == "cosmetic" else case["tier"]
    assert entry_level(got, want, compare_split=True) == expected


def test_norm_keeps_letters_and_digits_only():
    assert norm("2001-0001 (XxApp 1 Cir 1/2/03)") == "20010001XxApp1Cir1203"
    assert norm(None) == ""


def test_misread_needs_same_length_and_lookalike_characters():
    assert is_misread("918", "913")
    assert not is_misread("913", "913")
    assert not is_misread("91", "913")
    assert not is_misread("91x", "913")


def test_the_history_split_is_ignored_unless_asked_for():
    case = next(c for c in FIXTURE["cases"] if c["name"] == "history split differently")
    got = {**FIXTURE["base"], **case["model"]}
    want = {**FIXTURE["base"], **case["gold"]}
    assert entry_level(got, want) == "same"
