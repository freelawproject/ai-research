from urllib.parse import urlencode

from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from pipeline import routes
from pipeline.core.config import RENDER_H, RENDER_W
from viewer import data
from viewer.info import ENGINE_INFO

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
    routes.TIEBREAK_ONLY: "lighton (tiebreaker)",
}
# the combination the flow opens with: dots ∥ mistral ∥ surya block,
# resolved by direct three-way vote
_DEFAULT_COMBO = routes.PRESETS["three_way"]


def _pretty(name: str) -> str:
    """Display form of a model slug or engine name ("surya_block" and
    the surya engine both read as "surya block")."""
    return "surya block" if name in ("surya_block", "surya") else name


def _labels_for(streams: list[dict]) -> dict[str, str]:
    """Read labels per compare-stream key ("main", "supp1", ...,
    "lighton"), named by engine."""
    label_of = {
        "main": f"{_pretty(streams[0]['engine'])} (main)",
        "lighton": "lighton",
    }
    for s in streams[1:]:
        label_of[s["label"]] = _pretty(s["engine"])
    return label_of


def _read_rows(dispute: dict, label_of: dict[str, str]) -> list[dict]:
    """One row per read of a dispute, the winning read marked."""
    return [
        {
            "label": label_of.get(k, k),
            "keys": v,
            "display": dispute["displays"].get(k, ""),
            "winner": k == dispute["verdict"],
        }
        for k, v in dispute["reads"].items()
    ]


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
    """Landing page: one door into the pipeline flow, the per-route
    compare + resolve stats for every composable combination, and the
    model/architecture reference. Dataset, models, and page are all
    picked on the flow page itself."""
    names = data.dataset_names()
    stats = []
    for name in names:
        pages = data.dataset_pages(name)
        rows = data.route_stats(name)
        for r in rows:
            r["url"] = (
                reverse("page", args=[name, r["route"], pages[0]])
                if pages
                else None
            )
            r["engine_labels"] = [_pretty(s) for s in r["engines"]]
        stats.append({"dataset": name, "n_pages": len(pages), "rows": rows})
    return TemplateResponse(
        request,
        "viewer/home.html",
        {
            "has_datasets": bool(names),
            "stats": stats,
            "engine_info": ENGINE_INFO,
            "error": request.GET.get("error", ""),
        },
    )


async def review(
    request: HttpRequest, dataset: str, category: str
) -> HttpResponse:
    """The review pages behind the stats table's column headers: every
    dispute of one category, across all combinations of a dataset.
    Each entry links to the walkthrough page holding its dispute
    card."""
    meta = data.REVIEW_CATEGORIES.get(category)
    if meta is None:
        raise Http404(f"no review category {category}")
    title, description = meta["title"], meta["description"]
    entries = []
    for e in data.review_disputes(dataset, category):
        label_of = _labels_for(e["streams"])
        d = e["dispute"]
        entries.append(
            {
                "combo": " + ".join(_pretty(s) for s in e["engines"]),
                "resolver": (
                    "lighton tiebreak" if e["tiebreak"] else "3-way vote"
                ),
                "page": e["page"],
                "url": reverse("page", args=[dataset, e["route"], e["page"]]),
                "resolution": d["resolution"],
                "reason": d.get("reason"),
                "roles": ", ".join(d["roles"]),
                "blocks": d["blocks"],
                "read_rows": _read_rows(d, label_of),
            }
        )
    return TemplateResponse(
        request,
        "viewer/review.html",
        {
            "dataset": dataset,
            "category": category,
            "title": title,
            "description": description,
            "entries": entries,
        },
    )


async def route_go(request: HttpRequest) -> HttpResponse:
    """Land on the walkthrough for the selected dataset + three models
    (all optional — defaults fill in; invalid combinations bounce back
    with the reason). The home button hits this with no params."""
    names = data.dataset_names()
    if not names:
        return redirect("home")
    dataset = request.GET.get("dataset", "")
    if dataset not in names:
        dataset = names[0]
    # `or` (not a get() default): the home form submits empty values
    # for anything never picked — empty means "use the default" too
    m1 = request.GET.get("m1") or _DEFAULT_COMBO[0]
    m2 = request.GET.get("m2") or _DEFAULT_COMBO[1]
    m3 = request.GET.get("m3") or _DEFAULT_COMBO[2]
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
    items = supp.get("blocks") or []
    by_id = {it["id"]: it for it in items}
    in_image = [by_id[x] for x in rsupp.get("in_image", []) if x in by_id]
    prop, toggle = _SUPP_TOGGLES[min(index, len(_SUPP_TOGGLES) - 1)]
    label = _pretty(supp["engine"])
    if supp["engine"] == "surya":
        raw = data.engine_raw(dataset, page, "surya", variant=supp["unit"])
    else:
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


