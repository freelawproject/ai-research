"""Results viewer: the word-level block tagger's spans over the eval sets.

    uv run uvicorn app:app --port 8180

Reads the portal set from ``data/portal_eval.jsonl`` and the previous
model's output from ``before/portal_eval/pred.jsonl`` (both in the
eval-data bundle), and this model's output from ``out/portal_eval/``
(written by ``score.py``). ``TAGGER_DATA`` / ``TAGGER_BEFORE`` /
``TAGGER_OUT`` point elsewhere.

One page, ``/``: the paragraphs flagged in scanning issue #458, each
with the raw output quoted there and, side by side, the previous
model's spans (as deployed: per-token decode) and this model's spans
over the same paragraph. ``marks=plain`` (default) draws each model's
own tags only, with a mark under the spans one side has and the other
does not;
``marks=gold`` judges each tag against the gold (match / other edges /
other label / no gold / missed), with the same normalization the scores
use (``edges=strict|normalized``). Read-only.
"""

from __future__ import annotations

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tagger.spannorm import normalize  # noqa: E402
from tagger.wordlevel import merge_adjacent  # noqa: E402

DATA = Path(os.environ.get("TAGGER_DATA", HERE / "data"))
OUT = Path(os.environ.get("TAGGER_OUT", HERE / "out"))
BEFORE = Path(os.environ.get("TAGGER_BEFORE", HERE / "before"))
SIDES = {"before": (BEFORE, "previous model"), "after": (OUT, "this model")}
ISSUE = json.load((HERE / "issue458.json").open())
BLOCK_RE = re.compile(r"<p>.*?</p>|<blockquote>.*?</blockquote>", re.S)

