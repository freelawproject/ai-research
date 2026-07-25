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
from pipeline.routes import ROUTES
from pipeline.run import run_route

_ARTIFACT = {
    "schema_version": SCHEMA_VERSION,
    "dataset": "tiny",
    "page": "rep.1.1__p0",
    "route": "mistral",
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
                        "html": "hello <em>world</em>",
                    }
                ],
                "text": "hello world",
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
                            "html": "hello <em>world</em>",
                        }
                    ],
                    "text": "hello world",
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
        art = tmp / "artifacts" / "tiny" / "mistral"
        art.mkdir(parents=True)
        (art / "rep.1.1__p0.json").write_text(json.dumps(_ARTIFACT))
        # a second page whose artifact is a STALE schema version
        stale = dict(_ARTIFACT, page="rep.1.1__p9", schema_version=0)
        (art / "rep.1.1__p9.json").write_text(json.dumps(stale))

    def test_home_lists_samples_with_route_buttons(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tiny")
        self.assertContains(response, "rep.1.1__p0")
        # mistral has an artifact -> link; the others -> inert spans
        self.assertContains(response, "/d/tiny/mistral/rep.1.1__p0/")
        self.assertNotContains(response, "/d/tiny/gemini/rep.1.1__p0/")
        self.assertNotContains(response, "/d/tiny/surya_line/rep.1.1__p0/")
        self.assertNotContains(response, "/d/tiny/surya_block/rep.1.1__p0/")
        self.assertNotContains(response, "/d/tiny/three_way/rep.1.1__p0/")

    def test_dataset_redirects_to_first_page(self) -> None:
        response = self.client.get("/d/tiny/")
        self.assertRedirects(
            response,
            "/d/tiny/mistral/rep.1.1__p0/",
            fetch_redirect_response=False,
        )

    def test_walkthrough_renders_stages(self) -> None:
        response = self.client.get("/d/tiny/mistral/rep.1.1__p0/")
        self.assertEqual(response.status_code, 200)
        for fragment in (
            "Render",
            "Layout",
            "Main OCR",
            "Supplemental",
            "Reconstruct",
        ):
            self.assertContains(response, fragment)
        self.assertContains(response, "hello world")
        self.assertContains(response, "hello <em>world</em>", html=False)
        self.assertContains(response, "pageViewer")

    def test_missing_artifact_404s(self) -> None:
        response = self.client.get("/d/tiny/mistral/nope__p9/")
        self.assertEqual(response.status_code, 404)

    def test_stale_schema_artifact_404s(self) -> None:
        response = self.client.get("/d/tiny/mistral/rep.1.1__p9/")
        self.assertEqual(response.status_code, 404)

    def test_page_img_served(self) -> None:
        response = self.client.get("/img/tiny/rep.1.1__p0.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_path_traversal_rejected(self) -> None:
        response = self.client.get("/img/tiny/%2e%2e%2fsecret.png")
        self.assertEqual(response.status_code, 404)

    def test_raw_engine_output_expandables(self) -> None:
        response = self.client.get("/d/tiny/mistral/rep.1.1__p0/")
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
        written, skipped = run_route(self.tmp, "e2e", ROUTES["mistral"])
        self.assertEqual((written, skipped), (1, 0))
        artifact = json.loads(
            (
                self.tmp / "artifacts" / "e2e" / "mistral" / "rep.9.9__p0.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(artifact["schema_version"], SCHEMA_VERSION)
        response = self.client.get("/d/e2e/mistral/rep.9.9__p0/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hello")
        # dots markdown styling survived into the rendered reconstruction
        self.assertContains(response, "<em>world</em>")
        # mistral bold styling too
        self.assertContains(response, "<strong>world</strong>")
