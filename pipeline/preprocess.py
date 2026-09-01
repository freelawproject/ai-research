"""Stage 1 — OCR pipeline pages → the tagger's canonical input doc.

Converts assembled per-page artifacts from the FLP extraction pipeline
(`<range>__page_NNN.json`, each holding ``stages.assemble.html``) into
the single canonical markup document the block tagger consumes:

- block roles content/heading/caption → ``<p>``, blockquote →
  ``<blockquote>``, consecutive footnote blocks → one ``<footnotes>``
  band, page_number dropped;
- inline ``<em>``/``<sup>`` kept (adjacent word-level ``<em>`` runs
  merged), every other inline tag unwrapped;
- whitespace collapsed; the assemble stage's ``∅`` low-confidence
  placeholder stripped; text-empty blocks dropped.

Output: ``<out>/<range>.json`` =
``{range, text, pages: [{stem, start, end}], layout_heading_spans}``.
``layout_heading_spans`` records blocks the layout detector classed as
visual headings — an optional extra label source for postprocess.

    uv run python preprocess.py --artifacts DIR --ranges R [R ...]

Stdlib only — no ML dependencies.
"""

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = re.compile(r"\s+")

BLOCK_ROLE = {  # pipeline data-role -> canonical block tag (None = drop)
    "content": "p",
    "heading": "p",
    "caption": "p",
    "blockquote": "blockquote",
    "footnote": "footnote",  # grouped into a band below
    "page_number": None,
}
KEEP_INLINE = {"em", "sup"}


class Writer:
    """Accumulates the canonical string; manages whitespace."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pos = 0
        self.pending_space = False
        self.at_edge = True  # just after a block open or document start

    def _emit(self, s: str) -> None:
        self.parts.append(s)
        self.pos += len(s)

    def text(self, raw: str) -> None:
        if not raw:
            return
        collapsed = WS.sub(" ", raw)
        core = collapsed.strip()
        if not core:
            if not self.at_edge:
                self.pending_space = True
            return
        if self.pending_space or (collapsed[0] == " " and not self.at_edge):
            self._emit(" ")
        self.pending_space = collapsed[-1] == " "
        self.at_edge = False
        self._emit(core)

    def flush_space(self) -> None:
        if self.pending_space and not self.at_edge:
            self._emit(" ")
        self.pending_space = False

    def markup_inline(self, s: str) -> None:
        """Inline tag: any pending space lands before it."""
        self.flush_space()
        self.at_edge = False
        self._emit(s)

    def block_open(self, s: str) -> None:
        self.pending_space = False
        self._emit(s)
        self.at_edge = True

    def block_close(self, s: str) -> None:
        self.pending_space = False
        self._emit(s + "\n")
        self.at_edge = True

    def result(self) -> str:
        return "".join(self.parts)


class _BlockParser(HTMLParser):
    """Assembled-page HTML -> [(role, [events])] where events are
    ("text", s) | ("open"/"close", tag) for kept inline tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, list]] = []
        self._depth = 0
        self._events: list | None = None

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        if self._depth == 1:
            a = dict(attrs)
            role = a.get("data-role", "content")
            self._events = []
            self.blocks.append((role, self._events))
        elif self._events is not None and tag in KEEP_INLINE:
            self._events.append(("open", tag))

    def handle_endtag(self, tag):
        if self._depth == 1:
            self._events = None
        elif self._events is not None and tag in KEEP_INLINE:
            self._events.append(("close", tag))
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        # ∅ is the assemble stage's zero-width low-confidence marker (a
        # dispute whose main side is empty) — a placeholder, not page
        # text; a block that was only a marker drops as text-empty.
        data = data.replace("∅", "")
        if self._events is not None and data:
            self._events.append(("text", data))


def _merge_em_runs(events: list) -> list:
    """Some OCR engines emit word-level emphasis
    (<em>770</em> <em>F</em><em>.</em>…). Merge adjacent <em> spans —
    whitespace-only text between two spans moves inside the merged
    span."""
    out: list = []
    i = 0
    while i < len(events):
        if events[i] == ("close", "em"):
            j = i + 1
            ws = []
            while (
                j < len(events)
                and events[j][0] == "text"
                and not events[j][1].strip()
            ):
                ws.append(events[j])
                j += 1
            if j < len(events) and events[j] == ("open", "em"):
                out.extend(ws)
                i = j + 1
                continue
        out.append(events[i])
        i += 1
    return out


def convert_page(html: str, w: Writer) -> list[tuple]:
    """One assembled page -> canonical blocks appended to the Writer.
    Returns [(role, start, end)] per kept block — char extents in the
    canonical string (markup included)."""
    blocks_out: list[tuple] = []
    parser = _BlockParser()
    parser.feed(html)
    fn_run = False
    for role, raw_events in parser.blocks:
        events = _merge_em_runs(raw_events)
        tag = BLOCK_ROLE.get(role, "p")
        if tag is None:
            continue
        if not "".join(v for k, v in events if k == "text").strip():
            continue  # text-empty block: pure markup noise
        if tag == "footnote":
            if not fn_run:
                w.block_open("<footnotes>\n")
                fn_run = True
            wrap = "p"
        else:
            if fn_run:
                w.block_close("</footnotes>")
                fn_run = False
            wrap = tag
        blk_start = w.pos
        w.block_open(f"<{wrap}>")
        for kind, val in events:
            if kind == "text":
                w.text(val)
            elif kind == "open":
                w.markup_inline(f"<{val}>")
            else:
                w.markup_inline(f"</{val}>")
        w.block_close(f"</{wrap}>")
        blocks_out.append((role, blk_start, w.pos))
    if fn_run:
        w.block_close("</footnotes>")
    return blocks_out


def convert_range(artifacts: Path, rng: str) -> dict | None:
    """All of a range's assembled pages -> one canonical doc + page
    map. Pages are discovered by filename (`<range>__page_NNN.json`)
    and processed in page order."""
    files = sorted(artifacts.glob(f"{rng}__page_*.json"))
    if not files:
        return None
    w = Writer()
    pages = []
    layout_headings = []
    for f in files:
        art = json.loads(f.read_text())
        start = w.pos
        blocks = convert_page(art["stages"]["assemble"]["html"], w)
        layout_headings += [
            {"start": a, "end": b}
            for role, a, b in blocks if role == "heading"
        ]
        stem = f.stem.split("__", 1)[1]
        pages.append({"stem": stem, "start": start, "end": w.pos})
    return {
        "range": rng,
        "text": w.result(),
        "pages": pages,
        "layout_heading_spans": layout_headings,
    }


def discover_ranges(artifacts: Path) -> list[str]:
    return sorted({f.stem.split("__", 1)[0]
                   for f in artifacts.glob("*__page_*.json")})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--artifacts", type=Path, required=True,
                    help="directory of <range>__page_NNN.json artifacts")
    ap.add_argument("--ranges", nargs="*", default=[],
                    help="ranges to convert (default: every range found)")
    ap.add_argument("--out", type=Path, default=HERE / "work" / "docs")
    args = ap.parse_args()

    ranges = args.ranges or discover_ranges(args.artifacts)
    if not ranges:
        print(f"no artifacts under {args.artifacts}")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for rng in ranges:
        doc = convert_range(args.artifacts, rng)
        if doc is None:
            print(f"!! {rng}: no artifacts")
            return 1
        (args.out / f"{rng}.json").write_text(
            json.dumps(doc, ensure_ascii=False)
        )
        print(f"{rng}: {len(doc['pages'])} pages, "
              f"{len(doc['text'])} chars")
    print(f"→ {args.out} (next: infer.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
