"""Synthetic tests for paginate_and_call + postprocess_offsets.

Hand-built tagged_artifacts and emissions exercise specific code paths:
chunking, tag-safe boundaries, single-opinion-per-call pagination, two-pass
merge (group_id + canonical), untagged-snippet validation, conflict
resolution, audit logs.

No Bedrock; pure local logic.

Run: python -m pytest test_phase2_synthetic.py -v
   or: python test_phase2_synthetic.py
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "citator-pipeline")
)

from utils.paginate_and_call import (  # noqa: E402
    CallInput,
    chunk_opinion,
    deserialize_calls,
    estimate_tokens,
    find_safe_boundary,
    make_record_id,
    parse_record_id,
    prepare_calls,
    serialize_calls,
)
from utils.postprocess_offsets import (  # noqa: E402
    _casename_in_maincit,
    _normalize_canonical,
    detect_casename_disagreements,
    merge_two_pass,
    postprocess,
    resolve_id_conflicts,
    resolve_merged,
    validate_call_emissions,
)

# batch_utils imports boto3 transitively; skip its tests if not available.
try:
    from utils.batch_utils import (  # noqa: E402
        CITATION_GROUPING_MAX_TOKENS,
        build_citation_grouping_record,
        extract_citation_grouping_emission,
    )
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


# ── Fixture helpers ──────────────────────────────────────────────

def tag(id_: int, group: str, text: str) -> str:
    return f'<cited id="{id_}" group="{group}">{text}</cited>'


def make_artifact(
    cluster_id: int,
    opinions: list[dict],
    id_to_metadata: dict,
    groups: dict,
) -> dict:
    return {
        "cluster_id": cluster_id,
        "opinions": opinions,
        "id_to_metadata_map": id_to_metadata,
        "groups": groups,
        "n_tags": len(id_to_metadata),
        "n_unmatched_misses": 0,
        "empty_html": [],
    }


# ── paginate_and_call tests ──────────────────────────────────────

class TestEstimateTokens(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_proportional(self):
        # 37 chars / 3.7 = 10 tokens
        self.assertEqual(estimate_tokens("a" * 37), 10)


class TestFindSafeBoundary(unittest.TestCase):
    def test_at_extremes(self):
        text = "abcdef"
        self.assertEqual(find_safe_boundary(text, 0), 0)
        self.assertEqual(find_safe_boundary(text, 6), 6)

    def test_paragraph_break_preferred(self):
        text = "first paragraph.\n\nsecond paragraph."
        # target near the \n\n boundary
        boundary = find_safe_boundary(text, len("first paragraph."))
        self.assertEqual(boundary, len("first paragraph.\n\n"))

    def test_skips_inside_tag(self):
        # \n\n inside a <cited> tag's text should NOT be chosen
        text = (
            "before "
            '<cited id="0" group="g0">case\n\nname here</cited>'
            " after.\n\nthe next paragraph."
        )
        # target near the in-tag \n\n
        target = text.find("name") + 2
        boundary = find_safe_boundary(text, target, max_walk=200)
        # Should land at the post-tag paragraph break, not the in-tag one
        post_tag_para = text.find("\n\nthe")
        self.assertEqual(boundary, post_tag_para + 2)

    def test_sentence_break_fallback(self):
        text = "no paragraph here. just sentences. another."
        boundary = find_safe_boundary(text, 20, max_walk=50)
        # Should land at a sentence break (period + space)
        self.assertIn(text[boundary - 2 : boundary], [". ", ".", "e."])


class TestChunkOpinion(unittest.TestCase):
    def test_short_opinion_one_chunk(self):
        text = "short opinion text with one <cited id=\"0\" group=\"g0\">cite</cited>."
        chunks = chunk_opinion(text, chunk_size_tokens=1000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0, text))

    def test_long_opinion_splits_with_balanced_tags(self):
        # Build a long text with many tags so chunking triggers.
        # ~ 50K chars, with ~100 cited tags spread out.
        paragraphs = []
        id_ = 0
        for i in range(200):
            tag_text = tag(id_, f"g{id_}", f"Case {id_} v. State")
            paragraphs.append(
                f"Paragraph {i}. We cite to {tag_text} as a starting point. "
                f"Then we discuss this for a while at length, padding to make "
                f"the paragraph longer than just a sentence so chunk math is meaningful."
            )
            id_ += 1
        text = "\n\n".join(paragraphs)
        # Force chunking with a small target size
        chunks = chunk_opinion(text, chunk_size_tokens=2000, overlap_tokens=200)
        self.assertGreater(len(chunks), 1, "expected multiple chunks for long text")
        for start, chunk_text in chunks:
            # Tag-safe: every <cited has a matching </cited within the chunk
            opens = chunk_text.count("<cited id=")
            closes = chunk_text.count("</cited>")
            self.assertEqual(opens, closes,
                             f"chunk starting at {start} has {opens} opens / {closes} closes")

    def test_overlap_produces_shared_content(self):
        # Build text where overlap region clearly shares content
        text = ("A. " * 1000 + "MIDDLE_MARKER. " + "B. " * 1000)
        chunks = chunk_opinion(text, chunk_size_tokens=300, overlap_tokens=50)
        if len(chunks) >= 2:
            # At least one boundary -> some overlap
            self.assertTrue(
                any(c[0] > 0 for c in chunks),
                "expected subsequent chunks to start past the beginning",
            )


class TestPrepareCalls(unittest.TestCase):
    def test_single_small_opinion_single_call(self):
        opinion = {
            "opinion_type": "020lead",
            "source_opinion_id": 1,
            "tagged_text": f"short text {tag(0, 'g0', 'Cite')} more.",
        }
        artifact = make_artifact(
            cluster_id=1, opinions=[opinion],
            id_to_metadata={"0": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 11, "citation_string": "Cite", "group_id": "g0"}},
            groups={"g0": [0]},
        )
        calls = prepare_calls(artifact)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].items), 1)
        item = calls[0].items[0]
        self.assertEqual(item["opinion_handle"], 0)
        self.assertEqual(item["chunk_index"], 1)
        self.assertEqual(item["total_chunks"], 1)
        # LLM input JSON shape
        llm_input = calls[0].to_llm_input()
        self.assertIn("opinions", llm_input)
        self.assertEqual(
            set(llm_input["opinions"][0].keys()),
            {"opinion_handle", "opinion_type", "chunk_index", "total_chunks", "tagged_text"},
        )

    def test_each_opinion_gets_its_own_call(self):
        """No cross-opinion packing — every opinion is its own single-opinion call."""
        opinions = [
            {"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": "lead text"},
            {"opinion_type": "040dissent", "source_opinion_id": 2, "tagged_text": "dissent text"},
        ]
        artifact = make_artifact(1, opinions, {}, {})
        calls = prepare_calls(artifact)
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(len(c.items), 1)
            self.assertEqual(c.items[0]["opinion_handle"], 0)
        types = [c.items[0]["opinion_type"] for c in calls]
        self.assertEqual(types, ["020lead", "040dissent"])

    def test_combined_and_non_combined_each_alone(self):
        """Combined never shares a call (trivially true under single-opinion rule)."""
        opinions = [
            {"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": "lead text"},
            {"opinion_type": "010combined", "source_opinion_id": 99, "tagged_text": "combined text"},
        ]
        artifact = make_artifact(1, opinions, {}, {})
        calls = prepare_calls(artifact)
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(len(c.items), 1)

    def test_long_opinion_gets_chunked_into_own_calls(self):
        """An opinion above MAX_OPINION_TOKENS (20K) chunks; each chunk = own call."""
        # 20K tokens × 3.7 chars/token = ~74K chars threshold.
        # Build text > 74K chars so chunking triggers.
        long_text = ("Para. " * 20000)  # ~120K chars → ~32K tokens, > 20K
        short_text = "Short dissent."
        opinions = [
            {"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": long_text},
            {"opinion_type": "040dissent", "source_opinion_id": 2, "tagged_text": short_text},
        ]
        artifact = make_artifact(1, opinions, {}, {})
        calls = prepare_calls(artifact)
        chunk_calls = [c for c in calls
                       if any(it["total_chunks"] > 1 for it in c.items)]
        self.assertGreaterEqual(len(chunk_calls), 1,
                                "long opinion must produce chunked calls")
        for c in chunk_calls:
            self.assertEqual(len(c.items), 1, "chunk must be alone in its call")
            self.assertEqual(c.items[0]["opinion_type"], "020lead")
        non_chunk_calls = [c for c in calls if c not in chunk_calls]
        self.assertEqual(len(non_chunk_calls), 1)
        self.assertEqual(
            [it["opinion_type"] for it in non_chunk_calls[0].items],
            ["040dissent"],
        )

    def test_no_cross_opinion_packing_with_many_short_opinions(self):
        """Five short opinions produce five calls (no FFD packing under 20K rule)."""
        small = "short opinion text. " * 20
        opinions = [
            {"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": small},
            {"opinion_type": "030concurrence", "source_opinion_id": 2, "tagged_text": small},
            {"opinion_type": "030concurrence", "source_opinion_id": 3, "tagged_text": small},
            {"opinion_type": "040dissent", "source_opinion_id": 4, "tagged_text": small},
            {"opinion_type": "010combined", "source_opinion_id": 99, "tagged_text": small},
        ]
        artifact = make_artifact(1, opinions, {}, {})
        calls = prepare_calls(artifact)
        self.assertEqual(len(calls), 5)
        for c in calls:
            self.assertEqual(len(c.items), 1)

    def test_each_combined_record_independent(self):
        """Multiple combined records each get their own call."""
        opinions = [
            {"opinion_type": "010combined", "source_opinion_id": 1, "tagged_text": "first combined"},
            {"opinion_type": "010combined", "source_opinion_id": 2, "tagged_text": "second combined"},
        ]
        artifact = make_artifact(1, opinions, {}, {})
        calls = prepare_calls(artifact)
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(len(c.items), 1)
            self.assertEqual(c.items[0]["opinion_type"], "010combined")


# ── postprocess_offsets tests ────────────────────────────────────

class TestNormalizeCanonical(unittest.TestCase):
    def test_collapses_whitespace_and_case(self):
        self.assertEqual(_normalize_canonical("Marbury v. Madison"),
                         _normalize_canonical("marbury  v.  madison"))

    def test_handles_none_and_empty(self):
        self.assertEqual(_normalize_canonical(None), "")
        self.assertEqual(_normalize_canonical(""), "")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(_normalize_canonical("Erie R.R., 304 U.S."),
                         _normalize_canonical("Erie R.R., 304 U.S"))


class TestValidateEmissions(unittest.TestCase):
    def _make_setup(self):
        chunk_text = (
            f"Body before {tag(0, 'g0', 'Erie R.R. Co. v. Tompkins, 304 U.S. 64')}. "
            f"Then says: as the Court explained in Brown v. Board, 347 U.S. 483, "
            f"the doctrine applies."
        )
        artifact = make_artifact(
            cluster_id=42,
            opinions=[{"opinion_type": "020lead", "source_opinion_id": 100, "tagged_text": chunk_text}],
            id_to_metadata={
                "0": {"opinion_type": "020lead", "source_opinion_id": 100, "offset": 12,
                      "citation_string": "Erie R.R. Co. v. Tompkins, 304 U.S. 64", "group_id": "g0"},
            },
            groups={"g0": [0]},
        )
        call = CallInput(
            cluster_id=42,
            items=[{
                "opinion_handle": 0, "source_opinion_id": 100, "opinion_type": "020lead",
                "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
                "tagged_text": chunk_text, "tokens": 50,
            }],
        )
        return artifact, call

    def test_fabricated_id_dropped_and_logged(self):
        artifact, call = self._make_setup()
        invalid_buf = io.StringIO()
        cited_cases = [{
            "mainCitationString": "Bogus v. Bogus, 1 U.S. 1 (1800)",
            "parallelCitationString": None,
            "caseName": "Bogus v. Bogus",
            "accepted_ids": [0, 9999],  # 9999 fabricated
            "untagged_occurrences": [],
            "ocr_corrected": False, "ocr_note": None,
        }]
        cleaned, counters = validate_call_emissions(
            cited_cases, call, artifact, invalid_ids_log=invalid_buf
        )
        self.assertEqual(cleaned[0]["accepted_ids"], [0])
        self.assertEqual(counters["n_invalid_ids"], 1)
        log_line = json.loads(invalid_buf.getvalue().strip())
        self.assertEqual(log_line["id"], 9999)
        self.assertEqual(log_line["reason"], "id_not_in_id_to_metadata_map")

    def test_verbatim_untagged_accepted(self):
        artifact, call = self._make_setup()
        untagged_buf = io.StringIO()
        snippet = "Brown v. Board, 347 U.S. 483"
        cited_cases = [{
            "mainCitationString": "Brown v. Board, 347 U.S. 483 (1954)",
            "parallelCitationString": "74 S.Ct. 686",
            "caseName": "Brown v. Board",
            "accepted_ids": [],
            "untagged_occurrences": [{"opinion_handle": 0, "snippet": snippet}],
            "ocr_corrected": False, "ocr_note": None,
        }]
        cleaned, counters = validate_call_emissions(
            cited_cases, call, artifact, untagged_log=untagged_buf
        )
        self.assertEqual(counters["n_untagged_accepted"], 1)
        self.assertEqual(counters["n_untagged_dropped"], 0)
        self.assertEqual(len(cleaned[0]["_validated_untagged"]), 1)
        v = cleaned[0]["_validated_untagged"][0]
        self.assertEqual(v["snippet"], snippet)
        # Offset matches: chunk_start=0, so offset = position in chunk_text
        self.assertEqual(v["offset"], call.items[0]["tagged_text"].find(snippet))

    def test_paraphrased_untagged_dropped(self):
        artifact, call = self._make_setup()
        untagged_buf = io.StringIO()
        cited_cases = [{
            "mainCitationString": None, "parallelCitationString": None, "caseName": None,
            "accepted_ids": [],
            "untagged_occurrences": [{"opinion_handle": 0, "snippet": "totally not in source"}],
            "ocr_corrected": False, "ocr_note": None,
        }]
        cleaned, counters = validate_call_emissions(
            cited_cases, call, artifact, untagged_log=untagged_buf
        )
        self.assertEqual(counters["n_untagged_dropped"], 1)
        log = json.loads(untagged_buf.getvalue().strip())
        self.assertEqual(log["outcome"], "dropped")
        self.assertEqual(log["drop_reason"], "snippet_not_found")

    def test_bad_handle_dropped(self):
        artifact, call = self._make_setup()
        untagged_buf = io.StringIO()
        cited_cases = [{
            "mainCitationString": None, "parallelCitationString": None, "caseName": None,
            "accepted_ids": [],
            "untagged_occurrences": [{"opinion_handle": 99, "snippet": "x"}],
            "ocr_corrected": False, "ocr_note": None,
        }]
        cleaned, counters = validate_call_emissions(
            cited_cases, call, artifact, untagged_log=untagged_buf
        )
        self.assertEqual(counters["n_untagged_dropped"], 1)
        self.assertEqual(json.loads(untagged_buf.getvalue().strip())["drop_reason"],
                         "invalid_opinion_handle")


class TestMergeTwoPass(unittest.TestCase):
    def test_pass1_group_id_merge(self):
        # Two cases that both contain ids from g0 → merged via Pass 1
        cases = [
            {"mainCitationString": "A v. A, 1 U.S. 1 (1900)", "parallelCitationString": None,
             "caseName": "A v. A", "accepted_ids": [0, 1],
             "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "A short", "parallelCitationString": None,
             "caseName": "A v. A", "accepted_ids": [2],
             "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
        ]
        id_map = {
            "0": {"group_id": "g0"}, "1": {"group_id": "g0"}, "2": {"group_id": "g0"},
        }
        groups = merge_two_pass(cases, id_map)
        self.assertEqual(len(groups), 1, "expected the two cases to merge")
        self.assertEqual(sorted(groups[0]), [0, 1])

    def test_pass2_canonical_merge(self):
        # Two cases with no overlap in group_ids but same canonical name
        cases = [
            {"mainCitationString": "Marbury v. Madison, 5 U.S. 137 (1803)",
             "parallelCitationString": None, "caseName": "Marbury v. Madison",
             "accepted_ids": [0], "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "marbury v. madison, 5 U.S. 137 (1803)",  # case-variant
             "parallelCitationString": None, "caseName": "Marbury v. Madison",
             "accepted_ids": [5], "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
        ]
        id_map = {"0": {"group_id": "g0"}, "5": {"group_id": "g7"}}  # different groups
        groups = merge_two_pass(cases, id_map)
        self.assertEqual(len(groups), 1)

    def test_no_merge_when_both_null(self):
        # Two cases with no accepted_ids and null canonical → don't merge each other
        cases = [
            {"mainCitationString": None, "parallelCitationString": None, "caseName": None,
             "accepted_ids": [], "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": None, "parallelCitationString": None, "caseName": None,
             "accepted_ids": [], "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
        ]
        groups = merge_two_pass(cases, {})
        self.assertEqual(len(groups), 2)

    def test_pass1_no_merge_when_casenames_differ(self):
        """Eyecite mis-chained two distinct cases into one Phase 1 group; model
        correctly split them. Pass 1 must NOT re-union them when their caseNames
        are clearly different. Regression test for the chimera bug."""
        cases = [
            {"mainCitationString": "Powell v. McCormack, 395 U.S. 486 (1969)",
             "parallelCitationString": None, "caseName": "Powell v. McCormack",
             "accepted_ids": [0], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "Nixon v. United States, 506 U.S. 224 (1993)",
             "parallelCitationString": None, "caseName": "Nixon v. United States",
             "accepted_ids": [1], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
        ]
        # Both ids share the same Phase 1 group (eyecite mis-chain).
        id_map = {"0": {"group_id": "g0"}, "1": {"group_id": "g0"}}
        groups = merge_two_pass(cases, id_map)
        self.assertEqual(len(groups), 2,
                         "cases with distinct caseNames must NOT merge by group_id")

    def test_pass1_merges_when_one_name_null(self):
        """Short-cite emission with null caseName (e.g., chunk only saw an Id.)
        should still merge with the named full-cite emission via Pass 1, because
        null is a wildcard."""
        cases = [
            {"mainCitationString": "Marbury v. Madison, 5 U.S. 137 (1803)",
             "parallelCitationString": None, "caseName": "Marbury v. Madison",
             "accepted_ids": [0], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": None, "parallelCitationString": None,
             "caseName": None, "accepted_ids": [3],
             "_validated_untagged": [], "ocr_corrected": False, "ocr_note": None},
        ]
        id_map = {"0": {"group_id": "g0"}, "3": {"group_id": "g0"}}
        groups = merge_two_pass(cases, id_map)
        self.assertEqual(len(groups), 1,
                         "null caseName is a wildcard; should merge with named sibling")


class TestResolveMerged(unittest.TestCase):
    def test_longer_wins(self):
        cases = [
            {"mainCitationString": "5 U.S. 137", "parallelCitationString": None,
             "caseName": "Marbury", "accepted_ids": [0], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "Marbury v. Madison, 5 U.S. 137 (1803)",
             "parallelCitationString": None, "caseName": "Marbury v. Madison",
             "accepted_ids": [3], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
        ]
        merged = resolve_merged([0, 1], cases)
        self.assertEqual(merged["mainCitationString"], "Marbury v. Madison, 5 U.S. 137 (1803)")
        self.assertEqual(merged["caseName"], "Marbury v. Madison")
        self.assertEqual(merged["accepted_ids"], [0, 3])

    def test_parallel_cite_longer_wins(self):
        cases = [
            {"mainCitationString": "Foo v. Bar, 1 U.S. 1 (1900)",
             "parallelCitationString": None, "caseName": "Foo v. Bar",
             "accepted_ids": [0], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "Foo v. Bar, 1 U.S. 1 (1900)",
             "parallelCitationString": "10 S.Ct. 100, 20 L.Ed. 200",
             "caseName": "Foo v. Bar",
             "accepted_ids": [5], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
        ]
        merged = resolve_merged([0, 1], cases)
        self.assertEqual(merged["parallelCitationString"], "10 S.Ct. 100, 20 L.Ed. 200")

    def test_ocr_or_combine(self):
        cases = [
            {"mainCitationString": "X", "parallelCitationString": None, "caseName": "Y",
             "accepted_ids": [], "_validated_untagged": [],
             "ocr_corrected": True, "ocr_note": "fixed spacing"},
            {"mainCitationString": "X", "parallelCitationString": None, "caseName": "Y",
             "accepted_ids": [], "_validated_untagged": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "X", "parallelCitationString": None, "caseName": "Y",
             "accepted_ids": [], "_validated_untagged": [],
             "ocr_corrected": True, "ocr_note": "fixed case"},
        ]
        merged = resolve_merged([0, 1, 2], cases)
        self.assertTrue(merged["ocr_corrected"])
        self.assertEqual(merged["ocr_note"], "fixed spacing; fixed case")

    def test_untagged_dedup_by_opinion_and_snippet(self):
        cases = [
            {"mainCitationString": None, "parallelCitationString": None, "caseName": None,
             "accepted_ids": [],
             "_validated_untagged": [
                 {"source_opinion_id": 1, "snippet": "snip", "opinion_handle": 0,
                  "opinion_type": "020lead", "offset": 5},
             ],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": None, "parallelCitationString": None, "caseName": None,
             "accepted_ids": [],
             "_validated_untagged": [
                 # same (opinion, snippet) — duplicate, should drop
                 {"source_opinion_id": 1, "snippet": "snip", "opinion_handle": 0,
                  "opinion_type": "020lead", "offset": 5},
                 # different snippet — kept
                 {"source_opinion_id": 1, "snippet": "other", "opinion_handle": 0,
                  "opinion_type": "020lead", "offset": 50},
             ],
             "ocr_corrected": False, "ocr_note": None},
        ]
        merged = resolve_merged([0, 1], cases)
        self.assertEqual(len(merged["_validated_untagged"]), 2)


class TestEndToEndPostprocess(unittest.TestCase):
    def test_combined_dedup_via_group_id(self):
        """Case cited in a split lead AND in 010combined; Phase 1 gave both the
        same group_id (since they share a CL data-id); Pass 1 must merge."""
        # Build artifact: two opinions (lead + combined), each citing the same case.
        # In Phase 1, both spans would share the same data-id → same group_id g0.
        lead_text = f"Lead text {tag(0, 'g0', 'Erie, 304 U.S. 64')} body."
        comb_text = f"Combined text {tag(1, 'g0', 'Erie, 304 U.S. 64')} body."
        artifact = make_artifact(
            cluster_id=99,
            opinions=[
                {"opinion_type": "020lead", "source_opinion_id": 10, "tagged_text": lead_text},
                {"opinion_type": "010combined", "source_opinion_id": 11, "tagged_text": comb_text},
            ],
            id_to_metadata={
                "0": {"opinion_type": "020lead", "source_opinion_id": 10, "offset": 10,
                      "citation_string": "Erie, 304 U.S. 64", "group_id": "g0"},
                "1": {"opinion_type": "010combined", "source_opinion_id": 11, "offset": 14,
                      "citation_string": "Erie, 304 U.S. 64", "group_id": "g0"},
            },
            groups={"g0": [0, 1]},
        )
        call0 = CallInput(cluster_id=99, items=[
            {"opinion_handle": 0, "source_opinion_id": 10, "opinion_type": "020lead",
             "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
             "tagged_text": lead_text, "tokens": 10},
            {"opinion_handle": 1, "source_opinion_id": 11, "opinion_type": "010combined",
             "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
             "tagged_text": comb_text, "tokens": 10},
        ])
        # Pretend model emitted two cited_cases — one for lead's id 0, one for combined's id 1.
        # Postprocess should merge them via Pass 1 (shared group_id g0).
        emission = {"cited_cases": [
            {"mainCitationString": "Erie R.R. Co. v. Tompkins, 304 U.S. 64 (1938)",
             "parallelCitationString": None,
             "caseName": "Erie R.R. Co. v. Tompkins",
             "accepted_ids": [0], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "Erie R.R. Co. v. Tompkins, 304 U.S. 64 (1938)",
             "parallelCitationString": None,
             "caseName": "Erie R.R. Co. v. Tompkins",
             "accepted_ids": [1], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
        ]}
        result = postprocess(artifact, [call0], [emission])
        self.assertEqual(result["n_cases"], 1)
        self.assertEqual(result["n_occurrences"], 2)
        case = result["cited_cases"][0]
        # Both occurrences kept, in stable order
        types = [o["opinion_type"] for o in case["occurrences"]]
        self.assertIn("020lead", types)
        self.assertIn("010combined", types)

    def test_id_conflict_winner_has_more_occurrences(self):
        """Same id assigned to two genuinely-different cases (Pass 1+2 don't
        merge, because caseNames differ). The case with more occurrences
        keeps the id; the loser is stripped."""
        # Two chunks of one opinion disagree about id 42:
        #   Chunk A: id 42 belongs to Talbot (alongside 10, 11 — more context)
        #   Chunk B: id 42 belongs to Marbury (alone)
        text = (
            f"Body {tag(10, 'g0', 'Talbot v. Henning, 500 U.S. 412')} a "
            f"{tag(11, 'g0', 'Talbot, supra')} b "
            f"{tag(42, 'g0', 'Id.')} c "
            f"{tag(50, 'g1', 'Marbury v. Madison, 5 U.S. 137')} d."
        )
        artifact = make_artifact(
            cluster_id=200,
            opinions=[{"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": text}],
            id_to_metadata={
                "10": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 5,
                       "citation_string": "Talbot v. Henning, 500 U.S. 412", "group_id": "g0"},
                "11": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 60,
                       "citation_string": "Talbot, supra", "group_id": "g0"},
                "42": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 100,
                       "citation_string": "Id.", "group_id": "g0"},
                "50": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 140,
                       "citation_string": "Marbury v. Madison, 5 U.S. 137", "group_id": "g1"},
            },
            groups={"g0": [10, 11, 42], "g1": [50]},
        )
        # Simulate two chunks of the same opinion that overlap.
        call_a = CallInput(cluster_id=200, items=[
            {"opinion_handle": 0, "source_opinion_id": 1, "opinion_type": "020lead",
             "chunk_index": 1, "total_chunks": 2, "chunk_start": 0,
             "tagged_text": text, "tokens": 50},
        ])
        call_b = CallInput(cluster_id=200, items=[
            {"opinion_handle": 0, "source_opinion_id": 1, "opinion_type": "020lead",
             "chunk_index": 2, "total_chunks": 2, "chunk_start": 0,
             "tagged_text": text, "tokens": 50},
        ])
        # Chunk A: Talbot owns ids 10, 11, 42 (and a separate Marbury for id 50).
        # Note: id 50 is in group g1, so giving it to Talbot would let Pass 1's
        # group-id check still keep Talbot and Marbury separate (which is what
        # we want for this test).
        emission_a = {"cited_cases": [
            {"mainCitationString": "Talbot v. Henning, 500 U.S. 412 (1991)",
             "parallelCitationString": None, "caseName": "Talbot v. Henning",
             "accepted_ids": [10, 11, 42], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
            {"mainCitationString": "Marbury v. Madison, 5 U.S. 137 (1803)",
             "parallelCitationString": None, "caseName": "Marbury v. Madison",
             "accepted_ids": [50], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
        ]}
        # Chunk B disagrees about id 42 — assigns it to Marbury.
        emission_b = {"cited_cases": [
            {"mainCitationString": "Marbury v. Madison, 5 U.S. 137 (1803)",
             "parallelCitationString": None, "caseName": "Marbury v. Madison",
             "accepted_ids": [42, 50], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
        ]}
        conflicts_buf = io.StringIO()
        result = postprocess(
            artifact, [call_a, call_b], [emission_a, emission_b],
            id_conflicts_log=conflicts_buf,
        )
        self.assertEqual(result["n_id_conflicts"], 1)
        # id 42 should land with Talbot (3 occurrences) not Marbury (1 occurrence).
        case_by_name = {c["caseName"]: c for c in result["cited_cases"]}
        talbot_ids = [o for o in case_by_name["Talbot v. Henning"]["occurrences"]]
        talbot_offsets = {o["offset"] for o in talbot_ids}
        # id 42's offset is 100 per id_to_metadata
        self.assertIn(100, talbot_offsets)
        marbury_offsets = {o["offset"] for o in case_by_name["Marbury v. Madison"]["occurrences"]}
        self.assertNotIn(100, marbury_offsets)
        # Log line for the conflict
        line = json.loads(conflicts_buf.getvalue().strip())
        self.assertEqual(line["id"], 42)
        self.assertEqual(line["winner"]["caseName"], "Talbot v. Henning")

    def test_unrouted_ids_fall_back_to_solo_cases(self):
        """Model omits ids whose group_ids have no sibling in any cited_case
        → each gets a solo case with null names; still logged as unrouted."""
        text = (f"a {tag(0, 'g0', 'A')} b {tag(1, 'g1', 'B')} c {tag(2, 'g2', 'C')} d.")
        artifact = make_artifact(
            cluster_id=42,
            opinions=[{"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": text}],
            id_to_metadata={
                "0": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 2,
                      "citation_string": "A", "group_id": "g0"},
                "1": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 30,
                      "citation_string": "B", "group_id": "g1"},
                "2": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 58,
                      "citation_string": "C", "group_id": "g2"},
            },
            groups={"g0": [0], "g1": [1], "g2": [2]},
        )
        call = CallInput(cluster_id=42, items=[
            {"opinion_handle": 0, "source_opinion_id": 1, "opinion_type": "020lead",
             "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
             "tagged_text": text, "tokens": 20},
        ])
        emission = {"cited_cases": [
            {"mainCitationString": "A v. A, 1 U.S. 1 (1900)",
             "parallelCitationString": None,
             "caseName": "A v. A",
             "accepted_ids": [0], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
        ]}
        unrouted_buf = io.StringIO()
        result = postprocess(artifact, [call], [emission], unrouted_log=unrouted_buf)
        self.assertEqual(result["n_unrouted_ids"], 2)
        # Each unrouted id became its own solo case (null names) — total 3 cases.
        self.assertEqual(result["n_cases"], 3)
        self.assertEqual(result["n_occurrences"], 3)
        solo_cases = [c for c in result["cited_cases"] if c["mainCitationString"] is None]
        self.assertEqual(len(solo_cases), 2)
        # Log records fallback kind
        lines = unrouted_buf.getvalue().strip().split("\n")
        unrouted_entries = {json.loads(line)["id"]: json.loads(line) for line in lines}
        self.assertEqual(set(unrouted_entries.keys()), {1, 2})
        for entry in unrouted_entries.values():
            self.assertEqual(entry["fallback"], "solo_from_phase1")

    def test_unrouted_id_attaches_to_group_sibling(self):
        """Model omits id 5 but routes id 3 to Talbot; both share group_id g0.
        Fallback attaches id 5 to Talbot's case rather than creating a solo."""
        text = (
            f"a {tag(3, 'g0', 'Talbot, 500 U.S. 412')} b "
            f"{tag(5, 'g0', 'Id.')} c."
        )
        artifact = make_artifact(
            cluster_id=42,
            opinions=[{"opinion_type": "020lead", "source_opinion_id": 1, "tagged_text": text}],
            id_to_metadata={
                "3": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 2,
                      "citation_string": "Talbot, 500 U.S. 412", "group_id": "g0"},
                "5": {"opinion_type": "020lead", "source_opinion_id": 1, "offset": 60,
                      "citation_string": "Id.", "group_id": "g0"},
            },
            groups={"g0": [3, 5]},
        )
        call = CallInput(cluster_id=42, items=[
            {"opinion_handle": 0, "source_opinion_id": 1, "opinion_type": "020lead",
             "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
             "tagged_text": text, "tokens": 20},
        ])
        # Model routes id 3 but omits id 5.
        emission = {"cited_cases": [
            {"mainCitationString": "Talbot v. Henning, 500 U.S. 412 (1991)",
             "parallelCitationString": None, "caseName": "Talbot v. Henning",
             "accepted_ids": [3], "untagged_occurrences": [],
             "ocr_corrected": False, "ocr_note": None},
        ]}
        unrouted_buf = io.StringIO()
        result = postprocess(artifact, [call], [emission], unrouted_log=unrouted_buf)
        self.assertEqual(result["n_unrouted_ids"], 1)
        # Single case still; id 5 attached to Talbot via group_id sibling.
        self.assertEqual(result["n_cases"], 1)
        case = result["cited_cases"][0]
        self.assertEqual(case["caseName"], "Talbot v. Henning")
        offsets = {o["offset"] for o in case["occurrences"]}
        self.assertEqual(offsets, {2, 60})
        # Log records the fallback kind
        line = json.loads(unrouted_buf.getvalue().strip())
        self.assertEqual(line["id"], 5)
        self.assertEqual(line["fallback"], "group_id_sibling")


