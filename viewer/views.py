from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from pipeline.core.config import RENDER_H, RENDER_W
from pipeline.engines.info import ENGINE_INFO
from pipeline.routes import ROUTES
from viewer import data

_CONTAINER_CLS = "ov-cont"
_SUPP_CHIP = {
    "mistral": "border-fuchsia-700",
    "gemini": "border-fuchsia-700",
    "surya": "border-emerald-600",
}
# Alpine CSP components use fixed property/method names; supplemental
# overlay slots are capped at two (the widest route runs two).
_SUPP_TOGGLES = (("showSupp", "toggleSupp"), ("showSupp2", "toggleSupp2"))


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
    """Landing page: every sample with one button per route, plus the
    model/architecture reference."""
    datasets = []
    for info in data.list_datasets():
        route_pages = {
            name: set(data.route_pages(info.name, name))
            for name in ROUTES
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
                    for name in ROUTES
                ],
            }
            for p in sorted(pages)
        ]
        datasets.append({"info": info, "rows": rows})
    return TemplateResponse(
        request,
        "viewer/home.html",
        {"datasets": datasets, "engine_info": ENGINE_INFO},
    )


async def dataset(request: HttpRequest, dataset: str) -> HttpResponse:
    """Convenience: a dataset link lands on its first walkable page."""
    for name in ROUTES:
        pages = data.route_pages(dataset, name)
        if pages:
            return redirect("page", dataset, name, pages[0])
    return redirect("home")


def _supp_ctx(
    dataset: str, page: str, index: int, supp: dict, rsupp: dict
) -> dict:
    items = supp.get("blocks") or supp.get("lines") or []
    by_id = {it["id"]: it for it in items}
    dropped = [by_id[x] for x in rsupp.get("dropped", []) if x in by_id]
    in_image = [by_id[x] for x in rsupp.get("in_image", []) if x in by_id]
    prop, toggle = _SUPP_TOGGLES[min(index, len(_SUPP_TOGGLES) - 1)]
    if supp["engine"] == "surya":
        label = f"surya {supp['unit']}"
        raw = data.engine_raw(dataset, page, "surya", variant=supp["unit"])
    else:
        label = supp["engine"]
        raw = data.engine_raw(dataset, page, supp["engine"])
    return {
        "supp": supp,
        "items": items,
        "label": label,
        "chip": _SUPP_CHIP.get(supp["engine"], "border-fuchsia-700"),
        "prop": prop,
        "toggle": toggle,
        "boxes": _boxes(items, cls_for=f"ov-{supp['engine']}"),
        "raw": raw,
        "recon_items": rsupp.get("items", []),
        "dropped": [
            {"id": x["id"], "text": x.get("text", "")} for x in dropped
        ],
        "in_image": [
            {"id": x["id"], "text": x.get("text", "")} for x in in_image
        ],
        "n_omitted": len(rsupp.get("omitted", [])),
        "_dropped_items": dropped,
        "_in_image_items": in_image,
    }


async def page(
    request: HttpRequest, dataset: str, route: str, page: str
) -> HttpResponse:
    """The pipeline walkthrough: one page, one route, stages 1-5."""
    artifact = data.load_artifact(dataset, route, page)
    stages = artifact["stages"]
    recon = stages.get("reconstruct")
    recon_supps = (recon or {}).get("supplementals", [])

    supps = []
    dropped_items: list[dict] = []
    in_image_items: list[dict] = []
    for i, supp in enumerate(stages["supplementals"]):
        rsupp = recon_supps[i] if i < len(recon_supps) else {}
        ctx_i = _supp_ctx(dataset, page, i, supp, rsupp)
        dropped_items += ctx_i.pop("_dropped_items")
        in_image_items += ctx_i.pop("_in_image_items")
        supps.append(ctx_i)

    layers = {
        "containers": _boxes(stages["layout"]["post"]),
        "yolo_raw": _boxes(stages["layout"]["raw"], extra="ov-raw"),
        "dots": _boxes(stages["main_ocr"]["blocks"], cls_for="ov-dots"),
        "dropped": _boxes(dropped_items, cls_for="ov-dropped"),
        "in_image": _boxes(in_image_items, cls_for="ov-inimg"),
    }

    pages = data.route_pages(dataset, route)
    idx = pages.index(page) if page in pages else 0
    routes = [
        {
            "name": name,
            "url": reverse("page", args=[dataset, name, page]),
            "active": name == route,
        }
        for name in ROUTES
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
        "supps": supps,
        "recon": {"main": recon["main"]["items"]} if recon else None,
        "raws": {
            "layout": data.engine_raw(dataset, page, "container_yolo"),
            "dots": data.engine_raw(dataset, page, "dots"),
        },
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
