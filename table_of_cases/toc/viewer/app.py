"""Viewer and eval-set builder for the table of cases.

Left: the scanned page. Right: the entries each engine parsed from it, side
by side. With two engines of the same prompt version shown, the reviewer's
choices are saved as the eval set in data/eval/<prompt version>/. The app
never calls a model, never changes model outputs, and loads nothing from
the network.

    uv run uvicorn toc.viewer.app:app --port 8225
"""

import json
import os
import re
import threading
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from toc.config import (
    CACHE_DIR,
    EVAL_DIR,
    FIRST_LIST_PAGE,
    OUTPUTS_DIR,
    SOURCES,
    page_file,
)
from toc.evalset import EvalConflict, EvalPage, build_record

UI_DIR = Path(__file__).resolve().parent / "ui"
RENDER_DPI = 170
ENGINE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PAGE_FILE = re.compile(r"^page_(\d{4})\.json$")
EVAL_VERSION = re.compile(r"^v\d+$")

_pdfs = {name: pymupdf.open(path) for name, path in SOURCES.items() if path.is_file()}
if not _pdfs:
    expected = ", ".join(str(path) for path in SOURCES.values())
    raise SystemExit(f"No scan found. Expected at least one of: {expected}")
_pdf_lock = threading.Lock()
PAGE_COUNT = len(next(iter(_pdfs.values())))

app = FastAPI(title="Table of Cases viewer")
app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
templates = Jinja2Templates(directory=UI_DIR / "templates")


def engine_dirs() -> list[Path]:
    if not OUTPUTS_DIR.is_dir():
        return []
    return sorted(
        d for d in OUTPUTS_DIR.iterdir() if d.is_dir() and ENGINE_NAME.match(d.name)
    )


def engine_pages(engine_dir: Path) -> list[int]:
    matches = (PAGE_FILE.match(f.name) for f in engine_dir.iterdir())
    return sorted(int(match.group(1)) for match in matches if match)


def check_page(page: int) -> None:
    if not 1 <= page <= PAGE_COUNT:
        raise HTTPException(404, f"Page {page} is outside 1–{PAGE_COUNT}.")


def load_engine_page(engine_dir: Path, page: int) -> dict | None:
    """Return one engine's output for a page, or None if it has none."""
    path = page_file(engine_dir, page)
    if not path.is_file():
        return None
    try:
        entries = json.loads(path.read_text())["entries"]
    except (OSError, ValueError, KeyError) as exc:
        return {"entries": [], "error": f"{path.name} could not be read: {exc}"}
    return {"entries": entries, "error": None}


def eval_dir(version: str) -> Path:
    """The eval-set folder for one prompt version (v1, v2, ...)."""
    if not EVAL_VERSION.match(version):
        raise HTTPException(404, f"No eval set named {version!r}.")
    return EVAL_DIR / version


def read_eval(version: str, page: int) -> dict | None:
    path = page_file(eval_dir(version), page)
    return json.loads(path.read_text()) if path.is_file() else None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"page_count": PAGE_COUNT, "first_list_page": FIRST_LIST_PAGE},
    )


@app.get("/api/meta")
def meta():
    return {
        "page_count": PAGE_COUNT,
        "first_list_page": FIRST_LIST_PAGE,
        "sources": list(_pdfs),
        "engines": [{"name": d.name, "pages": engine_pages(d)} for d in engine_dirs()],
    }


@app.get("/api/page/{page}")
def page_outputs(page: int):
    check_page(page)
    outputs = {}
    for engine_dir in engine_dirs():
        loaded = load_engine_page(engine_dir, page)
        if loaded is not None:
            outputs[engine_dir.name] = loaded
    return {"page": page, "outputs": outputs}


@app.get("/page/{source}/{page}")
def page_image(source: str, page: int):
    if source not in _pdfs:
        raise HTTPException(404, f"No scan named {source!r}.")
    check_page(page)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # The bitonal scan stays lossless; the photographic scan is sent as JPEG.
    suffix, media_type = (
        ("png", "image/png") if source == "bitonal" else ("jpg", "image/jpeg")
    )
    path = CACHE_DIR / f"{source}_{page:04d}_{RENDER_DPI}.{suffix}"
    if not path.is_file():
        with _pdf_lock:
            pixmap = _pdfs[source][page - 1].get_pixmap(dpi=RENDER_DPI)
            if suffix == "png":
                pixmap.save(path)
            else:
                pixmap.save(path, jpg_quality=88)
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "max-age=86400"}
    )


@app.get("/api/eval/{version}")
def eval_summary(version: str):
    """Review progress per page for one prompt version, for the page list."""
    pages = {}
    for path in sorted(eval_dir(version).glob("page_*.json")):
        data = json.loads(path.read_text())
        pages[data["page"]] = {
            "complete": data["complete"],
            "open": data["open"],
            "total": data["total"],
        }
    return {"pages": pages}


@app.get("/api/eval/{version}/{page}")
def eval_get(version: str, page: int):
    check_page(page)
    return {"page": page, "saved": read_eval(version, page)}


@app.post("/api/eval/{version}/{page}")
def eval_save(version: str, page: int, body: EvalPage):
    """Save the reviewer's choices for one page as that page's eval set."""
    check_page(page)
    try:
        record = build_record(page, body, read_eval(version, page))
    except EvalConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    path = page_file(eval_dir(version), page)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
    return {key: record[key] for key in ("complete", "open", "total")}
