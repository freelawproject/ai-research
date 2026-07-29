"""Assemble: the final page.

The main engine's reconstruction is the skeleton — its items in
reading order, one HTML block per role. It fills with the MAIN
normalized stream, every compare + resolve verdict applied: a winning
supplemental's tokens substitute the main tokens (an insertion lands at
the dispute's anchor item); a low-confidence dispute renders wrapped in
``<mark class="low-confidence">`` (high-risk adds a class), and one
whose main side is EMPTY renders a zero-width ∅ marker carrying the
other reading in its title.

Styling is a UNION across engines aligned by unit key (display only).
Footnote marks ride their tokens as ``<sup>``; gemini star pages are
re-inserted at their aligned position, never doubled. The presentation
restores the source spacing — units whose provenance shows they were
contiguous glue back together. A table item renders by SPLICING the
resolved tokens into its own sanitized html, keeping rows and cells
intact. An image block is an empty ``<figure data-bbox>`` placeholder;
a consumer that serves page crops passes ``figure_url`` to render them
inline.
"""

from __future__ import annotations

import difflib
import html
import re
from collections.abc import Callable

# Innermost-first wrapping order of the styling tags a token may carry.
_STYLE_ORDER = ("strong", "em", "u", "sub")

# Skeleton element per item role (anything else renders as <p>).
_ROLE_ELEMENT = {"heading": "h2", "blockquote": "blockquote"}

_WS = re.compile(r"\s")


def _no_ws_between(item_html: str, a: int, b: int) -> bool:
    """No whitespace in the item's html between two span edges — tag
    characters (`</em>`, a `<sup>` mark) may sit between contiguous
    text. Without the source html, only exact adjacency counts."""
    if a > b:
        return False
    if b > len(item_html):
        return a == b
    return not _WS.search(item_html[a:b])


def _glued(t: dict, prev: dict | None, html_of: dict[int, str]) -> bool:
    """Whether a unit was CONTIGUOUS with its predecessor in the source
    text. The unit split separates `word,` into two comparison units;
    the presentation re-joins them by provenance: same item, and
    nothing but tag characters between the previous unit's end and this
    unit's start (a wrap-joined predecessor exposes its second
    fragment's end)."""
    if prev is None:
        return False
    span, pspan = t.get("span"), prev.get("span")
    if not span or not pspan:
        return False
    if prev.get("wrap"):
        w_item, _, w_end = prev["wrap"]
        if t.get("item") == w_item and _no_ws_between(
            html_of.get(w_item, ""), w_end, span[0]
        ):
            return True
    return t.get("item") == prev.get("item") and _no_ws_between(
        html_of.get(t.get("item", -1), ""), pspan[1], span[0]
    )


def _final(
    t: dict,
    d: dict | None = None,
    item: int | None = None,
    glue: bool = False,
) -> dict:
    """One final-stream token: the source token's content plus the
    dispute flags it renders under. `item` is always a MAIN item id (a
    substituted supplemental token is re-anchored to the dispute's
    first main block); `glue` means the unit renders attached to its
    predecessor (no space) — the source had no whitespace between
    them."""
    return {
        "display": t["display"],
        "key": t["key"],
        "item": t["item"] if item is None else item,
        "span": list(t.get("span", ())),  # provenance chars (splice)
        "glue": glue,
        "styling": list(t.get("styling", [])),
        "marks": list(t.get("marks", [])),
        "stars": list(t.get("stars", [])),
        "dispute": None if d is None else d["id"],
        "low_confidence": bool(d and d["low_confidence"]),
        "high_risk": bool(d and d["high_risk"]),
        "substituted": bool(d and d["verdict"] != "main"),
    }


def _marker(d: dict, item: int, note: str) -> dict:
    """A zero-width low-confidence marker: the main side of this
    dispute is EMPTY (an engine read extra text here that did not
    win), so there is no main token to flag — the marker keeps the
    dispute visible in the final for review. It carries no content
    (`empty`): the plain text and the unit counts skip it."""
    return {
        "display": "",
        "key": "",
        "item": item,
        "glue": False,
        "styling": [],
        "marks": [],
        "stars": [],
        "dispute": d["id"],
        "low_confidence": True,
        "high_risk": d["high_risk"],
        "substituted": False,
        "empty": True,
        "note": note,
    }


def _html_of(rec: dict | None) -> dict[int, str]:
    return {
        it["id"]: it.get("html", "") for it in (rec or {}).get("items", [])
    }


