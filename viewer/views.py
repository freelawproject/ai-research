from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from pipeline.core.config import RENDER_H, RENDER_W
from pipeline.routes import ROUTES
from viewer import data

# Overlay layers the image panel can toggle; colors live in input.css.
_CONTAINER_CLS = "ov-cont"


def _pct_style(bbox: list[float]) -> str:
    left = 100 * bbox[0] / RENDER_W
    top = 100 * bbox[1] / RENDER_H
    width = 100 * (bbox[2] - bbox[0]) / RENDER_W
    height = 100 * (bbox[3] - bbox[1]) / RENDER_H
    return (
        f"left:{left:.2f}%;top:{top:.2f}%;"
        f"width:{width:.2f}%;height:{height:.2f}%"
    )


def _boxes(
    items: list[dict],
    cls_for: str | None = None,
    extra: str = "",
) -> list[dict]:
    out = []
    for it in items:
        if not it.get("bbox"):
            continue
        label = it.get("label") or it.get("type") or it.get("tag") or ""
        cls = cls_for or f"ov-{label} {_CONTAINER_CLS}"
        if extra:
            cls = f"{cls} {extra}"
        out.append(
            {
                "style": _pct_style(it["bbox"]),
                "cls": cls,
                "title": f"{it.get('id', '')} {label}".strip(),
            }
        )
    return out


async def home(request: HttpRequest) -> HttpResponse:
    """Landing page: every sample, with one button per route."""
    datasets = []
    for info in data.list_datasets():
        route_pages = {
            name: set(data.route_pages(info.name, name))
            for name in sorted(ROUTES)
        }
        pages: set[str] = set()
        for ids in route_pages.values():
            pages |= ids
        rows = [
            {
                "id": p,
                "routes": [
                    {
                        "name": name,
                        "url": (
                            reverse("page", args=[info.name, name, p])
                            if p in route_pages[name]
                            else None
                        ),
                    }
                    for name in sorted(ROUTES)
                ],
            }
            for p in sorted(pages)
        ]
        datasets.append({"info": info, "rows": rows})
    return TemplateResponse(
        request, "viewer/home.html", {"datasets": datasets}
    )


async def dataset(request: HttpRequest, dataset: str) -> HttpResponse:
    """Convenience: a dataset link lands on its first walkable page."""
    for name in sorted(ROUTES):
        pages = data.route_pages(dataset, name)
        if pages:
            return redirect("page", dataset, name, pages[0])
    return redirect("home")


async def page(
    request: HttpRequest, dataset: str, route: str, page: str
) -> HttpResponse:
    """The pipeline walkthrough: one page, one route, stages 1-4."""
    artifact = data.load_artifact(dataset, route, page)
    stages = artifact["stages"]
    supp = stages["supplemental"]

    pages = data.route_pages(dataset, route)
    idx = pages.index(page) if page in pages else 0
    supp_items = supp.get("blocks") or supp.get("lines") or []
    layers = {
        "containers": _boxes(stages["layout"]["post"]),
        "yolo_raw": _boxes(stages["layout"]["raw"], extra="ov-raw"),
        "dots": _boxes(stages["main_ocr"]["blocks"], cls_for="ov-dots"),
        "supp": _boxes(supp_items, cls_for=f"ov-{supp['engine']}"),
    }
    routes = [
        {
            "name": name,
            "url": reverse("page", args=[dataset, name, page]),
            "active": name == route,
        }
        for name in sorted(ROUTES)
        if page in data.route_pages(dataset, name) or name == route
    ]
    page_options = [
        {
            "id": p,
            "url": reverse("page", args=[dataset, route, p]),
            "selected": p == page,
        }
        for p in pages
    ]
    ctx = {
        "artifact": artifact,
        "stages": stages,
        "supp": supp,
        "layers": layers,
        "routes": routes,
        "dataset": dataset,
        "route": route,
        "page_id": page,
        "page_options": page_options,
        "pos": f"{idx + 1}/{len(pages)}",
        "prev_url": (
            reverse("page", args=[dataset, route, pages[idx - 1]])
            if idx > 0
            else None
        ),
        "next_url": (
            reverse("page", args=[dataset, route, pages[idx + 1]])
            if idx + 1 < len(pages)
            else None
        ),
        "img_url": reverse("page_img", args=[dataset, page]),
    }
    return TemplateResponse(request, "viewer/page.html", ctx)


async def page_img(
    request: HttpRequest, dataset: str, page: str
) -> FileResponse:
    """The canonical black-redacted render of a page."""
    path = data.page_png_path(dataset, page)
    return FileResponse(open(path, "rb"), content_type="image/png")
