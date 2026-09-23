"""Viewer: eyecite vs GPT vs Opus vs encoder against gold, on the 49 triage dev
opinions. One text, five tagging layers; one row per mention locus with each
system's verdict; the pooled table of model_comparison_dev.md computed live.

    cd experiments_09022026/triage/dev_compare_viewer
    uv run uvicorn app:app --port 8240          # http://127.0.0.1:8240/

Reads (nothing is written):
    ../data/annotator/                          gold + eyecite seed (annotator root)
    ../data/citation_seed/dev_ids.txt            the 49 dev opinions
    ../data/citation_seed/dev_gpt_v8g_high/      GPT-5.6 v8g high edits
    ../data/citation_seed/dev_opus_v5/           Opus 5 v5 edits
    ../../experiments_09092026/data/output/pred_gold_dev_r4_rule.json   encoder
Env: DEVCMP_PRED (encoder predictions), DEVCMP_IDS (id list),
     DEVCMP_EXTRA=sonnet,kimi,gpt_v5 (add the other measured rows).
"""

from __future__ import annotations

import os
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import compare as C

HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="dev citation system comparison")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "ui", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "ui", "templates"))

IDS = C.dev_ids()
PAYLOADS: dict[str, dict] = {}
print(f"comparing {len(IDS)} dev opinions across {len(C.SYSTEMS)} systems …", file=sys.stderr)
_t0 = time.time()
# one pass at boot: the index needs the pooled totals anyway, and PAYLOADS then
# serves every opinion page without recomputing.
ROWS, TABLE, TOTALS = C.summarize(IDS, cache=PAYLOADS)
print(f"ready in {time.time() - _t0:.1f}s — {TOTALS['n_gold']:,} gold mentions, "
      f"{TOTALS['n_diff']:,} loci where at least one system differs", file=sys.stderr)

SORTS = {"diff": lambda r: -r["n_diff"], "gold": lambda r: -r["n_gold"],
         "cid": lambda r: r["cid"], "case": lambda r: r["meta"]["case_name"].lower()}


def ordered(sort):
    return sorted(ROWS, key=SORTS.get(sort, SORTS["diff"]))


SYS_META = [{"key": k, "label": C.SYSTEMS[k][0], "short": C.SYSTEMS[k][1], "css": C.SYSTEMS[k][2]}
            for k in C.SYSTEMS]


@app.get("/", response_class=HTMLResponse)
def index(request: Request, sort: str = "diff"):
    rows = ordered(sort)
    return templates.TemplateResponse(request, "index.html", {
        "rows": rows, "table": TABLE, "totals": TOTALS, "systems": SYS_META, "sort": sort})


@app.get("/opinion/{cid}", response_class=HTMLResponse)
def opinion(request: Request, cid: str, sort: str = "diff"):
    if cid not in PAYLOADS:
        raise HTTPException(404, f"{cid} is not one of the {len(IDS)} dev opinions")
    order = [r["cid"] for r in ordered(sort)]
    pos = order.index(cid)
    d = PAYLOADS[cid]
    row = next(r for r in ROWS if r["cid"] == cid)
    return templates.TemplateResponse(request, "opinion.html", {
        "cid": cid, "meta": d["meta"], "row": row, "sort": sort,
        "systems": [s for s in SYS_META if s["key"] in d["order"]],
        "counts": d["counts"], "n_gold": d["n_gold"], "errors": d["errors"],
        "pos": pos + 1, "n": len(order),
        "prev": order[pos - 1] if pos > 0 else None,
        "next": order[pos + 1] if pos < len(order) - 1 else None})


@app.get("/api/opinion/{cid}")
def api_opinion(cid: str):
    if cid not in PAYLOADS:
        raise HTTPException(404, f"{cid} is not one of the {len(IDS)} dev opinions")
    return JSONResponse(PAYLOADS[cid])


@app.get("/api/summary")
def api_summary():
    return JSONResponse({"totals": TOTALS, "pooled": TABLE, "rows": ROWS})