def resolve_tokens(norm: dict, cmp: dict, recon: dict) -> list[dict]:
    """The final token stream: the main normalized stream with every
    compare + resolve verdict applied. A dispute whose verdict is a
    supplemental label substitutes that supplemental's aligned tokens
    (they bring their own display, styling, and marks); every other
    dispute keeps the main tokens, carrying the dispute's flags. A
    low-confidence dispute whose main side is empty gets a zero-width
    marker so it stays visible. `recon` supplies each stream's item
    html — the source text the glue test reads."""
    main = norm["main"]["tokens"]
    main_html = _html_of(recon["main"])
    recon_supps = recon.get("supplementals", [])
    supp_of = {}
    engine_of = {}
    supp_html: dict[str, dict[int, str]] = {}
    for i, s in enumerate(norm["supplementals"]):
        label = f"supp{i + 1}"
        supp_of[label] = s["tokens"]
        engine_of[label] = s["engine"]
        supp_html[label] = _html_of(
            recon_supps[i] if i < len(recon_supps) else None
        )

    def main_glue(i: int) -> bool:
        return _glued(main[i], main[i - 1] if i > 0 else None, main_html)

    def replaced_span(i1: int, i2: int, anchor: int) -> list[int]:
        """The char range in the ANCHOR item's html that a dispute's
        main side occupied — where a substitution or marker splices
        into a structural (table) item."""
        ours = [
            m for m in main[i1:i2] if m.get("item") == anchor and m.get("span")
        ]
        if ours:
            return [ours[0]["span"][0], ours[-1]["span"][1]]
        for j in range(i1 - 1, -1, -1):  # insertion: after the
            m = main[j]  # previous token of that item
            if m.get("item") == anchor and m.get("span"):
                return [m["span"][1], m["span"][1]]
        return [0, 0]

    final: list[dict] = []
    pos = 0
    for d in sorted(cmp["disputes"], key=lambda d: tuple(d["main_span"])):
        i1, i2 = d["main_span"]
        final += [_final(main[i], glue=main_glue(i)) for i in range(pos, i1)]
        anchor = d["blocks"][0] if d["blocks"] else 0
        if d["verdict"] == "main":
            final += [
                _final(main[i], d, glue=main_glue(i)) for i in range(i1, i2)
            ]
            if i1 == i2 and d["low_confidence"]:
                note = " · ".join(
                    f"{engine_of.get(k, k)} read “{v}”"
                    for k, v in d["reads"].items()
                    if k != "main" and v
                )
                marker = _marker(d, anchor, note)
                marker["replaced_span"] = replaced_span(i1, i2, anchor)
                final.append(marker)
        else:
            supp = supp_of[d["verdict"]]
            html_map = supp_html[d["verdict"]]
            j1, j2 = d["spans"][d["verdict"]]
            rs = replaced_span(i1, i2, anchor)
            for j in range(j1, j2):
                t = _final(
                    supp[j],
                    d,
                    item=anchor,
                    glue=_glued(
                        supp[j],
                        supp[j - 1] if j > 0 else None,
                        html_map,
                    ),
                )
                t["replaced_span"] = rs
                final.append(t)
        pos = i2
    final += [
        _final(main[i], glue=main_glue(i)) for i in range(pos, len(main))
    ]
    return final


