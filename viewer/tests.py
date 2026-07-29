"""Viewer tests run against small SYNTHETIC data trees (no dependency on
the distributed data bundle, so they pass in CI). Each TestCase builds its
own tree in setUpClass and points settings at it."""

import io
import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

import fitz
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from PIL import Image

from pipeline.core import assemble, config
from pipeline.core.artifacts import SCHEMA_VERSION
from pipeline.core.normalize import registry_hash
from pipeline.engines import lighton
from pipeline.routes import resolve
from pipeline.run import run_route
from viewer import data
from viewer.views import _diff_flags

_ARTIFACT: dict = {
    "schema_version": SCHEMA_VERSION,
    "dataset": "tiny",
    "page": "rep.1.1__p0",
    "route": "dots+mistral+lighton",
    "tiebreak": "lighton",
    "stages": {
        "render": {
            "png": "datasets/tiny/page_png/rep.1.1__p0.png",
            "size": [1700, 2200],
            "n_redaction_rects": 0,
        },
        "layout": {
            "engine": "container_yolo",
            "raw": [],
            "post": [
                {
                    "label": "column",
                    "bbox": [100, 100, 800, 2000],
                    "confidence": 0.95,
                }
            ],
            "columns": [
                {
                    "side": "L",
                    "bbox": [100, 100, 800, 2000],
                    "confidence": 0.95,
                }
            ],
            "stats": {},
        },
        "main_ocr": {
            "engine": "dots",
            "blocks": [
                {
                    "id": 0,
                    "order": 0,
                    "label": "Text",
                    "bbox": [100, 100, 800, 200],
                    "text": "hello world",
                }
            ],
        },
        "supplementals": [
            {
                "engine": "mistral",
                "unit": "block",
                "missing": False,
                "blocks": [
                    {
                        "id": 0,
                        "type": "text",
                        "bbox": [100, 100, 800, 200],
                        "text": "hello world",
                    }
                ],
            }
        ],
        "reconstruct": {
            "main": {
                "engine": "dots",
                "order": [0],
                "items": [
                    {
                        "id": 0,
                        "role": "content",
                        "band": "body",
                        "column": "L",
                        "text": "Hello world",
                        "html": "hello <em>world</em>",
                    }
                ],
                "text": "Hello world",
            },
            "supplementals": [
                {
                    "engine": "mistral",
                    "unit": "block",
                    "order": [0],
                    "items": [
                        {
                            "id": 0,
                            "role": "content",
                            "band": "body",
                            "column": "L",
                            "text": "hello world",
                            "html": "hello <em>world</em>",
                        }
                    ],
                    "text": "hello world",
                }
            ],
        },
        "normalize": {
            "registry": {
                # the LIVE hash: ensure_artifact treats a stale registry
                # hash as a rebuild trigger, and this synthetic tree
                # cannot rebuild (no redacted sources)
                "hash": registry_hash(),
                "rules": [
                    {
                        "name": "sample-rule",
                        "layer": "shared",
                        "applies_to": "key",
                        "description": "synthetic rule for view tests",
                        "n_examples": 1,
                    }
                ],
            },
            "main": {
                "engine": "dots",
                "tokens": [
                    {
                        "display": "Hello",
                        "key": "hello",
                        "item": 0,
                        "span": [0, 5],
                    },
                    {
                        "display": "world",
                        "key": "world",
                        "item": 0,
                        "span": [6, 11],
                    },
                ],
                "rules": [
                    {
                        "rule": "sample-rule",
                        "applied": True,
                        "n_changed": 1,
                        "changes": [
                            {
                                "at": 0,
                                "before": [
                                    {"display": "Hello", "key": "Hello"}
                                ],
                                "after": [
                                    {"display": "Hello", "key": "hello"}
                                ],
                            }
                        ],
                    }
                ],
            },
            "supplementals": [
                {
                    "engine": "mistral",
                    "unit": "block",
                    "tokens": [
                        {
                            "display": "hello",
                            "key": "hello",
                            "item": 0,
                            "span": [0, 5],
                        },
                        # the token the fixture's dispute substitutes
                        # into the final (verdict: supp1)
                        {
                            "display": "word",
                            "key": "word",
                            "item": 0,
                            "span": [6, 10],
                        },
                    ],
                    "rules": [
                        {
                            "rule": "sample-rule",
                            "applied": True,
                            "n_changed": 0,
                            "changes": [],
                        }
                    ],
                }
            ],
        },
        "compare": {
            "params": {
                "gate_min_chars": 10,
                "tail_agree_chars": 12,
                "anchor_start": 2,
                "skip_roles": ["heading", "page_number"],
            },
            "streams": [
                {"label": "main", "engine": "dots"},
                {"label": "supp1", "engine": "mistral", "unit": "block"},
            ],
            "degraded": [],
            "disputes": [
                {
                    "id": 0,
                    "main_span": [1, 2],
                    "blocks": [0],
                    "roles": ["content"],
                    "spans": {"supp1": [1, 2]},
                    "reads": {
                        "main": "world",
                        "supp1": "word",
                        "lighton": "word",
                    },
                    "displays": {
                        "main": "world",
                        "supp1": "word",
                        "lighton": "word",
                    },
                    "verdict": "supp1",
                    "resolution": "majority",
                    "reason": None,
                    "low_confidence": False,
                    "high_risk": False,
                    "tiebreak": {
                        "bboxes": [[100, 100, 800, 200]],
                        "cached": True,
                        "accepted": True,
                        "accepted_by": "main",
                        "located": True,
                        "read": "hello word",
                        "keys": ["word"],
                        "display": "word",
                    },
                }
            ],
            "marks": [
                {
                    "label": "supp1",
                    "main": ["4"],
                    "supp": [],
                    "agree": False,
                    "diffs": [{"main": ["4"], "supp": []}],
                }
            ],
            "metrics": {
                "main_units": 2,
                "disputed_units": 1,
                "n_disputes": 1,
                "by_resolution": {"majority": 1},
                "by_reason": {},
                "n_low_confidence": 0,
                "n_high_risk": 0,
                "tiebreak": {
                    "attempted": 1,
                    "cached": 1,
                    "accepted": 1,
                    "voted": 1,
                },
            },
        },
    },
}
# the assemble record is the REAL pipeline function over the fixture's
# stages, so the fixture can never drift from what run.py writes
_ARTIFACT["stages"]["assemble"] = assemble.assemble_page(
    _ARTIFACT["stages"]["normalize"],
    _ARTIFACT["stages"]["compare"],
    _ARTIFACT["stages"]["reconstruct"],
    _ARTIFACT["stages"]["main_ocr"]["blocks"],
)


