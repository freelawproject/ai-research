"""Tiny local web viewer for page images and YOLO predictions.

Run on the machine that has the data:

    python -m ocr_tools.viewer [--host 127.0.0.1] [--port 8000]

The sidebar filters by dataset split and by YOLO model (any model with
``*.yolo.<name>.json`` prediction files in the pages directory). When a model
is selected, only pages with predictions for it are listed, and the bounding
boxes are drawn on the images on the fly -- nothing is written to disk. The
grid loads more pages lazily as you scroll.
"""

import argparse
import io
import json
import re
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image

from ocr_tools.utils import DATA_DIR, load_data
from ocr_tools.yolo import draw_layout

PAGES_DIR = DATA_DIR / "pages"
SPLITS = ("train", "val", "test")
MODEL_NAME_RE = re.compile(r"^[\w.-]+$")
PAGE_SIZE = 30


@lru_cache(maxsize=1)
def stems_by_split() -> dict[str, list[str]]:
    data = load_data()
    by_split: dict[str, list[str]] = {split: [] for split in SPLITS}
    for split, path in zip(data["split"], data["path"]):
        by_split.setdefault(split, []).append(path.stem)
    by_split[""] = [
        stem for stems in list(by_split.values()) for stem in stems
    ]
    return by_split


@lru_cache(maxsize=1)
def known_stems() -> frozenset[str]:
    return frozenset(stems_by_split()[""])


def discover_models() -> list[str]:
    pattern = re.compile(r"\.yolo\.(.+)\.json$")
    return sorted(
        {
            match.group(1)
            for path in PAGES_DIR.glob("*.yolo.*.json")
            if (match := pattern.search(path.name))
        }
    )


def list_items(split: str, model: str) -> list[str]:
    stems = stems_by_split().get(split, [])
    if model:
        stems = [
            stem
            for stem in stems
            if (PAGES_DIR / f"{stem}.yolo.{model}.json").exists()
        ]
    return [stem for stem in stems if (PAGES_DIR / f"{stem}.jpg").exists()]


def render_page(stem: str, model: str) -> bytes:
    image = Image.open(PAGES_DIR / f"{stem}.jpg")
    if model:
        label_path = PAGES_DIR / f"{stem}.yolo.{model}.json"
        if label_path.exists():
            image = draw_layout(image, json.loads(label_path.read_text()))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>scanning viewer</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="m-0 flex min-h-screen bg-slate-100 text-slate-800 antialiased">
<aside class="sticky top-0 h-screen w-56 flex-none border-r border-slate-200
              bg-white px-5 py-6 shadow-sm">
  <h1 class="mb-6 flex items-center gap-2 text-sm font-semibold
             tracking-wide text-slate-900">
    <span class="inline-block h-2.5 w-2.5 rounded-full bg-indigo-500"></span>
    scanning viewer
  </h1>
  <fieldset class="mb-6">
    <legend class="mb-2 text-xs font-semibold uppercase tracking-wider
                   text-slate-400">Split</legend>
    <select id="split"
            class="w-full rounded-lg border border-slate-300 bg-white px-2.5
                   py-1.5 text-sm shadow-sm focus:border-indigo-400
                   focus:outline-none focus:ring-2 focus:ring-indigo-200">
      <option value="">all</option>
      <option value="train">train</option>
      <option value="val">val</option>
      <option value="test">test</option>
    </select>
  </fieldset>
  <fieldset class="mb-6">
    <legend class="mb-2 text-xs font-semibold uppercase tracking-wider
                   text-slate-400">Model</legend>
    <select id="model"
            class="w-full rounded-lg border border-slate-300 bg-white px-2.5
                   py-1.5 text-sm shadow-sm focus:border-indigo-400
                   focus:outline-none focus:ring-2 focus:ring-indigo-200">
      <option value="">(raw images)</option>
    </select>
  </fieldset>
  <div id="count"
       class="inline-block rounded-full bg-slate-100 px-3 py-1 text-xs
              font-medium text-slate-500"></div>
</aside>
<main class="flex-1">
  <div id="grid"
       class="mx-auto grid max-w-4xl grid-cols-1 gap-6 p-6"></div>
  <div id="sentinel" class="h-px"></div>
</main>
<script>
const PAGE = 30;
const state = {split:"", model:"", offset:0, loading:false, done:false};
const grid = document.getElementById("grid");
const sentinel = document.getElementById("sentinel");

function reset() {
  state.split = document.getElementById("split").value;
  state.model = document.getElementById("model").value;
  state.offset = 0;
  state.done = false;
  grid.innerHTML = "";
  loadMore();
}

async function loadMore() {
  if (state.loading || state.done) return;
  state.loading = true;
  const q = new URLSearchParams({split: state.split, model: state.model,
                                 offset: state.offset, limit: PAGE});
  const data = await (await fetch("/api/items?" + q)).json();
  for (const stem of data.items) {
    const url = "/image/" + encodeURIComponent(stem) + ".jpg" +
                (state.model ? "?model=" + encodeURIComponent(state.model) : "");
    const card = document.createElement("div");
    card.className = "group";
    card.innerHTML =
      `<a href="${url}" target="_blank" class="block overflow-hidden ` +
      `rounded-xl border border-slate-200 bg-white shadow-sm transition ` +
      `group-hover:-translate-y-0.5 group-hover:shadow-md">` +
      `<img loading="lazy" src="${url}" class="block w-full"></a>` +
      `<div class="break-all px-1 pt-1.5 font-mono text-[11px] ` +
      `text-slate-500">${stem}</div>`;
    grid.appendChild(card);
  }
  state.offset += data.items.length;
  state.done = state.offset >= data.total || data.items.length === 0;
  document.getElementById("count").textContent =
      state.offset + " / " + data.total;
  state.loading = false;
  // Re-arm the observer in case the sentinel is still in view.
  observer.unobserve(sentinel);
  observer.observe(sentinel);
}

const observer = new IntersectionObserver(
  entries => { if (entries.some(e => e.isIntersecting)) loadMore(); },
  {rootMargin: "600px"},
);

async function init() {
  const models = await (await fetch("/api/models")).json();
  const select = document.getElementById("model");
  for (const name of models) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.onchange = reset;
  document.getElementById("split").onchange = reset;
  observer.observe(sentinel);
  reset();
}
init();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        model = query.get("model", [""])[0]
        if model and not MODEL_NAME_RE.match(model):
            self._send(400, b"bad model name", "text/plain")
            return

        if parsed.path == "/":
            self._send(200, INDEX_HTML.encode(), "text/html")
        elif parsed.path == "/api/models":
            self._json(discover_models())
        elif parsed.path == "/api/items":
            split = query.get("split", [""])[0]
            offset = int(query.get("offset", ["0"])[0])
            limit = min(int(query.get("limit", [str(PAGE_SIZE)])[0]), 200)
            stems = list_items(split, model)
            self._json(
                {
                    "items": stems[offset : offset + limit],
                    "total": len(stems),
                }
            )
        elif parsed.path.startswith("/image/") and parsed.path.endswith(
            ".jpg"
        ):
            stem = unquote(parsed.path[len("/image/") : -len(".jpg")])
            if stem not in known_stems():
                self._send(404, b"unknown page", "text/plain")
                return
            self._send(200, render_page(stem, model), "image/jpeg")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"viewer running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