# ── caseName double-check ───────────────────────────────────────

class TestCasenameInMaincit(unittest.TestCase):
    def test_substring_ok(self):
        self.assertTrue(_casename_in_maincit(
            "Marbury v. Madison",
            "Marbury v. Madison, 5 U.S. 137 (1803)"))

    def test_substring_ok_with_ocr_variant(self):
        self.assertTrue(_casename_in_maincit(
            "Marbury v. Madison",
            "Marbury  v. Madison , 5 U. S. 137 (1803)"))

    def test_mismatch_detected(self):
        self.assertFalse(_casename_in_maincit(
            "Brown v. Board",
            "Marbury v. Madison, 5 U.S. 137 (1803)"))

    def test_nulls_treated_as_ok(self):
        self.assertTrue(_casename_in_maincit(None, "anything"))
        self.assertTrue(_casename_in_maincit("anything", None))
        self.assertTrue(_casename_in_maincit(None, None))

    def test_mismatch_logged_during_validation(self):
        artifact = make_artifact(
            cluster_id=1, opinions=[],
            id_to_metadata={"0": {"opinion_type": "020lead", "source_opinion_id": 1,
                                   "offset": 0, "citation_string": "X", "group_id": "g0"}},
            groups={"g0": [0]},
        )
        call = CallInput(cluster_id=1, items=[{
            "opinion_handle": 0, "source_opinion_id": 1, "opinion_type": "020lead",
            "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
            "tagged_text": "x", "tokens": 1,
        }])
        cn_buf = io.StringIO()
        cited_cases = [{
            "mainCitationString": "Marbury v. Madison, 5 U.S. 137 (1803)",
            "parallelCitationString": None,
            "caseName": "Brown v. Board",  # disagrees with mainCit
            "accepted_ids": [0], "untagged_occurrences": [],
            "ocr_corrected": False, "ocr_note": None,
        }]
        cleaned, counters = validate_call_emissions(
            cited_cases, call, artifact, casename_warnings_log=cn_buf
        )
        self.assertEqual(counters["n_casename_warnings"], 1)
        log_line = json.loads(cn_buf.getvalue().strip())
        self.assertEqual(log_line["caseName"], "Brown v. Board")
        self.assertEqual(log_line["kind"], "casename_not_in_maincit")
        self.assertIn("not a substring", log_line["message"])


