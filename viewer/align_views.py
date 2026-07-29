"""Alignment viewer — what every engine read of the same region.

A separate surface from the route walkthrough. Regions are aligned
across engines by geometry, put in reading order from their own
coordinates, and resolved to what the engines agree on — the page reads
as that consensus by default, with each engine one click behind it.
Container-YOLO is drawn over the page image and nothing more: on
unredacted sets it finds no columns, which is worth SEEING rather than
letting it drive placement.
"""

from __future__ import annotations

from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from pipeline.core.config import RENDER_W
from pipeline.core.consensus import MAJORITY, SINGLE, UNANIMOUS, VOTED
from viewer import align_data
from viewer.views import _pct_style  # one source of overlay geometry

# Engine -> the overlay class its boxes get (defined in input.css).
ENGINE_CLS = {
    "dots": "ov-dots",
    "mistral": "ov-mistral",
    "surya": "ov-surya",
}
ENGINE_LABEL = {
    "dots": "dots.ocr",
    "mistral": "Mistral OCR",
    "surya": "Surya",
}
# The consensus read is picked like an engine, and is the default.
FINAL = "final"
FINAL_LABEL = "Final (majority)"
AGREEMENT_LABEL = {
    UNANIMOUS: "all engines agree",
    MAJORITY: "majority agree",
    VOTED: "voted word by word",
    SINGLE: "one engine only",
}
# Below this, the engines' merged boxes barely overlap: the GROUP is
# suspect, so any text difference it shows may be an alignment artefact.
WEAK_IOU = 0.3


def _boxes(items: list[dict], cls: str, title: str = "") -> list[dict]:
    out = []
    for it in items:
        bbox = it.get("bbox")
        if not bbox:
            continue
        label = it.get("label") or it.get("type") or ""
        out.append(
            {
                "style": _pct_style(bbox),
                "cls": cls,
                "title": f"{title} {label}".strip() or label,
            }
        )
    return out


def _engine_boxes(doc: dict, engine: str) -> list[dict]:
    """One engine's merged box per group it contributed to."""
    out = []
    for g in doc["groups"]:
        merged = g["engines"].get(engine)
        if merged is None:
            continue
        out.append(
            {
                "style": _pct_style(merged["bbox"]),
                "cls": ENGINE_CLS[engine],
                "title": (
                    f"#{g['id']} {engine} {'+'.join(merged['labels'][:3])}"
                ),
            }
        )
    return out


def _group_rows(doc: dict, engines: list[str]) -> list[dict]:
    rows = []
    for g in doc["groups"]:
        reads = []
        for e in engines:
            merged = g["engines"].get(e)
            reads.append(
                {
                    "engine": e,
                    "label": ENGINE_LABEL.get(e, e),
                    "present": merged is not None,
                    "html": merged["html"] if merged else "",
                    "text": merged["text"] if merged else "",
                    "n_boxes": merged["n_boxes"] if merged else 0,
                    "labels": ", ".join(merged["labels"]) if merged else "",
                }
            )
        final = g["consensus"]
        rows.append(
            {
                "id": g["id"],
                "agreement": final["agreement"],
                "agreement_label": AGREEMENT_LABEL.get(
                    final["agreement"], final["agreement"]
                ),
                "voted": final["agreement"] == VOTED,
                "n_low_confidence": final["n_low_confidence"],
                "final_html": final["html"],
                "band": g["band"],
                "column": g["column"] or "—",
                "style": _pct_style(g["bbox"]),
                "iou": g["alignment_iou"],
                "weak": g["alignment_iou"] < WEAK_IOU,
                "page_scale": g["page_scale"],
                "missing": [
                    ENGINE_LABEL.get(e, e)
                    for e in engines
                    if e not in g["present"]
                ],
                "reads": reads,
            }
        )
    return rows


def _reading(doc: dict, engine: str) -> list[dict]:
    """The page in geometric reading order — one engine's read, or the
    consensus of all of them."""
    out = []
    for g in doc["groups"]:
        if engine == FINAL:
            final = g["consensus"]
            html, extra = final["html"], final["agreement"]
        else:
            merged = g["engines"].get(engine)
            if merged is None:
                continue
            html, extra = merged["html"], ""
        if not html:
            continue
        out.append(
            {
                "id": g["id"],
                "band": g["band"],
                "html": html,
                "agreement": extra,
            }
        )
    return out


def _nav(dataset: str, pages: list[str], page: str) -> dict:
    """Volume + page navigation. A 3,500-page set cannot go in a page
    <select> the way the bundled samples do, so the volume picks the
    set and prev/next walks it."""
    idx = pages.index(page)
    here = align_data.volume_of(page)

    def url_of(p: str) -> str:
        return reverse("align_page", args=[dataset, p])

    return {
        "pos": f"{idx + 1}/{len(pages)}",
        "prev_url": url_of(pages[idx - 1]) if idx > 0 else None,
        "next_url": url_of(pages[idx + 1]) if idx + 1 < len(pages) else None,
        "volume_options": [
            {
                **v,
                "url": url_of(v["first"]),
                "selected": v["volume"] == here,
            }
            for v in align_data.volumes(pages)
        ],
        "volume": here,
    }


