"""Alignment viewer tests, over a small SYNTHETIC unredacted set — a
prerendered dataset (page renders + volume PDFs, no redacted tree),
which is the layout these sets actually arrive in."""

import io
import json
import shutil
import tempfile
from pathlib import Path

import fitz
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from PIL import Image

from pipeline.align_run import ALIGN_DIR, run_dataset
from pipeline.core.config import RENDER_H, RENDER_W, dataset
from pipeline.core.pages import discover_pages
from viewer import align_data, data

VOLUME = "rep.7.1.900"
PAGES = ("page_0001", "page_0002")


def _dots(page_no: int) -> dict:
    return {
        "text": "left right",
        "regions": [
            {
                "order": 0,
                "label": "Page-header",
                "bbox": [230, 118, 440, 152],
                "text": f"{page_no}",
            },
            {
                "order": 1,
                "label": "Text",
                "bbox": [230, 200, 810, 900],
                "text": "left column body",
            },
            {
                "order": 2,
                "label": "Text",
                "bbox": [230, 920, 810, 1600],
                "text": "left column more",
            },
            {
                "order": 3,
                "label": "Text",
                "bbox": [870, 200, 1460, 900],
                "text": "right column body",
            },
            {
                "order": 4,
                "label": "Text",
                "bbox": [870, 920, 1460, 1600],
                "text": "right column more",
            },
        ],
    }


def _surya(page_no: int) -> dict:
    """Same page, but the first body region split in two — the N:M case
    the alignment exists to absorb."""
    regions = [
        {
            "order": 0,
            "label": "PageHeader",
            "bbox": [230, 118, 440, 152],
            "text": f"{page_no}",
            "html": f"{page_no}",
        },
        {
            "order": 1,
            "label": "Text",
            "bbox": [230, 200, 810, 540],
            "text": "left column",
            "html": "left column",
        },
        {
            "order": 2,
            "label": "Text",
            "bbox": [230, 550, 810, 900],
            "text": "body",
            "html": "body",
        },
        {
            "order": 3,
            "label": "Text",
            "bbox": [230, 920, 810, 1600],
            "text": "left column more",
            "html": "left column more",
        },
        {
            "order": 4,
            "label": "Text",
            "bbox": [870, 200, 1460, 900],
            "text": "right column body",
            "html": "right column body",
        },
        {
            "order": 5,
            "label": "Text",
            "bbox": [870, 920, 1460, 1600],
            "text": "right column more",
            "html": "right column more",
        },
        # Surya reads a table of contents as ONE wide table spanning
        # both page columns, where the others emit two narrow ones. It
        # is far wider than its card, and must scroll inside it.
        {
            "order": 6,
            "label": "TableOfContents",
            "bbox": [230, 1650, 1460, 2000],
            "text": "wide table",
            "html": (
                "<table><tr>"
                + "".join(
                    f"<td>A very long case name, cited at page {n}</td>"
                    for n in range(6)
                )
                + "</tr></table>"
            ),
        },
    ]
    return {"stem": "x", "mode": "block", "text": "", "regions": regions}


def _containers() -> list[dict]:
    """What the container model does on an unredacted page: one
    whole-page image box and no columns at all."""
    return [{"label": "image", "bbox": [0, 0, 1700, 2200], "confidence": 0.7}]


