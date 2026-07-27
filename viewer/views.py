from urllib.parse import urlencode

from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from pipeline import routes
from pipeline.core.config import RENDER_H, RENDER_W
from pipeline.engines.info import ENGINE_INFO
from viewer import data

_CONTAINER_CLS = "ov-cont"
_SUPP_CHIP = {
    "dots": "border-red-600",
    "mistral": "border-fuchsia-700",
    "gemini": "border-fuchsia-700",
    "surya": "border-emerald-600",
}
# Alpine CSP components use fixed property/method names; supplemental
# overlay slots are capped at two (the widest route runs two).
_SUPP_TOGGLES = (("showSupp", "toggleSupp"), ("showSupp2", "toggleSupp2"))

# Dropdown labels for the model slugs (slug = value everywhere).
_MODEL_LABELS = {
    "dots": "dots",
    "mistral": "mistral",
    "gemini": "gemini",
    "surya_block": "surya block",
    "surya_line": "surya line",
    routes.TIEBREAK_ONLY: "lighton (tiebreaker)",
}
# the combination the flow opens with: dots ∥ mistral ∥ surya block,
# resolved by direct three-way vote
_DEFAULT_COMBO = routes.PRESETS["three_way"]


def _selector_ctx(sel: tuple[str, str, str]) -> dict:
    """Context for the three model dropdowns. Slots 1+2 offer the OCR
    models; slot 3 offers LightOn (tiebreak) or a third model (direct
    three-way vote)."""
    primary = [{"value": s, "label": _MODEL_LABELS[s]} for s in routes.MODELS]
    third = [
        {
            "value": s,
            "label": _MODEL_LABELS[s],
        }
        for s in (routes.TIEBREAK_ONLY, *routes.MODELS)
    ]
    return {
        "primary_options": primary,
        "third_options": third,
        "sel": {"m1": sel[0], "m2": sel[1], "m3": sel[2]},
    }


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
    """Landing page: one door into the pipeline flow, plus the
    model/architecture reference. Dataset, models, and page are all
    picked on the flow page itself."""
    return TemplateResponse(
        request,
        "viewer/home.html",
        {
            "has_datasets": bool(data.list_datasets()),
            "engine_info": ENGINE_INFO,
            "error": request.GET.get("error", ""),
        },
    )


async def route_go(request: HttpRequest) -> HttpResponse:
    """Land on the walkthrough for the selected dataset + three models
    (all optional — defaults fill in; invalid combinations bounce back
    with the reason). The home button hits this with no params."""
    names = [info.name for info in data.list_datasets()]
    if not names:
        return redirect("home")
    dataset = request.GET.get("dataset", "")
    if dataset not in names:
        dataset = names[0]
    m1 = request.GET.get("m1", _DEFAULT_COMBO[0])
    m2 = request.GET.get("m2", _DEFAULT_COMBO[1])
    m3 = request.GET.get("m3", _DEFAULT_COMBO[2])
    try:
        route = routes.compose(m1, m2, m3)
    except ValueError as exc:
        return redirect(f"{reverse('home')}?{urlencode({'error': exc})}")
    page_id = request.GET.get("page", "")
    pages = data.dataset_pages(dataset)
    if not pages:
        return redirect("home")
    if page_id not in pages:
        page_id = pages[0]
    return redirect("page", dataset, route.name, page_id)


async def dataset_redirect(request: HttpRequest, dataset: str) -> HttpResponse:
    """Convenience: a dataset link lands on its first page under the
    default combination."""
    pages = data.dataset_pages(dataset)
    if not pages:
        return redirect("home")
    route = routes.compose(*_DEFAULT_COMBO)
    return redirect("page", dataset, route.name, pages[0])


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
        "no_bbox": rsupp.get("no_bbox", []),
        "n_omitted": len(rsupp.get("omitted", [])),
        "redactions": [
            {"id": x["id"], "black_frac": x.get("black_frac")}
            for x in (
                by_id[r] for r in rsupp.get("redactions", []) if r in by_id
            )
        ],
        "star_pages": rsupp.get("star_pages", []),
        "_dropped_items": dropped,
        "_in_image_items": in_image,
    }


