"""Viewer tests run against small SYNTHETIC data trees (no dependency on
the distributed data bundle, so they pass in CI). Each TestCase builds its
own tree in setUpClass and points settings at it."""

import json
import shutil
import tempfile
from pathlib import Path

import fitz
from django.test import SimpleTestCase, override_settings
from PIL import Image

from pipeline.core.artifacts import SCHEMA_VERSION
from pipeline.core.normalize import registry_hash
from pipeline.routes import resolve
from pipeline.run import run_route
from viewer import data

_ARTIFACT = {
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
            "page_text": "hello world",
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
                            "n_changed": 0,
                            "changes": [],
                        }
                    ],
                }
            ],
        },
    },
}


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
        Image.new("RGB", (17, 22)).save(ds / "page_png" / "rep.1.1__p0.png")
        art = tmp / "artifacts" / "tiny" / "dots+mistral+lighton"
        art.mkdir(parents=True)
        (art / "rep.1.1__p0.json").write_text(json.dumps(_ARTIFACT))
        # a second page whose artifact is a STALE schema version
        stale = dict(_ARTIFACT, page="rep.1.1__p9", schema_version=0)
        (art / "rep.1.1__p9.json").write_text(json.dumps(stale))
        # extra renders exercise NATURAL page ordering (p2 before p10)
        for extra in ("rep.1.1__p2", "rep.1.1__p10"):
            Image.new("RGB", (17, 22)).save(ds / "page_png" / f"{extra}.png")
        # and a page whose artifact carries a stale REGISTRY hash
        aged = json.loads(json.dumps(_ARTIFACT))
        aged["page"] = "rep.1.1__p8"
        aged["stages"]["normalize"]["registry"]["hash"] = "000000000000"
        (art / "rep.1.1__p8.json").write_text(json.dumps(aged))

    def test_home_is_a_door_to_the_flow(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open the pipeline flow")
        self.assertContains(response, "/go/")
        # dataset/model/page SELECTION lives on the flow page — home
        # only carries hidden fields that restore the last-used combo
        self.assertNotContains(response, "<select")
        self.assertContains(response, 'data-restore="true"')
        self.assertNotContains(response, "rep.1.1__p0")

    def test_flow_page_offers_dataset_and_model_selects(self) -> None:
        response = self.client.get("/d/tiny/dots+mistral+lighton/rep.1.1__p0/")
        self.assertContains(response, 'name="dataset"')
        self.assertContains(response, 'name="m1"')
        self.assertContains(response, 'name="m2"')
        self.assertContains(response, 'name="m3"')
        # lighton only offered in the third slot
        self.assertContains(response, "lighton (tiebreaker)", count=1)

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
        self.assertContains(response, "lighton tiebreak")

    def test_home_shows_model_info(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, "Models")
        self.assertContains(response, "dots.mocr")
        self.assertContains(
            response, "https://huggingface.co/rednote-hilab/dots.mocr"
        )
        self.assertContains(response, "OpenRAIL-M")


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
        Image.new("RGB", (17, 22)).save(ds / "page_png" / "rep.9.9__p0.png")
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
        # stage 6: canonical tokens with provenance, stamped registry.
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
        # mistral is the main engine here (dots sits in the vote slot).
        out = (
            self.tmp
            / "artifacts"
            / "e2e"
            / "mistral+gemini+dots"
            / "rep.9.9__p0.json"
        )
        self.assertFalse(out.exists())
        response = self.client.get("/d/e2e/mistral+gemini+dots/rep.9.9__p0/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Main OCR — mistral")
        self.assertTrue(out.exists())
        artifact = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(artifact["route"], "mistral+gemini+dots")
        self.assertIsNone(artifact["tiebreak"])
        self.assertEqual(artifact["stages"]["main_ocr"]["engine"], "mistral")
        # gemini has no cached XML in this tree -> refusal supplemental
        engines = [s["engine"] for s in artifact["stages"]["supplementals"]]
        self.assertEqual(engines, ["gemini", "dots"])