class DataTreeTestCase(SimpleTestCase):
    """Base: a per-class tmp data tree, settings pointed at it."""

    tmp: Path

    @classmethod
    def build_tree(cls, tmp: Path) -> None:
        raise NotImplementedError

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp(prefix="extraction-tests-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.build_tree(cls.tmp)
        cls.enterClassContext(
            override_settings(
                DATA_ROOT=cls.tmp,
                DATASETS_ROOT=cls.tmp / "datasets",
                ARTIFACTS_ROOT=cls.tmp / "artifacts",
                WEIGHTS_ROOT=cls.tmp / "weights",
            )
        )


class ViewerViewsTest(DataTreeTestCase):
    """View tests over a hand-written artifact fixture."""

    @classmethod
    def build_tree(cls, tmp: Path) -> None:
        ds = tmp / "datasets" / "tiny"
        (ds / "page_png").mkdir(parents=True)
        for engine in ("dots", "mistral"):
            d = ds / "engines" / engine
            d.mkdir(parents=True)
            (d / "rep.1.1__p0.json").write_text('{"raw": "engine file"}')
        Image.new("RGB", (17, 22), "white").save(
            ds / "page_png" / "rep.1.1__p0.png"
        )
        # the crop image behind the fixture's tiebreak dispute
        crops = ds / "engines" / "lighton_crops"
        crops.mkdir(parents=True)
        Image.new("RGB", (7, 1), "white").save(
            crops / "rep.1.1__p0_100_100_800_200.png"
        )
        art = tmp / "artifacts" / "tiny" / "dots+mistral+lighton"
        art.mkdir(parents=True)
        (art / "rep.1.1__p0.json").write_text(json.dumps(_ARTIFACT))
        # a second page whose artifact is a STALE schema version
        stale = dict(_ARTIFACT, page="rep.1.1__p9", schema_version=0)
        (art / "rep.1.1__p9.json").write_text(json.dumps(stale))
        # extra renders exercise NATURAL page ordering (p2 before p10)
        for extra in ("rep.1.1__p2", "rep.1.1__p10"):
            Image.new("RGB", (17, 22), "white").save(
                ds / "page_png" / f"{extra}.png"
            )
        # and a page whose artifact carries a stale REGISTRY hash
        aged = json.loads(json.dumps(_ARTIFACT))
        aged["page"] = "rep.1.1__p8"
        aged["stages"]["normalize"]["registry"]["hash"] = "000000000000"
        (art / "rep.1.1__p8.json").write_text(json.dumps(aged))
        # a second route whose one dispute is HIGH RISK (feeds the
        # review page + its own stats row)
        hr = json.loads(json.dumps(_ARTIFACT))
        hr["route"] = "dots+surya_block+lighton"
        hr_cmp = hr["stages"]["compare"]
        hr_cmp["streams"][1] = {
            "label": "supp1",
            "engine": "surya",
            "unit": "block",
        }
        hr_cmp["disputes"] = [
            {
                "id": 0,
                "main_span": [0, 1],
                "blocks": [0],
                "roles": ["content"],
                "spans": {"supp1": [0, 1]},
                "reads": {"main": "hello", "supp1": "goodbye friend"},
                "displays": {"main": "Hello", "supp1": "goodbye friend"},
                "verdict": "main",
                "resolution": "fallback",
                "reason": "no-cached-read",
                "low_confidence": True,
                "high_risk": True,
            }
        ]
        hr_cmp["metrics"] = {
            "main_units": 2,
            "disputed_units": 1,
            "n_disputes": 1,
            "by_resolution": {"fallback": 1},
            "by_reason": {"no-cached-read": 1},
            "n_low_confidence": 1,
            "n_high_risk": 1,
            "tiebreak": {
                "attempted": 1,
                "cached": 0,
                "accepted": 0,
                "voted": 0,
            },
        }
        # this route's main styles its first unit — the same text as the
        # other route, so route-compare sees a STYLING diff there
        hr["stages"]["normalize"]["main"]["tokens"][0]["styling"] = ["em"]
        hr["stages"]["assemble"] = assemble.assemble_page(
            hr["stages"]["normalize"],
            hr_cmp,
            hr["stages"]["reconstruct"],
            hr["stages"]["main_ocr"]["blocks"],
        )
        hr_art = tmp / "artifacts" / "tiny" / "dots+surya_block+lighton"
        hr_art.mkdir(parents=True)
        (hr_art / "rep.1.1__p0.json").write_text(json.dumps(hr))

    def test_home_is_a_door_to_the_flow(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open the pipeline flow")
        self.assertContains(response, "/go/")
        # dataset/model/page SELECTION lives on the flow page — home
        # only carries hidden fields that restore the last-used combo
        self.assertNotContains(response, "<select")
        self.assertContains(response, 'data-restore="true"')

    def test_home_route_stats_cover_every_variant(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, "Route stats — tiny")
        # three engine columns, no combined route-name column
        self.assertContains(response, "1st engine")
        self.assertContains(response, "2nd engine")
        self.assertContains(response, "3rd engine / tiebreaker")
        self.assertContains(response, "high risk")
        # explicit denominator + main-bbox context columns
        self.assertContains(response, "disputes")
        self.assertContains(response, "main bboxes")
        # the built route aggregates its one page as counts out of its
        # disputes: 1/1 majority, 0/1 everywhere else (+ the high-risk
        # fixture route's majority + reorders cells)
        self.assertContains(response, "dots+mistral+lighton")  # row link
        self.assertContains(response, "1/1")
        self.assertContains(response, "0/1", count=6)
        self.assertContains(response, "reorders")
        # every reviewable column header links to its review page
        for category in ("reorders", "rejected", "low-conf", "high-risk"):
            self.assertContains(response, f"/review/tiny/{category}/")
        # the flow link is its own button column (one per row: the 3
        # dots+lighton tiebreaks + all 4 vote trios)
        self.assertContains(response, ">flow</a>", count=7)
        # lighton combinations without dots are gone (no cached crops)
        self.assertNotContains(response, "mistral+gemini+lighton")
        self.assertNotContains(response, "surya_block+gemini+lighton")
        # unbuilt variants still get a row
        self.assertContains(response, "dots+gemini+lighton")
        self.assertContains(response, "not built yet")
        # only UNIQUE combinations appear — never an ordering twin
        self.assertContains(response, "dots+mistral+surya_block")
        self.assertNotContains(response, "dots+surya_block+mistral")
        self.assertContains(response, "dots+gemini+surya_block")
        self.assertNotContains(response, "surya_block+gemini+dots")

    def test_home_route_stats_order(self) -> None:
        # tiebreak routes before votes; with-gemini before without
        body = self.client.get("/").content.decode()
        order = [
            "/d/tiny/dots+gemini+lighton/",  # tiebreak, gemini
            "/d/tiny/dots+mistral+lighton/",  # tiebreak, no gemini
            "/d/tiny/dots+gemini+mistral/",  # vote, gemini
            "/d/tiny/dots+mistral+surya_block/",  # vote, no gemini
        ]
        positions = [body.index(u) for u in order]
        self.assertEqual(positions, sorted(positions))

    def test_flow_page_offers_dataset_and_model_selects(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertContains(response, 'name="dataset"')
        self.assertContains(response, 'name="m1"')
        self.assertContains(response, 'name="m2"')
        self.assertContains(response, 'name="m3"')
        # lighton only offered in the third slot
        self.assertContains(response, "lighton (tiebreaker)", count=1)
        # surya's line mode is retired — never offered
        self.assertNotContains(response, "surya line")
        self.assertNotContains(response, "surya_line")

    def test_retired_surya_line_route_404s(self) -> None:
        response = self.client.get(
            "/d/tiny/dots+surya_line+lighton/rep.1.1__p0/"
        )
        self.assertEqual(response.status_code, 404)

    def test_route_go_defaults_land_on_the_flow(self) -> None:
        # the default combination: dots ∥ mistral ∥ surya block (vote)
        response = self.client.get("/go/")
        self.assertRedirects(
            response,
            "/d/tiny/dots+mistral+surya_block/rep.1.1__p0/",
            fetch_redirect_response=False,
        )

    def test_route_go_redirects_to_composed_route(self) -> None:
        response = self.client.get(
            "/go/",
            {
                "dataset": "tiny",
                "m1": "gemini",
                "m2": "dots",
                "m3": "lighton",
                "page": "rep.1.1__p0",
            },
        )
        self.assertRedirects(
            response,
            "/d/tiny/dots+gemini+lighton/rep.1.1__p0/",
            fetch_redirect_response=False,
        )

    def test_route_go_rejects_invalid_combo(self) -> None:
        response = self.client.get(
            "/go/", {"m1": "dots", "m2": "dots", "m3": "lighton"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=", response["Location"])
        follow = self.client.get(response["Location"])
        self.assertContains(follow, "pick three different models")

    def test_route_go_rejects_lighton_in_primary_slot(self) -> None:
        response = self.client.get(
            "/go/", {"m1": "lighton", "m2": "dots", "m3": "gemini"}
        )
        self.assertIn("error=", response["Location"])

    def test_invalid_route_in_url_404s(self) -> None:
        response = self.client.get("/d/tiny/dots+dots+lighton/rep.1.1__p0/")
        self.assertEqual(response.status_code, 404)

    def test_dataset_redirects_to_first_page(self) -> None:
        response = self.client.get("/d/tiny/")
        self.assertRedirects(
            response,
            "/d/tiny/dots+mistral+surya_block/rep.1.1__p0/",
            fetch_redirect_response=False,
        )

    def test_walkthrough_renders_stages(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertEqual(response.status_code, 200)
        for fragment in (
            "Render",
            "Layout",
            "Main OCR",
            "Supplemental",
            "Reconstruct",
            "Normalize",
        ):
            self.assertContains(response, fragment)
        self.assertContains(response, "hello world")
        self.assertContains(response, "hello <em>world</em>", html=False)
        self.assertContains(response, "pageViewer")

    def test_withheld_combination_is_404_everywhere(self) -> None:
        """A dataset that presents only its vote trios must not serve a
        tiebreak combination from a hand-typed or stale URL — the stats
        table withholds those numbers deliberately."""
        ok = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertEqual(ok.status_code, 200)  # presented before the rule
        with mock.patch.object(
            config, "VOTE_ONLY_DATASETS", frozenset({"tiny"})
        ):
            walkthrough = self.client.get(
                "/d/tiny/dots+mistral+lighton/rep.1.1__p0/"
            )
            compare = self.client.get(
                "/compare/tiny/rep.1.1__p0/"
                "?a=dots%2Bmistral%2Blighton&b=dots%2Bmistral%2Blighton"
            )
            rows = data.route_stats("tiny")
            built = data.built_routes("tiny", "rep.1.1__p0")
        self.assertEqual(walkthrough.status_code, 404)
        self.assertEqual(compare.status_code, 404)
        # the stats table offers the vote trios and nothing else
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(not r["tiebreak"] for r in rows))
        # the artifact is still ON DISK — withholding is a reporting
        # rule, so it never deletes or rebuilds anything
        self.assertNotIn("dots+mistral+lighton", built)
        self.assertTrue(
            (self.tmp / "artifacts" / "tiny" / "dots+mistral+lighton").is_dir()
        )

    def test_normalize_card_shows_registry_and_rule_diff(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertContains(response, f"registry {registry_hash()}")
        self.assertContains(response, "sample-rule")
        self.assertContains(response, "shared · key")
        # the change record renders as a before -> after diff
        self.assertContains(response, "Hello")
        self.assertContains(response, "dots (main): 1")
        # final key streams render for both engines
        self.assertContains(response, "mistral (supplemental)")

    def test_compare_card_renders_the_dispute(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertContains(response, "Compare + resolve")
        # the dispute card: three labeled reads, the winner marked
        self.assertContains(response, "→ mistral")
        self.assertContains(response, "mistral ✓")
        self.assertContains(response, "word")
        self.assertContains(response, "tiebreak funnel")
        self.assertContains(response, "1 attempted")
        # the crop image the tiebreaker actually read
        self.assertContains(
            response, "/crop/tiny/rep.1.1__p0/100_100_800_200.png"
        )
        # the disputes overlay layer is offered
        self.assertContains(response, "disputes")
        # the marks flow reports its own comparison
        self.assertContains(response, "footnote-mark sequences")

    def test_high_risk_review_page(self) -> None:
        response = self.client.get("/review/tiny/high-risk/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "High-risk disputes — tiny")
        # the fixture's high-risk dispute, with labeled reads and the
        # kept side marked
        self.assertContains(response, "dots + surya block + lighton")
        self.assertContains(response, "goodbye friend")
        self.assertContains(response, "no-cached-read")
        self.assertContains(response, "✓ (kept)")
        # links into the walkthrough page holding the dispute card
        self.assertContains(
            response, "/d/tiny/dots+surya_block+lighton/rep.1.1__p0/"
        )
        # majority-resolved disputes never appear
        self.assertNotContains(response, '"word"')

    def test_rejected_and_low_conf_review_pages(self) -> None:
        # the same fixture dispute is rejected (no-cached-read) AND
        # low-confidence, so both categories list it
        for category, title in (
            ("rejected", "Rejected or refused disputes"),
            ("low-conf", "Low-confidence disputes"),
        ):
            response = self.client.get(f"/review/tiny/{category}/")
            self.assertContains(response, title)
            self.assertContains(response, "goodbye friend")

    def test_reorders_review_page_empty_state(self) -> None:
        response = self.client.get("/review/tiny/reorders/")
        self.assertContains(response, "Reorders — tiny")
        self.assertContains(response, "Nothing in this category.")

    def test_unknown_review_category_404s(self) -> None:
        response = self.client.get("/review/tiny/everything/")
        self.assertEqual(response.status_code, 404)

    def test_crop_image_served(self) -> None:
        response = self.client.get(
            "/crop/tiny/rep.1.1__p0/100_100_800_200.png"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_missing_or_malformed_crop_404s(self) -> None:
        response = self.client.get("/crop/tiny/rep.1.1__p0/1_2_3_4.png")
        self.assertEqual(response.status_code, 404)
        response = self.client.get("/crop/tiny/rep.1.1__p0/../../x.png")
        self.assertEqual(response.status_code, 404)

    def test_unknown_page_404s(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/nope__p9/")
        self.assertEqual(response.status_code, 404)

    def test_stale_schema_unrebuildable_404s(self) -> None:
        # the stale artifact triggers a rebuild; this synthetic tree has
        # no redacted sources, so the rebuild fails -> 404, never a
        # silently stale render
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p9/")
        self.assertEqual(response.status_code, 404)

    def test_stale_registry_hash_unrebuildable_404s(self) -> None:
        # same discipline for a stale normalization-registry hash
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p8/")
        self.assertEqual(response.status_code, 404)

    def test_pages_order_naturally(self) -> None:
        self.assertEqual(
            data.dataset_pages("tiny"),
            ["rep.1.1__p0", "rep.1.1__p2", "rep.1.1__p10"],
        )

    def test_preset_route_urls_still_serve(self) -> None:
        # pre-composition bookmarks (/d/<ds>/mistral/...) resolve to the
        # canonical combination and serve from its artifact dir
        response = self.client.get("/d/tiny/mistral/rep.1.1__p0/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Main OCR — dots")

    def test_route_go_empty_params_use_defaults(self) -> None:
        # the home form submits empty values for anything never saved
        response = self.client.get(
            "/go/", {"dataset": "", "m1": "", "m2": "", "m3": "", "page": ""}
        )
        self.assertRedirects(
            response,
            "/d/tiny/dots+mistral+surya_block/rep.1.1__p0/",
            fetch_redirect_response=False,
        )

    def test_page_img_served(self) -> None:
        response = self.client.get("/img/tiny/rep.1.1__p0.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_path_traversal_rejected(self) -> None:
        response = self.client.get("/img/tiny/%2e%2e%2fsecret.png")
        self.assertEqual(response.status_code, 404)

    def test_raw_engine_output_expandables(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertContains(response, "content verbatim")
        self.assertContains(response, "raw model output — dots JSON")
        self.assertContains(response, "raw model output — mistral JSON")
        self.assertContains(response, "lighton crop tiebreak")

    def test_home_shows_model_info(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, "Models")
        self.assertContains(response, "dots.mocr")
        self.assertContains(
            response, "https://huggingface.co/rednote-hilab/dots.mocr"
        )
        self.assertContains(response, "OpenRAIL-M")

    def test_assemble_card_renders_the_final(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertContains(response, "Assemble")
        # the dispute's winning supplemental reading is IN the final
        self.assertContains(
            response,
            '<p data-item="0" data-role="content">Hello word</p>',
            html=False,
        )
        # and the card links into the route-compare view
        self.assertContains(
            response, "/compare/tiny/rep.1.1__p0/?a=dots%2Bmistral%2Blighton"
        )

    def test_assemble_card_marks_low_confidence(self) -> None:
        response = self.client.get(
            "/d/tiny/dots+surya_block+lighton/rep.1.1__p0/"
        )
        self.assertContains(
            response,
            '<mark class="low-confidence high-risk" data-dispute="0">'
            "<em>Hello</em></mark>",
            html=False,
        )

    def test_route_compare_highlights_the_differences(self) -> None:
        response = self.client.get(
            "/compare/tiny/rep.1.1__p0/"
            "?a=dots+mistral+lighton&b=dots+surya_block+lighton"
        )
        self.assertEqual(response.status_code, 200)
        # the finals differ on their second unit: word vs world
        self.assertContains(
            response, '<span class="route-diff">word</span>', html=False
        )
        # "Hello" agrees as text on both sides but only one styles it —
        # a STYLING diff, not a text diff; the low-confidence mark
        # survives into the compare panel
        self.assertContains(
            response,
            '<span class="style-diff">'
            '<mark class="low-confidence high-risk" data-dispute="0">'
            "<em>Hello</em></mark></span> "
            '<span class="route-diff">world</span>',
            html=False,
        )
        self.assertContains(
            response, '<span class="style-diff">Hello</span>', html=False
        )
        # each highlight dimension is its own toggle
        self.assertContains(response, "plain-text diffs")
        self.assertContains(response, "styling diffs")
        self.assertContains(response, "low confidence")

    def test_route_compare_shows_the_zoomable_page_image(self) -> None:
        response = self.client.get("/compare/tiny/rep.1.1__p0/")
        self.assertContains(response, "/img/tiny/rep.1.1__p0.png")
        self.assertContains(response, 'x-data="imgZoom"')
        self.assertContains(response, 'aria-label="Zoom in"')
        # subgrid rows: the panel headers share one height, so the two
        # texts start on the same line
        self.assertContains(response, "md:grid-rows-subgrid")

    def test_no_template_comment_leaks(self) -> None:
        # a multi-line {# #} renders literally (Django's {# #} is
        # single-line only — a recurring regression)
        for url in (
            "/",
            "/d/tiny/dots+mistral+lighton/rep.1.1__p0/",
            "/compare/tiny/rep.1.1__p0/",
            "/review/tiny/high-risk/",
        ):
            self.assertNotContains(self.client.get(url), "{#")

    def test_route_compare_defaults_to_built_combinations(self) -> None:
        # no params: both sides fall back to combinations already on
        # disk for this page (never a rebuild of an unbuilt one)
        response = self.client.get("/compare/tiny/rep.1.1__p0/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dots + mistral + lighton")
        self.assertContains(response, "dots + surya block + lighton")

    def test_route_compare_rejects_unknown_routes(self) -> None:
        response = self.client.get(
            "/compare/tiny/rep.1.1__p0/?a=dots+nope+lighton"
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.get("/compare/tiny/rep.9.9__p77/")
        self.assertEqual(response.status_code, 404)

    def test_route_compare_same_route_note(self) -> None:
        response = self.client.get(
            "/compare/tiny/rep.1.1__p0/"
            "?a=dots+mistral+lighton&b=dots+mistral+lighton"
        )
        self.assertContains(response, "Both sides show the same combination")

    def test_home_links_the_compare_view(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, "/compare/tiny/rep.1.1__p0/")

    def test_home_unknown_dataset_falls_back(self) -> None:
        response = self.client.get("/?dataset=nope")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Route stats — tiny")

    def test_review_page_filters_by_combination(self) -> None:
        # both fixture routes have flagged disputes; the low-conf page
        # shows chips and honors ?route=
        response = self.client.get("/review/tiny/low-conf/")
        self.assertContains(response, "combination:")
        self.assertContains(response, "all (1)")
        response = self.client.get(
            "/review/tiny/low-conf/?route=dots+surya_block+lighton"
        )
        self.assertContains(response, "goodbye friend")
        response = self.client.get(
            "/review/tiny/low-conf/?route=dots+mistral+lighton"
        )
        self.assertNotContains(response, "goodbye friend")
        self.assertContains(response, "Nothing in this category.")


class ExportIngestTest(DataTreeTestCase):
    """The LightOn round trip: a dispute with no cached read exports its
    crop bundle (the lighton pod kit's input contract), the read comes
    back in a tarball, ingest_reads lands it in the cache, and the
    rebuilt artifact votes."""

    @classmethod
    def build_tree(cls, tmp: Path) -> None:
        ds = tmp / "datasets" / "lot"
        flat = ds / "redacted"
        flat.mkdir(parents=True)
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        doc.save(str(flat / "rep.9.9__page_001.pdf"))
        doc.close()
        (ds / "page_png").mkdir(parents=True)
        Image.new("RGB", (1700, 2200), "white").save(
            ds / "page_png" / "rep.9.9__page_001.png"
        )
        engines = ds / "engines"
        (engines / "container_yolo").mkdir(parents=True)
        (engines / "container_yolo" / "rep.9.9__page_001.json").write_text(
            json.dumps(
                [
                    {
                        "label": "column",
                        "bbox": [100, 100, 1600, 2100],
                        "confidence": 0.95,
                    }
                ]
            )
        )
        (engines / "dots").mkdir(parents=True)
        (engines / "dots" / "rep.9.9__page_001.json").write_text(
            json.dumps(
                {
                    "text": "hello world again today okay",
                    "regions": [
                        {
                            "order": 0,
                            "label": "Text",
                            "bbox": [120, 120, 900, 200],
                            "text": "hello world again today okay",
                        }
                    ],
                }
            )
        )
        (engines / "mistral").mkdir(parents=True)
        # the whole-response shape, and a reading that disputes "world"
        (engines / "mistral" / "rep.9.9__page_001.json").write_text(
            json.dumps(
                {
                    "markdown": "hello wield again today okay",
                    "blocks": [
                        {
                            "type": "text",
                            "bbox": [120, 120, 900, 200],
                            "text": "hello wield again today okay",
                        }
                    ],
                }
            )
        )

    def test_single_glyph_crop_is_scaled_up(self) -> None:
        """A one-glyph dispute region renders large enough to read: a
        crop of a few pixels is unreadable to a person and too small for
        a vision tower that merges patches 2x2."""
        png = data.page_crop_png("lot", "rep.9.9__page_001", "300_300_312_319")
        with Image.open(io.BytesIO(png)) as im:
            self.assertEqual(min(im.size), data.MIN_CROP_SIDE)
            self.assertAlmostEqual(im.width / im.height, 12 / 19, places=1)
        # a region already big enough is untouched
        png = data.page_crop_png("lot", "rep.9.9__page_001", "120_120_900_200")
        with Image.open(io.BytesIO(png)) as im:
            self.assertEqual(im.size, (780, 80))

    def test_flat_export_ingest_revote_round_trip(self) -> None:
        out = io.StringIO()
        bundle = self.tmp / "bundle"
        call_command(
            "export_crops",
            dataset="lot",
            route=["dots+mistral+lighton"],
            out=bundle,
            stdout=out,
        )
        key = "rep.9.9__page_001_120_120_900_200"
        manifest = (bundle / "manifest.jsonl").read_text().strip()
        entry = json.loads(manifest)
        self.assertEqual(entry["key"], key)
        self.assertEqual(entry["expect"], "hello world again today okay")
        self.assertTrue((bundle / "crops" / f"{key}.png").exists())
        crops_cache = (
            self.tmp / "datasets" / "lot" / "engines" / "lighton_crops"
        )
        self.assertTrue((crops_cache / f"{key}.png").exists())
        # before the read arrives the dispute is an honest no-vote
        artifact = json.loads(
            (
                self.tmp
                / "artifacts"
                / "lot"
                / "dots+mistral+lighton"
                / "rep.9.9__page_001.json"
            ).read_text()
        )
        d = artifact["stages"]["compare"]["disputes"][0]
        self.assertEqual(d["reason"], "no-cached-read")

        # the pod's reads come back as a tarball of reads/<key>.txt
        reads = self.tmp / "reads"
        reads.mkdir()
        (reads / f"{key}.txt").write_text("hello world again today okay")
        tar_path = self.tmp / "lighton_reads_lot.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(reads / f"{key}.txt", arcname=f"reads/{key}.txt")
        call_command(
            "ingest_reads", tar_path, dataset="lot", stdout=io.StringIO()
        )
        self.assertTrue((crops_cache / f"{key}.txt").exists())

        run_route(self.tmp, "lot", resolve("dots+mistral+lighton"))
        artifact = json.loads(
            (
                self.tmp
                / "artifacts"
                / "lot"
                / "dots+mistral+lighton"
                / "rep.9.9__page_001.json"
            ).read_text()
        )
        d = artifact["stages"]["compare"]["disputes"][0]
        self.assertEqual(d["resolution"], "majority")
        self.assertEqual(d["verdict"], "main")  # lighton sided with dots
        self.assertIn("world", artifact["stages"]["assemble"]["text"])


class MainFallbackTest(DataTreeTestCase):
    """A page whose main output is missing or blank falls back to the
    next bbox engine (dots > mistral > surya); the remaining engines
    carry the page, and their differences flag low-confidence."""

    @classmethod
    def build_tree(cls, tmp: Path) -> None:
        ds = tmp / "datasets" / "fb"
        vol = ds / "redacted" / "rep" / "9" / "9"
        vol.mkdir(parents=True)
        (vol / "detections.json").write_text("[]")
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        doc.save(str(vol / "vol.pdf"))
        doc.close()
        (ds / "page_png").mkdir(parents=True)
        Image.new("RGB", (17, 22), "white").save(
            ds / "page_png" / "rep.9.9__p0.png"
        )
        engines = ds / "engines"
        (engines / "container_yolo").mkdir(parents=True)
        (engines / "container_yolo" / "rep.9.9__p0.json").write_text(
            json.dumps(
                [
                    {
                        "label": "column",
                        "bbox": [100, 100, 1600, 2100],
                        "confidence": 0.95,
                    }
                ]
            )
        )
        # dots exists but came back BLANK (no usable text)
        (engines / "dots").mkdir(parents=True)
        (engines / "dots" / "rep.9.9__p0.json").write_text(
            json.dumps(
                {
                    "text": "",
                    "regions": [
                        {
                            "order": 0,
                            "label": "Text",
                            "bbox": [120, 120, 900, 200],
                            "text": "",
                        }
                    ],
                }
            )
        )
        (engines / "mistral").mkdir(parents=True)
        (engines / "mistral" / "rep.9.9__p0.json").write_text(
            json.dumps(
                [
                    {
                        "type": "text",
                        "bbox": [120, 120, 900, 200],
                        "text": "hello world today",
                    }
                ]
            )
        )
        (engines / "surya" / "block").mkdir(parents=True)
        (engines / "surya" / "block" / "rep.9.9__p0.json").write_text(
            json.dumps(
                {
                    "regions": [
                        {
                            "order": 0,
                            "label": "Text",
                            "raw_label": "Text",
                            "bbox": [120, 120, 900, 200],
                            "text": "hello wield today",
                            "html": "hello wield today",
                        }
                    ]
                }
            )
        )

    def test_blank_main_falls_back_and_flags_differences(self) -> None:
        run_route(self.tmp, "fb", resolve("dots+mistral+surya_block"))
        artifact = json.loads(
            (
                self.tmp
                / "artifacts"
                / "fb"
                / "dots+mistral+surya_block"
                / "rep.9.9__p0.json"
            ).read_text()
        )
        mo = artifact["stages"]["main_ocr"]
        self.assertEqual(mo["engine"], "mistral")
        self.assertEqual(mo["fallback_from"], "dots")
        cmp = artifact["stages"]["compare"]
        # blank dots is a degraded supplemental; mistral vs surya
        # differences have no third voter -> low-confidence
        self.assertIn("supp1", cmp["degraded"])
        d = cmp["disputes"][0]
        self.assertEqual(d["reason"], "no-vote")
        self.assertTrue(d["low_confidence"])
        self.assertIn("world", artifact["stages"]["assemble"]["text"])
        response = self.client.get(
            "/d/fb/dots+mistral+surya_block/rep.9.9__p0/"
        )
        self.assertContains(response, "came back missing or")
        self.assertContains(response, "Main OCR — mistral")

    def test_missing_main_falls_back(self) -> None:
        # remove dots entirely: the page still runs on the other two
        (self.tmp / "datasets" / "fb" / "engines" / "dots").joinpath(
            "rep.9.9__p0.json"
        ).unlink(missing_ok=True)
        written, skipped = run_route(
            self.tmp, "fb", resolve("dots+gemini+mistral")
        )
        self.assertEqual((written, skipped), (1, 0))
        artifact = json.loads(
            (
                self.tmp
                / "artifacts"
                / "fb"
                / "dots+gemini+mistral"
                / "rep.9.9__p0.json"
            ).read_text()
        )
        mo = artifact["stages"]["main_ocr"]
        self.assertEqual(mo["engine"], "mistral")
        self.assertEqual(mo["fallback_from"], "dots")


class DiffFlagsTest(SimpleTestCase):
    """Route-compare flags text and styling as separate dimensions."""

    def test_dimensions_flag_separately(self) -> None:
        a = [
            {"key": "one", "styling": []},
            {"key": "two", "styling": ["em"]},
            {"key": "three", "styling": []},
        ]
        b = [
            {"key": "one", "styling": []},
            {"key": "two", "styling": []},
            {"key": "tres", "styling": []},
        ]
        text_runs, style_runs = _diff_flags(a, b)
        self.assertEqual((text_runs, style_runs), (1, 1))
        # "two": same text, different styling — styling dimension only
        self.assertTrue(a[1].get("style_diff"))
        self.assertTrue(b[1].get("style_diff"))
        self.assertFalse(a[1].get("diff"))
        # "three"/"tres": text dimension only
        self.assertTrue(a[2].get("diff"))
        self.assertTrue(b[2].get("diff"))
        self.assertFalse(a[2].get("style_diff"))
        # agreeing units carry no flags
        self.assertFalse(a[0].get("diff") or a[0].get("style_diff"))


class PipelineToViewerContractTest(DataTreeTestCase):
    """End-to-end seam test: pipeline.run builds a REAL artifact over a
    synthetic dataset, and the viewer renders it. Locks the
    pipeline/viewer contract — the fixture-based tests above cannot catch
    the two sides drifting apart."""

    @classmethod
    def build_tree(cls, tmp: Path) -> None:
        ds = tmp / "datasets" / "e2e"
        vol = ds / "redacted" / "rep" / "9" / "9"
        vol.mkdir(parents=True)
        (vol / "detections.json").write_text("[]")
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        doc.save(str(vol / "vol.pdf"))
        doc.close()
        (ds / "page_png").mkdir(parents=True)
        Image.new("RGB", (17, 22), "white").save(
            ds / "page_png" / "rep.9.9__p0.png"
        )
        engines = ds / "engines"
        (engines / "container_yolo").mkdir(parents=True)
        (engines / "container_yolo" / "rep.9.9__p0.json").write_text(
            json.dumps(
                [
                    {
                        "label": "column",
                        "bbox": [100, 100, 1600, 2100],
                        "confidence": 0.95,
                    }
                ]
            )
        )
        (engines / "dots").mkdir(parents=True)
        (engines / "dots" / "rep.9.9__p0.json").write_text(
            json.dumps(
                {
                    "text": "hello *world*",
                    "regions": [
                        {
                            "order": 0,
                            "label": "Text",
                            "bbox": [120, 120, 900, 200],
                            "text": "hello *world*",
                        },
                        # an image block: the final renders its region
                        # as an inline crop of the page render
                        {
                            "order": 1,
                            "label": "Picture",
                            "bbox": [120, 300, 500, 600],
                            "text": "",
                        },
                    ],
                }
            )
        )
        (engines / "mistral").mkdir(parents=True)
        (engines / "mistral" / "rep.9.9__p0.json").write_text(
            json.dumps(
                [
                    {
                        "type": "text",
                        "bbox": [120, 120, 900, 200],
                        "text": "hello **world**",
                    }
                ]
            )
        )

    def test_run_route_artifact_renders_in_viewer(self) -> None:
        written, skipped = run_route(self.tmp, "e2e", resolve("mistral"))
        self.assertEqual((written, skipped), (1, 0))
        artifact = json.loads(
            (
                self.tmp
                / "artifacts"
                / "e2e"
                / "dots+mistral+lighton"
                / "rep.9.9__p0.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(artifact["schema_version"], SCHEMA_VERSION)
        response = self.client.get("/d/e2e/dots+mistral+lighton/rep.9.9__p0/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hello")
        # dots markdown styling survived into the rendered reconstruction
        self.assertContains(response, "<em>world</em>")
        # mistral bold styling too
        self.assertContains(response, "<strong>world</strong>")
        # normalization: canonical tokens with provenance, stamped registry.
        # dots' `*world*` markdown decoded to <em> -> the emphasis is
        # STYLING on the token, not comparison content: both engines'
        # keys agree on "world".
        norm = artifact["stages"]["normalize"]
        self.assertEqual(norm["registry"]["hash"], registry_hash())
        self.assertEqual(
            [t["key"] for t in norm["main"]["tokens"]],
            ["hello", "world"],
        )
        self.assertEqual(norm["main"]["tokens"][1]["styling"], ["em"])
        self.assertEqual(
            [t["key"] for t in norm["supplementals"][0]["tokens"]],
            ["hello", "world"],
        )
        token = norm["main"]["tokens"][0]
        items_by_id = {
            i["id"]: i
            for i in artifact["stages"]["reconstruct"]["main"]["items"]
        }
        item = items_by_id[token["item"]]
        start, end = token["span"]
        self.assertEqual(item["html"][start:end], token["display"])
        self.assertContains(response, "Normalize")
        self.assertContains(response, f"registry {registry_hash()}")
        self.assertContains(response, "engine-decode rules")
        # compare + resolve: identical streams agree end to end
        cmp = artifact["stages"]["compare"]
        self.assertEqual(cmp["metrics"]["n_disputes"], 0)
        self.assertEqual(cmp["params"]["gate_min_chars"], 10)
        self.assertContains(response, "Compare + resolve")
        self.assertContains(response, "The streams agree")
        # assemble: the final carries the styling union (mistral's
        # <strong> joins dots' <em> on the agreeing unit)
        asm = artifact["stages"]["assemble"]
        self.assertEqual(asm["text"], "hello world")
        self.assertIn("<em><strong>world</strong></em>", asm["html"])
        self.assertEqual(asm["metrics"]["units"], 2)
        self.assertEqual(asm["metrics"]["styling_unioned"], 1)
        self.assertContains(response, "Assemble")
        # the artifact keeps the image placeholder EMPTY (viewer-
        # agnostic); the walkthrough fills it from the page-crop
        # endpoint
        self.assertIn(
            '<figure data-item="1" data-bbox="120,300,500,600"></figure>',
            asm["html"],
        )
        self.assertContains(
            response, "/pagecrop/e2e/rep.9.9__p0/120_300_500_600.png"
        )
        crop = self.client.get("/pagecrop/e2e/rep.9.9__p0/120_300_500_600.png")
        self.assertEqual(crop.status_code, 200)
        self.assertEqual(crop["Content-Type"], "image/png")
        self.assertEqual(
            self.client.get("/pagecrop/e2e/rep.9.9__p0/oops.png").status_code,
            404,
        )
        # and the route-compare view renders the same artifact
        compare_page = self.client.get(
            "/compare/e2e/rep.9.9__p0/"
            "?a=dots+mistral+lighton&b=dots+mistral+lighton"
        )
        self.assertEqual(compare_page.status_code, 200)
        self.assertContains(
            compare_page, "Both sides show the same combination"
        )

    def test_corrupt_artifact_self_heals(self) -> None:
        # a torn/half-written file rebuilds on the next visit instead of
        # 500ing forever
        out = (
            self.tmp
            / "artifacts"
            / "e2e"
            / "dots+mistral+lighton"
            / "rep.9.9__p0.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{ torn", encoding="utf-8")
        response = self.client.get("/d/e2e/dots+mistral+lighton/rep.9.9__p0/")
        self.assertEqual(response.status_code, 200)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema_version"], SCHEMA_VERSION)

    def test_stale_registry_hash_rebuilds(self) -> None:
        # a registry change (new/edited rule) refreshes the artifact on
        # the next visit — no silently stale normalization
        run_route(self.tmp, "e2e", resolve("mistral"))
        out = (
            self.tmp
            / "artifacts"
            / "e2e"
            / "dots+mistral+lighton"
            / "rep.9.9__p0.json"
        )
        doc = json.loads(out.read_text(encoding="utf-8"))
        doc["stages"]["normalize"]["registry"]["hash"] = "000000000000"
        out.write_text(json.dumps(doc), encoding="utf-8")
        response = self.client.get("/d/e2e/dots+mistral+lighton/rep.9.9__p0/")
        self.assertEqual(response.status_code, 200)
        fresh = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            fresh["stages"]["normalize"]["registry"]["hash"],
            registry_hash(),
        )

    def test_composed_route_materializes_on_first_visit(self) -> None:
        # no pipeline.run for this combination — the first visit builds
        # the artifact from the cached engine outputs and writes it.
        # mistral is the main engine here (no dots in the set).
        out = (
            self.tmp
            / "artifacts"
            / "e2e"
            / "mistral+gemini+surya_block"
            / "rep.9.9__p0.json"
        )
        self.assertFalse(out.exists())
        response = self.client.get(
            "/d/e2e/mistral+gemini+surya_block/rep.9.9__p0/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Main OCR — mistral")
        self.assertTrue(out.exists())
        artifact = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(artifact["route"], "mistral+gemini+surya_block")
        self.assertIsNone(artifact["tiebreak"])
        self.assertEqual(artifact["stages"]["main_ocr"]["engine"], "mistral")
        # neither gemini XML nor surya reads exist in this tree ->
        # refusal / missing supplementals, honestly degraded
        engines = [s["engine"] for s in artifact["stages"]["supplementals"]]
        self.assertEqual(engines, ["gemini", "surya"])

    def test_alias_ordering_serves_the_canonical_route(self) -> None:
        # order never matters: an alias URL builds/serves the CANONICAL
        # artifact (one dir per unique combination)
        response = self.client.get("/d/e2e/gemini+dots+lighton/rep.9.9__p0/")
        self.assertEqual(response.status_code, 200)
        out = (
            self.tmp
            / "artifacts"
            / "e2e"
            / "dots+gemini+lighton"
            / "rep.9.9__p0.json"
        )
        self.assertTrue(out.exists())
        self.assertFalse(
            (self.tmp / "artifacts" / "e2e" / "gemini+dots+lighton").exists()
        )

    def test_lighton_without_dots_404s(self) -> None:
        # tiebreak combinations without dots were removed — the cached
        # crops are cut from dots blocks
        response = self.client.get(
            "/d/e2e/mistral+gemini+lighton/rep.9.9__p0/"
        )
        self.assertEqual(response.status_code, 404)