def _norm_ctx(norm: dict | None) -> dict | None:
    """Stage-6 card context: the registry with per-rule change records
    (grouped per stream), plus each stream's final key text."""
    if not norm:
        return None
    streams = [
        {"label": f"{norm['main']['engine']} (main)", **norm["main"]}
    ] + [
        {
            "label": f"{s['engine']} {s['unit']} (supplemental)"
            if s["engine"] == "surya"
            else f"{s['engine']} (supplemental)",
            **s,
        }
        for s in norm["supplementals"]
    ]
    for s in streams:
        s["key_text"] = " ".join(t["key"] for t in s["tokens"])
        s["n_tokens"] = len(s["tokens"])
    metas = norm["registry"]["rules"]
    stream_metas = [m for m in metas if m.get("kind", "stream") == "stream"]
    rules = []
    for i, meta in enumerate(stream_metas):
        field = "display" if meta["applies_to"] == "display" else "key"
        per_stream = []
        for s in streams:
            rec = s["rules"][i]  # records follow stream-rule order
            per_stream.append(
                {
                    "label": s["label"],
                    "applied": rec["applied"],
                    "n_changed": rec["n_changed"],
                    "changes": [
                        {
                            "at": c["at"],
                            "before": " ".join(t[field] for t in c["before"]),
                            "after": " ".join(t[field] for t in c["after"]),
                        }
                        for c in rec["changes"]
                    ],
                }
            )
        rules.append(
            {
                **meta,
                "streams": per_stream,
                "total": sum(p["n_changed"] for p in per_stream),
            }
        )
    return {
        "hash": norm["registry"]["hash"],
        "rules": rules,
        "decode_rules": [m for m in metas if m.get("kind") == "decode"],
        "streams": streams,
    }


async def page(
    request: HttpRequest, dataset: str, route: str, page: str
) -> HttpResponse:
    """The pipeline walkthrough: one page, one model combination,
    stages 1-6. The artifact materializes on first visit."""
    try:
        route_obj = routes.parse(route)
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    artifact = data.ensure_artifact(dataset, route_obj, page)
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

    main_engine = stages["main_ocr"]["engine"]
    main_unit = stages["main_ocr"].get("unit", "block")
    layers = {
        "containers": _boxes(stages["layout"]["post"]),
        "yolo_raw": _boxes(stages["layout"]["raw"], extra="ov-raw"),
        "main": _boxes(
            stages["main_ocr"]["blocks"], cls_for=f"ov-{main_engine}"
        ),
        "dropped": _boxes(dropped_items, cls_for="ov-dropped"),
        "in_image": _boxes(in_image_items, cls_for="ov-inimg"),
    }

    pages = data.dataset_pages(dataset)
    idx = pages.index(page) if page in pages else 0
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
        "main_redactions": [
            {"id": b["id"], "black_frac": b.get("black_frac")}
            for b in stages["main_ocr"]["blocks"]
            if b["id"]
            in set((recon or {}).get("main", {}).get("redactions", []))
        ],
        "norm": _norm_ctx(stages.get("normalize")),
        "main_label": (
            f"{main_engine} {main_unit}"
            if main_engine == "surya"
            else main_engine
        ),
        "raws": {
            "layout": data.engine_raw(dataset, page, "container_yolo"),
            "main": data.engine_raw(
                dataset,
                page,
                main_engine,
                variant=main_unit if main_engine == "surya" else None,
            ),
        },
        "layers": layers,
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
        "dataset_options": [info.name for info in data.list_datasets()],
        **_selector_ctx(tuple(route.split("+"))),  # type: ignore[arg-type]
    }
    return TemplateResponse(request, "viewer/page.html", ctx)


async def page_img(
    request: HttpRequest, dataset: str, page: str
) -> FileResponse:
    """The canonical black-redacted render of a page."""
    path = data.page_png_path(dataset, page)
    return FileResponse(open(path, "rb"), content_type="image/png")