class TestCasenameDisagreementDetector(unittest.TestCase):
    def test_disagreement_flagged(self):
        cases = [
            {"mainCitationString": "X, 1 U.S. 1", "caseName": "Foo v. Bar"},
            {"mainCitationString": "X, 1 U.S. 1", "caseName": "Baz v. Qux"},
        ]
        warnings = detect_casename_disagreements([[0, 1]], cases)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(sorted(warnings[0]["caseNames"]), ["Baz v. Qux", "Foo v. Bar"])

    def test_agreement_no_flag(self):
        cases = [
            {"mainCitationString": "X, 1 U.S. 1", "caseName": "Foo v. Bar"},
            {"mainCitationString": "X, 1 U.S. 1", "caseName": "foo v. bar"},  # OCR variant
        ]
        warnings = detect_casename_disagreements([[0, 1]], cases)
        self.assertEqual(len(warnings), 0)

    def test_solo_group_no_flag(self):
        cases = [{"mainCitationString": "X", "caseName": "Foo"}]
        self.assertEqual(detect_casename_disagreements([[0]], cases), [])

    def test_one_null_no_flag(self):
        cases = [
            {"mainCitationString": "X", "caseName": "Foo v. Bar"},
            {"mainCitationString": "X", "caseName": None},
        ]
        self.assertEqual(detect_casename_disagreements([[0, 1]], cases), [])