def _crop_urls(dataset: str, page: str, bboxes: list[list[int]]) -> list[str]:
    """URLs of the cached crop images that actually exist for these
    bboxes (a read can be cached without its image)."""
    urls = []
    for bbox in bboxes:
        key = "_".join(str(int(c)) for c in bbox)
        if data.lighton_crop_png(dataset, page, key) is not None:
            urls.append(reverse("crop_img", args=[dataset, page, key]))
    return urls


def _compare_ctx(
    cmp: dict | None,
    dataset: str,
    page: str,
) -> dict | None:
    """Compare-card context: the dispute cards (reads labeled by
    engine, the winner marked, tiebreak trace + crop images where
    cached) plus the page metrics."""
    if not cmp:
        return None
    label_of = _labels_for(cmp["streams"])
    disputes = []
    for d in cmp["disputes"]:
        tb = d.get("tiebreak")
        disputes.append(
            {
                **d,
                "verdict_label": label_of.get(d["verdict"], d["verdict"]),
                "read_rows": _read_rows(d, label_of),
                "crop_urls": (
                    _crop_urls(dataset, page, tb["bboxes"]) if tb else []
                ),
            }
        )
    return {
        **cmp,
        "disputes": disputes,
        "marks": [
            {**m, "label": label_of.get(m["label"], m["label"])}
            for m in cmp["marks"]
            if m["supp"] or m["main"]
        ],
        "degraded_labels": [label_of.get(lb, lb) for lb in cmp["degraded"]],
        "skip_roles": ", ".join(cmp["params"]["skip_roles"]),
    }


def _dispute_boxes(cmp: dict | None, main_blocks: list[dict]) -> list[dict]:
    """One overlay box per disputed main block (low-confidence disputes
    filled)."""
    if not cmp:
        return []
    bbox_of = {b["id"]: b.get("bbox") for b in main_blocks}
    out = []
    for d in cmp["disputes"]:
        cls = "ov-dispute-low" if d["low_confidence"] else "ov-dispute"
        for block in d["blocks"]:
            bbox = bbox_of.get(block)
            if not bbox:
                continue
            out.append(
                {
                    "style": _pct_style(bbox),
                    "cls": f"ov {cls}",
                    "title": (
                        f"dispute #{d['id']} → {d['verdict']}"
                        f" ({d['resolution']})"
                    ),
                }
            )
    return out


async def page(
    request: HttpRequest, dataset: str, route: str, page: str
) -> HttpResponse:
    """The pipeline walkthrough: one page, one model combination,
    every stage. The artifact materializes on first visit. resolve()
    also accepts the CLI preset names, so pre-composition URLs
    (/d/<ds>/three_way/...) keep working; artifacts always land under
    the canonical combo name."""
    try:
        route_obj = routes.resolve(route)
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    artifact = data.ensure_artifact(dataset, route_obj, page)
    stages = artifact["stages"]
    recon = stages.get("reconstruct")
    recon_supps = (recon or {}).get("supplementals", [])

    supps = []
    in_image_items: list[dict] = []
    for i, supp in enumerate(stages["supplementals"]):
        rsupp = recon_supps[i] if i < len(recon_supps) else {}
        ctx_i = _supp_ctx(dataset, page, i, supp, rsupp)
        in_image_items += ctx_i.pop("_in_image_items")
        supps.append(ctx_i)

    main_engine = stages["main_ocr"]["engine"]
    main_unit = stages["main_ocr"].get("unit", "block")
    main_label = _pretty(main_engine)
    layers = {
        "containers": _boxes(stages["layout"]["post"]),
        "yolo_raw": _boxes(stages["layout"]["raw"], extra="ov-raw"),
        "main": _boxes(
            stages["main_ocr"]["blocks"], cls_for=f"ov-{main_engine}"
        ),
        "in_image": _boxes(in_image_items, cls_for="ov-inimg"),
        "disputes": _dispute_boxes(
            stages.get("compare"), stages["main_ocr"]["blocks"]
        ),
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
        "cmp": _compare_ctx(stages.get("compare"), dataset, page),
        "main_label": main_label,
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
        "dataset_options": data.dataset_names(),
        **_selector_ctx(routes.slugs(route_obj)),
    }
    return TemplateResponse(request, "viewer/page.html", ctx)


async def page_img(
    request: HttpRequest, dataset: str, page: str
) -> FileResponse:
    """The canonical black-redacted render of a page."""
    path = data.page_png_path(dataset, page)
    return FileResponse(open(path, "rb"), content_type="image/png")


async def crop_img(
    request: HttpRequest, dataset: str, page: str, bbox: str
) -> FileResponse:
    """The cached LightOn crop image for an exact bbox key — what the
    tiebreaker actually read (the dispute cards)."""
    path = data.lighton_crop_png(dataset, page, bbox)
    if path is None:
        raise Http404(f"no cached crop {page}_{bbox}")
    return FileResponse(open(path, "rb"), content_type="image/png")