app = FastAPI(title="block tagger results")
app.mount("/static", StaticFiles(directory=HERE / "ui" / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "ui" / "templates")


def wkey(r: dict) -> str:
    return f"{r['doc']}|c{r.get('case', 0)}|w{r['window']}"


@lru_cache(maxsize=None)
def gold_rows(dataset: str) -> dict[str, dict]:
    p = DATA / f"{dataset}.jsonl"
    if not p.exists():
        raise HTTPException(404, f"{p.name} not found in {DATA}")
    out = {}
    for r in map(json.loads, p.open()):
        out.setdefault(wkey(r), r)
    return out


@lru_cache(maxsize=None)
def pred_rows(side: str, dataset: str) -> dict[str, list[dict]]:
    p = SIDES[side][0] / dataset / "pred.jsonl"
    if not p.exists():
        raise HTTPException(404, f"no predictions at {p}; " + ("unpack the eval-data bundle" if side == "before" else "run score.py"))
    out = {}
    for r in map(json.loads, p.open()):
        out.setdefault(wkey(r), r["block_spans_pred"])
    return out


def norm(text: str, spans: list[dict], edges: str) -> list[dict]:
    return sorted(({"label": s["label"], "start": s["start"], "end": s["end"]}
                   for s in normalize(text, spans, punct=edges == "normalized")),
                  key=lambda s: (s["start"], s["end"]))


def edge_delta(text: str, p: dict, g: dict) -> str:
    bits = []
    if p["start"] > g["start"]:
        bits.append(f"drops {text[g['start']:p['start']]!r} at the start")
    elif p["start"] < g["start"]:
        bits.append(f"adds {text[p['start']:g['start']]!r} at the start")
    if p["end"] < g["end"]:
        bits.append(f"drops {text[p['end']:g['end']]!r} at the end")
    elif p["end"] > g["end"]:
        bits.append(f"adds {text[g['end']:p['end']]!r} at the end")
    return "; ".join(b if len(b) < 60 else b[:57] + "…" for b in bits)


def status_of(text: str, pred: list[dict], gold: list[dict]) -> list[dict]:
    """Each predicted span with how it relates to the gold: match, piece
    (same label, other edges), wronglabel, extra."""
    exact = {(g["label"], g["start"], g["end"]) for g in gold}
    out = []
    for p in pred:
        if (p["label"], p["start"], p["end"]) in exact:
            st, note = "match", ""
        else:
            over = [g for g in gold if g["start"] < p["end"] and g["end"] > p["start"]]
            same = [g for g in over if g["label"] == p["label"]]
            if same:
                st = "piece"
                note = edge_delta(text, p, same[0]) if len(same) == 1 else f"covers {len(same)} gold spans"
            elif over:
                st, note = "wronglabel", "gold: " + ", ".join(sorted({g["label"] for g in over}))
            else:
                st, note = "extra", "no gold span here"
        out.append({**p, "status": st, "note": note})
    return out


def window_view(dataset: str, key: str, edges: str) -> dict:
    g = gold_rows(dataset).get(key)
    if g is None:
        raise HTTPException(404, "unknown window")
    t = g["text"]
    gold = norm(t, merge_adjacent(t, g["block_spans"]), edges)
    out = {"key": key, "doc": g["doc"], "case": g.get("case", 0), "window": g["window"], "text": t,
           "gold": gold, "runs": {}}
    for side, (_, name) in SIDES.items():
        pred = norm(t, pred_rows(side, dataset).get(key, []), edges)
        exact = {(p["label"], p["start"], p["end"]) for p in pred}
        out["runs"][side] = {"run": name, "spans": status_of(t, pred, gold),
                             "missed": [s for s in gold if (s["label"], s["start"], s["end"]) not in exact]}
    return out


# ── Issue #458: the flagged paragraphs ───────────────────────────────
def _locate(flag: dict) -> tuple[str, int, int] | None:
    for key, g in gold_rows("portal_eval").items():
        if g["doc"] != flag["doc"]:
            continue
        i = g["text"].find(flag["anchor"])
        if i < 0:
            continue
        for m in BLOCK_RE.finditer(g["text"]):
            if m.start() <= i < m.end():
                return key, m.start(), m.end()
    return None


def _slice(spans: list[dict], a: int, b: int) -> list[dict]:
    return [{**s, "start": max(s["start"], a) - a, "end": min(s["end"], b) - a}
            for s in spans if s["start"] < b and s["end"] > a]


@app.get("/", response_class=HTMLResponse)
def issue_page(request: Request, edges: str = "strict", marks: str = "plain"):
    if edges not in ("strict", "normalized") or marks not in ("plain", "gold"):
        raise HTTPException(400, "edges must be strict|normalized, marks plain|gold")
    if not (DATA / "portal_eval.jsonl").exists() or not (BEFORE / "portal_eval" / "pred.jsonl").exists():
        raise HTTPException(404, f"needs {DATA}/portal_eval.jsonl and {BEFORE}/portal_eval/pred.jsonl (the eval-data bundle)")
    if not (OUT / "portal_eval" / "pred.jsonl").exists():
        raise HTTPException(404, f"needs {OUT}/portal_eval/pred.jsonl (run score.py)")
    return templates.TemplateResponse(request, "issue.html", {"edges": edges, "marks": marks, "issue": ISSUE})


@app.get("/api/issue")
def api_issue(edges: str = "strict"):
    out = []
    for flag in ISSUE["flags"]:
        loc = _locate(flag)
        if loc is None:
            out.append({**flag, "missing": True})
            continue
        key, a, b = loc
        v = window_view("portal_eval", key, edges)
        cls = flag["cls"]
        want = (lambda s: True) if cls == "all" else (lambda s, c=cls: s["label"] == c)
        item = {**flag, "key": key, "window": v["window"], "text": v["text"][a:b],
                "gold": _slice(v["gold"], a, b), "runs": {}}
        for side, r in v["runs"].items():
            spans, miss = _slice(r["spans"], a, b), _slice(r["missed"], a, b)
            item["runs"][side] = {"run": r["run"], "spans": spans, "missed": miss,
                                  "diffs": sum(1 for s in spans if s["status"] != "match" and want(s))
                                           + sum(1 for s in miss if want(s))}
        # spans only one side has (same label and edges), for the plain view
        sets = {side: {(s["label"], s["start"], s["end"]) for s in item["runs"][side]["spans"]} for side in SIDES}
        for side, other in (("before", "after"), ("after", "before")):
            for s in item["runs"][side]["spans"]:
                s["pair"] = (s["label"], s["start"], s["end"]) not in sets[other]
        item["diff_pair"] = sum(1 for side in SIDES for s in item["runs"][side]["spans"] if s["pair"] and want(s))
        out.append(item)
    return {"flags": out, "types": ISSUE["types"]}