# ── Batch path: record id + CallInput serialization (no boto3 needed) ──

class TestRecordId(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(make_record_id(112786, 0), "112786_call_0")
        self.assertEqual(parse_record_id("112786_call_0"), (112786, 0))
        self.assertEqual(parse_record_id(make_record_id(9557725, 42)), (9557725, 42))

    def test_returns_none_for_malformed(self):
        self.assertIsNone(parse_record_id("garbage"))
        self.assertIsNone(parse_record_id("no_call_marker"))
        self.assertIsNone(parse_record_id(""))
        self.assertIsNone(parse_record_id("abc_call_def"))  # non-int parts

    def test_separator_choice_robust(self):
        # The "_call_" sentinel must be unambiguous even if the integer cluster
        # id is large or has many digits.
        self.assertEqual(parse_record_id("9999999999_call_999"), (9999999999, 999))


class TestSerializeCalls(unittest.TestCase):
    def test_round_trip_preserves_items(self):
        items = [
            {"opinion_handle": 0, "source_opinion_id": 100, "opinion_type": "020lead",
             "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
             "tagged_text": "lead text", "tokens": 5},
            {"opinion_handle": 1, "source_opinion_id": 101, "opinion_type": "040dissent",
             "chunk_index": 1, "total_chunks": 1, "chunk_start": 0,
             "tagged_text": "dissent text", "tokens": 4},
        ]
        calls = [CallInput(cluster_id=42, items=items)]
        serialized = serialize_calls(calls)
        # Must be JSON-clean (no dataclass instances leaking).
        as_json = json.dumps(serialized)
        roundtripped = deserialize_calls(json.loads(as_json))
        self.assertEqual(len(roundtripped), 1)
        self.assertEqual(roundtripped[0].cluster_id, 42)
        self.assertEqual(roundtripped[0].items, items)
        # meta(handle) and to_llm_input() should work post-round-trip.
        self.assertEqual(roundtripped[0].meta(1)["opinion_type"], "040dissent")
        llm = roundtripped[0].to_llm_input()
        self.assertEqual(len(llm["opinions"]), 2)


# ── Batch path: build_record + extract_emission (need batch_utils → boto3) ──

@unittest.skipUnless(BOTO3_AVAILABLE, "batch_utils requires boto3")
class TestBuildCitationGroupingRecord(unittest.TestCase):
    def test_record_shape(self):
        llm_input = {"opinions": [
            {"opinion_handle": 0, "opinion_type": "020lead", "chunk_index": 1,
             "total_chunks": 1, "tagged_text": "hello"}
        ]}
        rec = build_citation_grouping_record(
            record_id="42_call_0",
            llm_input=llm_input,
            system_prompt="SYS PROMPT TEXT",
        )
        self.assertEqual(rec["recordId"], "42_call_0")
        mi = rec["modelInput"]
        self.assertEqual(mi["system"], "SYS PROMPT TEXT")
        self.assertEqual(mi["max_tokens"], CITATION_GROUPING_MAX_TOKENS)
        # tool config
        self.assertEqual(len(mi["tools"]), 1)
        self.assertEqual(mi["tools"][0]["name"], "group_citations_by_case")
        self.assertEqual(mi["tool_choice"], {"type": "tool", "name": "group_citations_by_case"})
        # message has the JSON-stringified input as user text (no XML wrapping).
        msg_text = mi["messages"][0]["content"][0]["text"]
        self.assertEqual(json.loads(msg_text), llm_input)


@unittest.skipUnless(BOTO3_AVAILABLE, "batch_utils requires boto3")
class TestExtractEmission(unittest.TestCase):
    def _tool_use_block(self, payload):
        return {"type": "tool_use", "id": "tu_1", "name": "group_citations_by_case",
                "input": payload}

    def test_extracts_tool_input(self):
        payload = {"cited_cases": [{
            "mainCitationString": "X v. Y, 1 U.S. 1 (1900)",
            "parallelCitationString": None,
            "caseName": "X v. Y",
            "accepted_ids": [0], "untagged_occurrences": [],
            "ocr_corrected": False, "ocr_note": None,
        }]}
        model_output = {"content": [self._tool_use_block(payload)]}
        self.assertEqual(extract_citation_grouping_emission(model_output), payload)

    def test_returns_empty_dict_when_no_tool_use(self):
        model_output = {"content": [{"type": "text", "text": "no tool"}]}
        self.assertEqual(extract_citation_grouping_emission(model_output), {})

    def test_returns_empty_dict_for_none_or_empty(self):
        self.assertEqual(extract_citation_grouping_emission(None), {})
        self.assertEqual(extract_citation_grouping_emission({}), {})
        self.assertEqual(extract_citation_grouping_emission({"content": []}), {})

    def test_picks_tool_use_amongst_mixed_blocks(self):
        payload = {"cited_cases": []}
        model_output = {"content": [
            {"type": "text", "text": "model thinking…"},
            self._tool_use_block(payload),
            {"type": "text", "text": "trailing prose"},
        ]}
        self.assertEqual(extract_citation_grouping_emission(model_output), payload)


if __name__ == "__main__":
    unittest.main()
