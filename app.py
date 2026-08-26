"""Block-tagger delivery viewer — gold vs model predictions.

Read-only visualization of the case-law block tagger's output on the
big-set VAL and TEST windows, next to the redacted page scans. The
data directory ships separately (see README.md); this repo holds only
the viewer.

    uv run uvicorn app:app --port 8180
"""

import bisect
import html
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import spannorm

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATASETS = ("bigset_val", "bigset_test")

app = FastAPI(title="block tagger — gold vs predictions")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

_CACHE: dict = {}


def _load(ds: str):
    if ds not in DATASETS:
        raise HTTPException(400, f"unknown dataset {ds!r}")
    if ds in _CACHE:
        return _CACHE[ds]
    wpath = DATA / f"{ds}.jsonl"
    ppath = DATA / "preds" / ds / "pred.jsonl"
    if not wpath.exists() or not ppath.exists():
        raise HTTPException(
            404, f"data for {ds} not found — unzip the data bundle "
                 "into blocktagger_delivery/data/ (see README)")
    wins = [json.loads(line) for line in wpath.open()]
    preds = {}
    for line in ppath.open():
        p = json.loads(line)
        preds[(p["doc"], p["case"], p["window"])] = p["block_spans_pred"]
    _CACHE[ds] = (wins, preds)
    return _CACHE[ds]


def _pages():
    if "pages" not in _CACHE:
        path = DATA / "pages.json"
        _CACHE["pages"] = (json.loads(path.read_text())
                           if path.exists() else {})
    return _CACHE["pages"]


def _render(text: str, spans: list, xcls: list) -> str:
    blk: list = [None] * len(text)
    for s in spans:
        for c in range(s["start"], min(s["end"], len(text))):
            blk[c] = s["label"]
    parts = []
    i = 0
    while i < len(text):
        j = i
        while j < len(text) and blk[j] == blk[i] and xcls[j] == xcls[i]:
            j += 1
        seg = html.escape(text[i:j])
        cls = []
        if blk[i]:
            cls.append(f"mvb-{blk[i]}")
        if xcls[i] is not None:
            cls.append(f"pf-{xcls[i]}" if xcls[i] else "pf")
        if cls:
            parts.append(f'<span class="{" ".join(cls)}" '
                         f'title="{blk[i] or ""}">{seg}</span>')
        else:
            parts.append(seg)
        i = j
    return "".join(parts)


def _diff(text, gt, pr):
    """(gold flag array, pred flag array, stats) — strict span-set
    comparison, both sides normalized uniformly by spannorm."""
    m_strict = spannorm.edge_mask(text, punct=False)
    m_norm = spannorm.edge_mask(text, punct=True)
    pr = spannorm.normalize(text, pr, False, m_strict)

    def keyset(spans, mask):
        return {(s["start"], s["end"], s["label"]) for s in
                spannorm.normalize(text, spans, False, mask)}

    def norm_key(s, mask):
        a, b = spannorm.trim_span(mask, s["start"], s["end"])
        return (a, b, s["label"])

    def flags(spans, other_s, other_n, pre):
        arr: list = [None] * len(text)
        for s in spans:
            if norm_key(s, m_strict) in other_s:
                continue
            f = f"{pre}-n" if norm_key(s, m_norm) in other_n else pre
            for c in range(s["start"], s["end"]):
                arr[c] = f
        return arr

    gt_x = flags(gt, keyset(pr, m_strict), keyset(pr, m_norm), "miss")
    pr_x = flags(pr, keyset(gt, m_strict), keyset(gt, m_norm), "fp")
    gs, ps = keyset(gt, m_strict), keyset(pr, m_strict)
    gn, pn = keyset(gt, m_norm), keyset(pr, m_norm)
    stats = (f"{len(gs & ps)}/{len(gs)} gold spans matched strict → "
             f"{len(gn & pn)}/{len(gn)} normalized · "
             f"{len(ps - gs)} pred-only strict")
    return gt_x, pr, pr_x, stats, (len(gs - ps), len(ps - gs))


@app.get("/")
def index_page(request: Request):
    return templates.TemplateResponse(request, "viewer.html", {})


@app.get("/api/index")
def api_index(ds: str = "bigset_val"):
    wins, preds = _load(ds)
    rows = []
    for i, w in enumerate(wins):
        pr = preds.get((w["doc"], w["case"], w["window"]), [])
        _, _, _, _, (n_miss, n_fp) = _diff(w["text"], w["block_spans"],
                                           pr)
        rows.append({"i": i, "doc": w["doc"], "case": w["case"],
                     "window": w["window"], "volume": w["volume"],
                     "kind": w.get("kind", "case"),
                     "n_spans": len(w["block_spans"]),
                     "n_miss": n_miss, "n_fp": n_fp})
    return JSONResponse({"ds": ds, "datasets": list(DATASETS),
                         "windows": rows})


@app.get("/api/win/{i}")
def api_win(i: int, ds: str = "bigset_val"):
    wins, preds = _load(ds)
    if not (0 <= i < len(wins)):
        raise HTTPException(404, f"no window {i}")
    w = wins[i]
    pr = preds.get((w["doc"], w["case"], w["window"]))
    if pr is None:
        raise HTTPException(404, "window has no prediction")
    text = w["text"]
    gt = w["block_spans"]
    gt_x, pr2, pr_x, stats, _ = _diff(text, gt, pr)
    # pages this window covers (for the redacted-scan pane)
    stems = []
    pg = _pages().get(w["doc"], [])
    if "g0" in w:
        for p in pg:
            if p["start"] < w["g1"] and p["end"] > w["g0"]:
                stems.append(p["stem"])
    return JSONResponse({
        "i": i, "n": len(wins), "doc": w["doc"], "case": w["case"],
        "window": w["window"], "volume": w["volume"], "stats": stats,
        "stems": stems,
        "gold_html": _render(text, gt, gt_x),
        "pred_html": _render(text, pr2, pr_x),
    })


@app.get("/api/img/{rng}/{stem}.png")
def api_img(rng: str, stem: str):
    path = DATA / "redacted" / f"{rng}__{stem}.png"
    if not path.exists():
        raise HTTPException(404, f"no redacted render for {rng}__{stem}")
    return Response(path.read_bytes(), media_type="image/png")