class AlignTreeTestCase(SimpleTestCase):
    tmp: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp(prefix="align-tests-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        ds = cls.tmp / "datasets" / "newset"
        (ds / "page_png").mkdir(parents=True)
        (ds / "source").mkdir(parents=True)
        doc = fitz.open()
        for _ in PAGES:
            doc.new_page(width=RENDER_W / 2, height=RENDER_H / 2)
        doc.save(ds / "source" / f"{VOLUME}.pdf")
        doc.close()
        for engine in ("dots", "mistral", "surya/block", "container_yolo"):
            (ds / "engines" / engine).mkdir(parents=True)
        for i, page in enumerate(PAGES, start=1):
            stem = f"{VOLUME}__{page}"
            Image.new("RGB", (RENDER_W, RENDER_H), "white").save(
                ds / "page_png" / f"{stem}.png"
            )
            (ds / "engines" / "dots" / f"{stem}.json").write_text(
                json.dumps(_dots(i))
            )
            (ds / "engines" / "surya" / "block" / f"{stem}.json").write_text(
                json.dumps(_surya(i))
            )
            (ds / "engines" / "container_yolo" / f"{stem}.json").write_text(
                json.dumps(_containers())
            )
        # mistral read only the FIRST page: a page still builds when an
        # engine is missing, it just loses that column
        (ds / "engines" / "mistral" / f"{VOLUME}__page_0001.json").write_text(
            json.dumps(
                [
                    {
                        "type": "text",
                        "bbox": [230, 200, 810, 900],
                        "text": "left column body",
                    }
                ]
            )
        )
        cls.enterClassContext(
            override_settings(
                # The align surface reads the ALIGN_* roots. The route
                # roots point at an empty tree: the two surfaces must
                # never see each other's sets.
                ALIGN_DATA_ROOT=cls.tmp,
                ALIGN_DATASETS_ROOT=cls.tmp / "datasets",
                ALIGN_ARTIFACTS_ROOT=cls.tmp / "artifacts",
                DATA_ROOT=cls.tmp / "route_data",
                DATASETS_ROOT=cls.tmp / "route_data" / "datasets",
                ARTIFACTS_ROOT=cls.tmp / "route_data" / "artifacts",
                WEIGHTS_ROOT=cls.tmp / "route_data" / "weights",
                ROOT_URLCONF="extraction.urls",
            )
        )


class PrerenderedDiscoveryTest(AlignTreeTestCase):
    def test_pages_come_from_the_renders(self) -> None:
        pages = discover_pages(dataset(self.tmp, "newset"))
        self.assertEqual(
            [p.page_id for p in pages],
            [f"{VOLUME}__{p}" for p in PAGES],
        )

    def test_page_index_is_zero_based_off_a_one_based_name(self) -> None:
        pages = discover_pages(dataset(self.tmp, "newset"))
        self.assertEqual([p.page_index for p in pages], [0, 1])

    def test_source_volume_backs_every_page(self) -> None:
        for p in discover_pages(dataset(self.tmp, "newset")):
            self.assertTrue(p.src_pdf.exists())


class AlignRunTest(AlignTreeTestCase):
    def test_every_page_builds(self) -> None:
        written, skipped = run_dataset(self.tmp, "newset")
        self.assertEqual((written, skipped), (2, 0))

    def test_page_without_one_engine_still_builds(self) -> None:
        doc = align_data.ensure_align("newset", f"{VOLUME}__page_0002")
        self.assertEqual(doc["engines"], ["dots", "surya"])
        self.assertEqual(doc["missing_engines"], ["mistral"])

    def test_split_region_aligns_to_one_group(self) -> None:
        doc = align_data.ensure_align("newset", f"{VOLUME}__page_0001")
        first_body = next(g for g in doc["groups"] if g["band"] == "body")
        self.assertEqual(first_body["engines"]["surya"]["n_boxes"], 2)
        self.assertEqual(
            first_body["engines"]["surya"]["text"], "left column body"
        )
        self.assertEqual(
            first_body["engines"]["dots"]["text"], "left column body"
        )

    def test_columns_come_from_the_boxes_not_the_containers(self) -> None:
        doc = align_data.ensure_align("newset", f"{VOLUME}__page_0001")
        self.assertEqual(doc["column_boundary"], 850.0)
        self.assertEqual(doc["containers"]["columns"], [])

    def test_groups_read_left_column_before_right(self) -> None:
        doc = align_data.ensure_align("newset", f"{VOLUME}__page_0001")
        body = [g for g in doc["groups"] if g["band"] == "body"]
        # ... and the page-spanning table reads last, in no column
        self.assertEqual(
            [g["column"] for g in body], ["L", "L", "R", "R", None]
        )

    def test_stale_schema_is_rebuilt(self) -> None:
        run_dataset(self.tmp, "newset")
        f = (
            self.tmp
            / "artifacts"
            / "newset"
            / ALIGN_DIR
            / f"{VOLUME}__page_0001.json"
        )
        f.write_text(json.dumps({"schema_version": 0}))
        self.assertGreater(
            align_data.ensure_align("newset", f"{VOLUME}__page_0001")[
                "schema_version"
            ],
            0,
        )

    def test_check_data_reads_align_against_its_own_schema(self) -> None:
        """The align dir is not a route: checking it against the route
        schema would call a current set stale and print a rebuild
        command that does not exist."""
        run_dataset(self.tmp, "newset")
        out = io.StringIO()
        call_command("check_data", dataset="newset", stdout=out)
        printed = out.getvalue()
        self.assertIn(f"✓ artifacts/{ALIGN_DIR}", printed)
        self.assertNotIn("--route align", printed)

    def test_corrupt_artifact_is_rebuilt(self) -> None:
        run_dataset(self.tmp, "newset")
        f = (
            self.tmp
            / "artifacts"
            / "newset"
            / ALIGN_DIR
            / f"{VOLUME}__page_0001.json"
        )
        f.write_text("{ truncated")
        doc = align_data.ensure_align("newset", f"{VOLUME}__page_0001")
        self.assertEqual(doc["page"], f"{VOLUME}__page_0001")


class AlignStatsTest(AlignTreeTestCase):
    def test_corpus_stats_aggregate_the_built_set(self) -> None:
        run_dataset(self.tmp, "newset")
        stats = align_data.corpus_stats("newset")
        assert stats is not None
        self.assertEqual(stats["pages"], 2)
        self.assertEqual(stats["two_column"], 2)
        self.assertGreater(stats["groups"], 0)

    def test_stats_are_cached_and_reused(self) -> None:
        run_dataset(self.tmp, "newset")
        first = align_data.corpus_stats("newset")
        cache = self.tmp / "artifacts" / "newset" / ".align_stats_cache.json"
        self.assertTrue(cache.exists())
        self.assertEqual(align_data.corpus_stats("newset"), first)

    def test_unbuilt_set_reports_nothing(self) -> None:
        self.assertIsNone(align_data.corpus_stats("nope"))

    def test_volumes_group_the_page_list(self) -> None:
        pages = align_data.dataset_pages("newset")
        self.assertEqual(
            align_data.volumes(pages),
            [{"volume": VOLUME, "first": f"{VOLUME}__page_0001", "n": 2}],
        )

    def test_pages_sort_by_number_not_lexicographically(self) -> None:
        self.assertEqual(
            sorted(["v__page_10", "v__page_2"], key=align_data.page_sort_key),
            ["v__page_2", "v__page_10"],
        )


class RootSeparationTest(AlignTreeTestCase):
    """The separate root IS the gate: neither surface may list the
    other's sets, or newset would surface with all 7 combinations —
    the silent column-interleave the align surface exists to avoid."""

    def test_align_set_is_invisible_to_the_route_surface(self) -> None:
        self.assertNotIn("newset", data.dataset_names())

    def test_route_root_is_invisible_to_align(self) -> None:
        self.assertEqual(align_data.dataset_names(), ["newset"])


class AlignViewTest(AlignTreeTestCase):
    def test_home_renders_the_set(self) -> None:
        run_dataset(self.tmp, "newset")
        r = self.client.get("/align/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "newset")
        self.assertContains(r, "aligned regions")

    def test_page_renders_regions_and_overlays(self) -> None:
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "group-card")
        self.assertContains(r, "ov-group")
        self.assertContains(r, "ov-split")
        self.assertContains(r, "left column body")

    def test_page_image_serves_from_the_align_root(self) -> None:
        """The render URL must not lean on the route surface: its
        DATASETS_ROOT is a DIFFERENT tree, so reversing page_img there
        breaks every align image (seen live as blank page panes)."""
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        img = r.context["img_url"]
        self.assertIn("/align/img/", img)
        resp = self.client.get(img)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_container_columns_are_reported_as_zero(self) -> None:
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        self.assertContains(r, "container columns")

    def test_read_through_picks_an_engine(self) -> None:
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/?read=surya")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "final-doc")

    def test_read_through_defaults_to_the_consensus(self) -> None:
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        self.assertContains(r, "Final (majority)")
        self.assertEqual(r.context["reading_is_final"], True)

    def test_unknown_engine_falls_back_to_the_consensus(self) -> None:
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/?read=nope")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["reading_is_final"], True)

    def test_every_region_card_shows_its_final_read(self) -> None:
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        self.assertContains(r, "agree-unanimous")
        for g in r.context["groups"]:
            self.assertTrue(g["final_html"])

    def test_the_consensus_survives_a_missing_engine(self) -> None:
        """Page 2 has no mistral: two agreeing engines still resolve."""
        doc = align_data.ensure_align("newset", f"{VOLUME}__page_0002")
        body = [g for g in doc["groups"] if g["band"] == "body"]
        self.assertTrue(
            all(g["consensus"]["html"] for g in body), "every region resolves"
        )

    def test_unknown_page_redirects_to_the_first(self) -> None:
        r = self.client.get("/align/newset/nosuchpage/")
        self.assertRedirects(
            r,
            f"/align/newset/{VOLUME}__page_0001/",
            fetch_redirect_response=False,
        )

    def test_a_wide_read_scrolls_inside_its_own_card(self) -> None:
        """A table wider than its card must scroll in place. Grid
        children default to min-width:auto, so without both the
        shrinkable track and the scroll container the widest engine's
        read drags the whole page sideways."""
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        self.assertContains(r, "<table>")
        self.assertContains(r, "read-body")
        css = (
            Path(__file__).parent
            / "static"
            / "viewer"
            / "css"
            / "tailwind.css"
        ).read_text()
        self.assertIn(".read-body{overflow-x:auto}", css)
        self.assertIn(".reads-grid>*{min-width:0}", css)
        # read-through has no per-card grid, so each paragraph is its
        # own scroll container
        self.assertContains(r, "align-doc")
        rule = next(
            c for c in css.split("}") if ".align-doc>p" in c.split("{")[0]
        )
        self.assertIn("overflow-x:auto", rule)

    def test_low_confidence_marks_look_the_same_everywhere(self) -> None:
        """The same marked word shows up in the region cards
        (.doc-html), in read-through (.final-doc), and in the legend
        that explains it. Only .final-doc was styled, so the other two
        fell back to the browser's yellow <mark> and read as a second,
        different signal."""
        css = (
            Path(__file__).parent
            / "static"
            / "viewer"
            / "css"
            / "tailwind.css"
        ).read_text()
        selectors = {
            rule.split("{")[0].strip()
            for rule in css.split("}")
            if "mark.low-confidence" in rule.split("{")[0]
        }
        self.assertIn(
            "mark.low-confidence",
            selectors,
            "an UNSCOPED rule is what covers .doc-html and the legends",
        )
        unscoped = next(
            r for r in css.split("}") if r.strip().startswith("mark.low-")
        )
        self.assertIn("225,29,72", unscoped.replace(" ", ""))

    def test_the_voted_bar_is_explained_in_the_legend(self) -> None:
        """The amber rule down a paragraph is the only cue that its text
        was voted rather than read; unlabelled, nobody can tell what it
        means."""
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/")
        self.assertContains(r, "legend-voted")
        self.assertContains(r, "voted word by word")

    def test_the_bar_and_its_legend_share_one_rule(self) -> None:
        css = (
            Path(__file__).parent
            / "static"
            / "viewer"
            / "css"
            / "tailwind.css"
        ).read_text()
        rule = next(
            r for r in css.split("}") if ".legend-voted" in r.split("{")[0]
        )
        # the minifier drops the attribute value's quotes
        self.assertIn("data-agreement=voted]", rule.replace('"', ""))

    def test_only_the_consensus_read_marks_agreement(self) -> None:
        """A single engine's read has no agreement to report, so no
        paragraph should carry a bar."""
        r = self.client.get(f"/align/newset/{VOLUME}__page_0001/?read=dots")
        self.assertNotContains(r, "data-agreement")

    def test_no_template_comment_leaks(self) -> None:
        """A multi-line {# #} renders literally — guard every page."""
        for url in ("/align/", f"/align/newset/{VOLUME}__page_0001/"):
            self.assertNotContains(self.client.get(url), "{#")
