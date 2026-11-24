"""Tests for the scoring metrics.

python3 -m unittest test_score.py
"""

import unittest

import score


class Comparable(unittest.TestCase):
    def test_ignores_case_spacing_and_punctuation(self):
        self.assertEqual(
            score.comparable("In re: AMBER  SAENZ"),
            score.comparable("In re Amber Saenz"),
        )

    def test_keeps_different_words_apart(self):
        self.assertNotEqual(score.comparable("Baughn"), score.comparable("Baugbhn"))


class ScoreOne(unittest.TestCase):
    def test_match_and_strict(self):
        s = score.score_one("SMITH v. JONES", "Smith v. Jones")
        self.assertEqual((s["match"], s["strict"], s["returned"]), (1.0, 0.0, 1.0))

    def test_empty_gold_is_matched_by_empty_prediction(self):
        s = score.score_one("", "")
        self.assertEqual(
            (s["match"], s["word_overlap"], s["returned"]), (1.0, 1.0, 0.0)
        )

    def test_empty_prediction_misses_a_name(self):
        s = score.score_one(None, "Smith v. Jones")
        self.assertEqual((s["match"], s["word_overlap"]), (0.0, 0.0))

    def test_name_where_gold_is_empty_is_wrong(self):
        self.assertEqual(score.score_one("Smith v. Jones", "")["match"], 0.0)


class WordOverlap(unittest.TestCase):
    def test_same_words_score_one(self):
        self.assertEqual(score.word_overlap("Smith v. Jones", "smith v jones"), 1.0)

    def test_partial_credit(self):
        # 3 shared words; prediction has 3, gold has 5: precision 1, recall 0.6
        self.assertAlmostEqual(
            score.word_overlap("Smith v. Jones", "John Smith v. Mary Jones"), 0.75
        )

    def test_no_shared_words(self):
        self.assertEqual(score.word_overlap("Roe", "Doe"), 0.0)


class Segment(unittest.TestCase):
    def test_segments(self):
        gold = {"case_name_full": "A v. B", "flags": []}
        self.assertEqual(score.segment(gold), "answerable")
        gold["flags"] = ["caption_cut_off"]
        self.assertEqual(score.segment(gold), "beyond_2k")
        self.assertEqual(
            score.segment({"case_name_full": "", "flags": ["no_caption"]}),
            "no_case_name",
        )


if __name__ == "__main__":
    unittest.main()