def align_home(request: HttpRequest) -> HttpResponse:
    """Set picker plus what the alignment found across the whole set."""
    names = align_data.dataset_names()
    selected = request.GET.get("dataset", "")
    if selected not in names:
        selected = names[0] if names else ""
    pages = align_data.dataset_pages(selected) if selected else []
    return TemplateResponse(
        request,
        "viewer/align_home.html",
        {
            "datasets": names,
            "dataset": selected,
            "n_pages": len(pages),
            "first_url": (
                reverse("align_page", args=[selected, pages[0]])
                if pages
                else None
            ),
            "volumes": align_data.volumes(pages),
            "stats": align_data.corpus_stats(selected) if selected else None,
            "engine_labels": ENGINE_LABEL,
        },
    )


async def align_page_img(
    request: HttpRequest, dataset: str, page: str
) -> FileResponse:
    """The canonical render of an align set's page. The route surface's
    page_img reads a different root, so the align surface serves its own."""
    path = align_data.page_png_path(dataset, page)
    return FileResponse(open(path, "rb"), content_type="image/png")


def align_page(request: HttpRequest, dataset: str, page: str) -> HttpResponse:
    pages = align_data.dataset_pages(dataset)
    if not pages:
        raise Http404(f"no pages in {dataset}")
    if page not in pages:
        return redirect("align_page", dataset=dataset, page=pages[0])
    doc = align_data.ensure_align(dataset, page)
    engines = doc["engines"]
    # the consensus is the default read: per-engine is the drill-down
    reading_engine = request.GET.get("read", "")
    if reading_engine not in {FINAL, *engines}:
        reading_engine = FINAL

    containers = doc["containers"]
    boundary = doc["column_boundary"]
    return TemplateResponse(
        request,
        "viewer/align.html",
        {
            "dataset": dataset,
            "datasets": align_data.dataset_names(),
            "page_id": page,
            "img_url": reverse("align_page_img", args=[dataset, page]),
            "engines": [
                {
                    "name": e,
                    "label": ENGINE_LABEL.get(e, e),
                    "cls": ENGINE_CLS[e],
                    "boxes": _engine_boxes(doc, e),
                    "selected": e == reading_engine,
                    "read_url": (
                        f"{reverse('align_page', args=[dataset, page])}"
                        f"?read={e}"
                    ),
                }
                for e in engines
            ],
            "read_options": [
                {
                    "name": FINAL,
                    "label": FINAL_LABEL,
                    "selected": reading_engine == FINAL,
                    "url": (
                        f"{reverse('align_page', args=[dataset, page])}"
                        f"?read={FINAL}"
                    ),
                },
                *(
                    {
                        "name": e,
                        "label": ENGINE_LABEL.get(e, e),
                        "selected": e == reading_engine,
                        "url": (
                            f"{reverse('align_page', args=[dataset, page])}"
                            f"?read={e}"
                        ),
                    }
                    for e in engines
                ),
            ],
            "missing_engines": [
                ENGINE_LABEL.get(e, e) for e in doc["missing_engines"]
            ],
            "layers": {
                "containers": _boxes(
                    containers["post"], "ov ov-cont", "container"
                ),
                "yolo_raw": _boxes(
                    containers["raw"], "ov ov-cont ov-raw", "raw"
                ),
                "groups": [
                    {
                        "style": _pct_style(g["bbox"]),
                        "cls": "ov ov-group",
                        "title": f"#{g['id']} {'+'.join(g['present'])}",
                    }
                    for g in doc["groups"]
                ],
                "weak": [
                    {
                        "style": _pct_style(g["bbox"]),
                        "cls": "ov ov-weak",
                        "title": (
                            f"#{g['id']} merged boxes overlap only "
                            f"{g['alignment_iou']}"
                        ),
                    }
                    for g in doc["groups"]
                    if g["alignment_iou"] < WEAK_IOU
                ],
            },
            "boundary_pct": (
                f"{100 * boundary / RENDER_W:.2f}%" if boundary else None
            ),
            "boundary": boundary,
            "n_columns": len(containers["columns"]),
            "groups": _group_rows(doc, engines),
            "reading": _reading(doc, reading_engine),
            "reading_engine": (
                FINAL_LABEL
                if reading_engine == FINAL
                else ENGINE_LABEL.get(reading_engine, reading_engine)
            ),
            "reading_is_final": reading_engine == FINAL,
            "metrics": doc["metrics"],
            "params": doc["params"],
            **_nav(dataset, pages, page),
        },
    )