def _opcodes(
    a: list[str], b: list[str]
) -> list[tuple[str, int, int, int, int]]:
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return [(tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes()]


def _union_styling(final: list[dict], norm: dict) -> int:
    """Union styling across engines, aligned by unit key: wherever a
    supplemental stream agrees with the final unit, its styling joins
    the unit's. Returns how many units gained styling."""
    keys = [t["key"] for t in final]
    changed: set[int] = set()
    for s in norm["supplementals"]:
        tokens = s["tokens"]
        for tag, i1, i2, j1, j2 in _opcodes(keys, [t["key"] for t in tokens]):
            if tag != "equal":
                continue
            for off in range(i2 - i1):
                extra = tokens[j1 + off].get("styling")
                if not extra:
                    continue
                t = final[i1 + off]
                add = [x for x in extra if x not in t["styling"]]
                if add:
                    t["styling"] = sorted([*t["styling"], *add])
                    changed.add(i1 + off)
    return len(changed)


def _aligned_anchor(
    ops: list[tuple[str, int, int, int, int]], g: int, n_final: int
) -> int:
    """The final-stream token a gemini-stream position anchors to: its
    position-for-position match inside an equal run, otherwise the last
    final token of (or before) the disagreeing region."""
    for tag, i1, i2, j1, j2 in ops:
        if j1 <= g < j2:
            if tag == "equal":
                return i1 + (g - j1)
            return max((i1 if i1 == i2 else i2) - 1, 0)
    return n_final - 1


def _insert_stars(final: list[dict], norm: dict) -> int:
    """Re-insert gemini parallel-reporter star pages at their aligned
    final position. A marker already present (it rode a winning
    substituted token) is never doubled. Returns how many markers were
    inserted by alignment."""
    gem = next(
        (s for s in norm["supplementals"] if s["engine"] == "gemini"), None
    )
    if gem is None or not final:
        return 0
    have = {s for t in final for s in t["stars"]}
    ops = _opcodes(
        [t["key"] for t in final], [t["key"] for t in gem["tokens"]]
    )
    inserted = 0
    for g, tok in enumerate(gem["tokens"]):
        stars = [s for s in tok.get("stars", ()) if s not in have]
        if not stars:
            continue
        t = final[_aligned_anchor(ops, g, len(final))]
        t["stars"] = [*t["stars"], *stars]
        have.update(stars)
        inserted += len(stars)
    return inserted


def final_stream(norm: dict, cmp: dict, recon: dict) -> list[dict]:
    """The complete final token stream — verdicts applied, styling
    unioned, star pages re-inserted. What the final HTML renders."""
    final = resolve_tokens(norm, cmp, recon)
    _union_styling(final, norm)
    _insert_stars(final, norm)
    return final


# ── rendering ────────────────────────────────────────────────────────────


def _word_html(t: dict) -> str:
    if t.get("empty"):  # zero-width low-confidence marker
        return '<span class="lc-marker">∅</span>'
    w = html.escape(t["display"], quote=False)
    for tag in _STYLE_ORDER:
        if tag in t["styling"]:
            w = f"<{tag}>{w}</{tag}>"
    w += "".join(
        f"<sup>{html.escape(m, quote=False)}</sup>" for m in t["marks"]
    )
    w += "".join(
        f' <span class="star-page">★{html.escape(s, quote=False)}</span>'
        for s in t["stars"]
    )
    return w


def _run_key(t: dict) -> tuple[int | None, bool, bool, bool]:
    """Tokens render in uniform runs: a low-confidence dispute's tokens
    share one <mark>; tokens flagged `diff` or `style_diff` (the
    route-compare view) share one highlight span."""
    return (
        t["dispute"] if t["low_confidence"] else None,
        t["high_risk"] if t["low_confidence"] else False,
        bool(t.get("diff")),
        bool(t.get("style_diff")),
    )


def _join(parts: list[tuple[str, bool]]) -> str:
    """Join (segment, glue) pairs: a glued segment attaches to its
    predecessor without a space — the source had none."""
    out: list[str] = []
    for seg, glue in parts:
        if out and not glue:
            out.append(" ")
        out.append(seg)
    return "".join(out)


def _wrap_seg(t: dict, seg: str) -> str:
    """The dispute/diff wrappers of a rendered segment, driven by its
    (run-uniform) first token."""
    if t["low_confidence"]:
        cls = (
            "low-confidence high-risk" if t["high_risk"] else "low-confidence"
        )
        note = t.get("note")
        title = f' title="{html.escape(note, quote=True)}"' if note else ""
        seg = (
            f'<mark class="{cls}" data-dispute="{t["dispute"]}"'
            f"{title}>{seg}</mark>"
        )
    if t.get("style_diff"):
        seg = f'<span class="style-diff">{seg}</span>'
    elif t.get("diff"):
        seg = f'<span class="route-diff">{seg}</span>'
    return seg


def _item_html(tokens: list[dict]) -> str:
    parts: list[tuple[str, bool]] = []
    run: list[dict] = []

    def flush() -> None:
        if not run:
            return
        seg = _join(
            [
                (_word_html(t), k > 0 and bool(t.get("glue")))
                for k, t in enumerate(run)
            ]
        )
        parts.append((_wrap_seg(run[0], seg), bool(run[0].get("glue"))))
        run.clear()

    for t in tokens:
        if run and _run_key(t) != _run_key(run[0]):
            flush()
        run.append(t)
    flush()
    return _join(parts)


_SUP_TAG = re.compile(r"<sup>.*?</sup>")


def _splice_item_html(item_html: str, tokens: list[dict]) -> str:
    """A STRUCTURAL item (a table) renders by splicing the final tokens
    back into the item's own sanitized html at their provenance spans —
    the markup between tokens (rows, cells) is preserved verbatim, so
    the table stays a table while disputes still substitute and mark.
    <sup> marks in the verbatim gaps are re-rendered on their tokens,
    so they are stripped from the gaps."""
    out: list[str] = []
    pos = 0
    i = 0

    def gap(upto: int) -> None:
        nonlocal pos
        if upto > pos:
            out.append(_SUP_TAG.sub("", item_html[pos:upto]))
            pos = upto

    while i < len(tokens):
        t = tokens[i]
        if t.get("substituted") or t.get("empty"):
            rs = t.get("replaced_span") or [pos, pos]
            group = [t]
            while (
                i + 1 < len(tokens)
                and tokens[i + 1].get("dispute") == t["dispute"]
                and (
                    tokens[i + 1].get("substituted")
                    or tokens[i + 1].get("empty")
                )
            ):
                i += 1
                group.append(tokens[i])
            gap(max(rs[0], pos))
            seg = " ".join(_word_html(g) for g in group)
            out.append(_wrap_seg(t, seg))
            pos = max(rs[1], pos)
        else:
            s, e = t["span"] or [pos, pos]
            gap(max(s, pos))
            out.append(_wrap_seg(t, _word_html(t)))
            pos = max(e, pos)
        i += 1
    gap(len(item_html))
    return "".join(out)


def render_html(
    final: list[dict],
    recon_main: dict,
    main_blocks: list[dict],
    figure_url: Callable[[list[int]], str] | None = None,
) -> str:
    """The final page HTML: the main reconstruction's items in reading
    order, each one block element carrying its role. Image items render
    as <figure data-bbox> placeholders — empty in the stored artifact
    (the consumer crops the region from the canonical page PNG); a
    consumer that CAN serve those crops passes `figure_url` (bbox ->
    image URL) and gets the crop rendered inline."""
    by_item: dict[int, list[dict]] = {}
    for t in final:
        by_item.setdefault(t["item"], []).append(t)
    bbox_of = {b["id"]: b.get("bbox") for b in main_blocks}
    blocks: list[str] = []
    for it in recon_main["items"]:
        if it["role"] == "image":
            bbox = bbox_of.get(it["id"])
            attr = (
                ' data-bbox="' + ",".join(str(int(c)) for c in bbox) + '"'
                if bbox
                else ""
            )
            img = (
                f'<img src="{figure_url(bbox)}" '
                f'alt="image region of the page render">'
                if bbox and figure_url is not None
                else ""
            )
            blocks.append(
                f'<figure data-item="{it["id"]}"{attr}>{img}</figure>'
            )
            continue
        tokens = by_item.get(it["id"])
        if not tokens:
            continue
        item_html = it.get("html", "")
        if "<table" in item_html:
            # structural item: splice tokens back into its own html
            blocks.append(
                f'<div data-item="{it["id"]}" data-role="{it["role"]}">'
                f"{_splice_item_html(item_html, tokens)}</div>"
            )
            continue
        el = _ROLE_ELEMENT.get(it["role"], "p")
        blocks.append(
            f'<{el} data-item="{it["id"]}" data-role="{it["role"]}">'
            f"{_item_html(tokens)}</{el}>"
        )
    return "\n".join(blocks)


def final_text(final: list[dict], recon_main: dict) -> str:
    """The final plain reading text (displays only — no marks, stars,
    or styling; source spacing restored), one line per skeleton item."""
    by_item: dict[int, list[tuple[str, bool]]] = {}
    for t in final:
        if t.get("empty"):
            continue
        by_item.setdefault(t["item"], []).append(
            (t["display"], bool(t.get("glue")))
        )
    return "\n".join(
        _join(by_item[it["id"]])
        for it in recon_main["items"]
        if it["role"] != "image" and by_item.get(it["id"])
    )


# ── entry point ──────────────────────────────────────────────────────────


def assemble_page(
    norm: dict, cmp: dict, recon: dict, main_blocks: list[dict]
) -> dict:
    """The assemble record for one page: the final HTML and text, the
    low-confidence dispute ids the HTML marks, and the page metrics."""
    final = resolve_tokens(norm, cmp, recon)
    n_styled = _union_styling(final, norm)
    n_stars = _insert_stars(final, norm)
    disputes = cmp["disputes"]
    return {
        "html": render_html(final, recon["main"], main_blocks),
        "text": final_text(final, recon["main"]),
        "low_confidence": [d["id"] for d in disputes if d["low_confidence"]],
        "metrics": {
            "units": sum(1 for t in final if not t.get("empty")),
            "substitutions": sum(
                1 for d in disputes if d["verdict"] != "main"
            ),
            "substituted_units": sum(1 for t in final if t["substituted"]),
            "n_low_confidence": sum(
                1 for d in disputes if d["low_confidence"]
            ),
            "n_high_risk": sum(1 for d in disputes if d["high_risk"]),
            "styling_unioned": n_styled,
            "star_pages": n_stars,
        },
    }
