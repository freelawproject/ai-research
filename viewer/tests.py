"""Viewer tests run against a small SYNTHETIC data tree (no dependency on
the distributed data bundle, so they pass in CI)."""

import json
import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from PIL import Image

_TMP = Path(tempfile.mkdtemp(prefix="extraction-tests-"))
_DS = _TMP / "datasets" / "tiny"

_ARTIFACT = {
    "schema_version": 1,
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
                    {"id": 0, "role": "content", "band": "body",
                     "column": "L", "html": "hello <em>world</em>"}
                ],
                "text": "hello world",
            },
            "supplementals": [
                {
                    "engine": "mistral",
                    "unit": "block",
                    "order": [0],
                    "items": [
                        {"id": 0, "role": "content", "band": "body",
                         "column": "L", "html": "hello <em>world</em>"}
                    ],
                    "text": "hello world",
                }
            ],
        },
    },
}


def _build_tree() -> None:
    if _DS.exists():
        return
    (_DS / "page_png").mkdir(parents=True)
    for engine in ("dots", "mistral"):
        d = _DS / "engines" / engine
        d.mkdir(parents=True)
        (d / "rep.1.1__p0.json").write_text('{"raw": "engine file"}')
    Image.new("RGB", (17, 22)).save(_DS / "page_png" / "rep.1.1__p0.png")
    art = _TMP / "artifacts" / "tiny" / "mistral"
    art.mkdir(parents=True)
    (art / "rep.1.1__p0.json").write_text(json.dumps(_ARTIFACT))


@override_settings(
    DATA_ROOT=_TMP,
    DATASETS_ROOT=_TMP / "datasets",
    ARTIFACTS_ROOT=_TMP / "artifacts",
    WEIGHTS_ROOT=_TMP / "weights",
)
class ViewerViewsTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _build_tree()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(_TMP, ignore_errors=True)
        super().tearDownClass()

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
