"""Render Phase 1 tagged artifacts as browsable HTML.

For each `data/tagged/{cluster_id}.json`, emits:

- `data/tagged_html/{cluster_id}.html` — the opinion text with each `<cited>`
  tag rendered as a colored pill (color = stable hash of `group`, so the same
  cited case shows the same color across its short cites / supra / Id).
  Hovering a pill shows id + group; clicking highlights every tag in the same
  group so chains are easy to trace.
- `data/tagged_html/style.css`, `script.js` — shared assets.
- `data/tagged_html/index.html` — sortable table of all clusters.

Usage:
    python render_tagged.py                          # all clusters
    python render_tagged.py --clusters 112786 1722   # subset
"""

import argparse
import html
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
TAGGED_DIR = DATA_DIR / "tagged"
OUT_DIR = DATA_DIR / "tagged_html"

_CITED_RE = re.compile(
    r'<cited id="(\d+)" group="(g\d+)">(.*?)</cited>', re.DOTALL
)

# Distinct pastel hues. Solo groups + chains both pick from this palette via a
# stable hash on the integer suffix of `gN`, so the same group_id always lands
# on the same color within a cluster.
_COLORS = [
    "#ffd4d4", "#ffe5c9", "#fff3c2", "#e8fac2", "#caf5d4", "#c2f0e9",
    "#c4e8fa", "#cfd9fc", "#dccffc", "#eccffc", "#fad0ee", "#fbcfd6",
    "#ffd9b3", "#ffe9b3", "#e5f0a0", "#b8e6a0", "#a8e8d0", "#a8d8f0",
]


def _group_color(group_id: str) -> str:
    """Stable color for `gN`. Solo groups still get a tint; chains share one."""
    try:
        n = int(group_id.lstrip("g"))
    except ValueError:
        n = abs(hash(group_id))
    return _COLORS[n % len(_COLORS)]


def _render_tagged_text(tagged_text: str) -> str:
    """Escape the prose and turn `<cited>` tags into colored interactive spans."""
    out: list[str] = []
    last = 0
    for m in _CITED_RE.finditer(tagged_text):
        out.append(html.escape(tagged_text[last:m.start()]))
        id_, group, inner = m.group(1), m.group(2), m.group(3)
        color = _group_color(group)
        title = html.escape(f"id={id_} group={group}")
        out.append(
            f'<span class="cited" data-id="{id_}" data-group="{group}" '
            f'title="{title}" style="background:{color};">'
            f'{html.escape(inner)}'
            f'<span class="badge">·{id_}</span></span>'
        )
        last = m.end()
    out.append(html.escape(tagged_text[last:]))
    return "".join(out)


_STYLE_CSS = """
body { font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       padding: 1em 2em; max-width: 1200px; margin: 0 auto;
       background: #fafafa; color: #222; }
h1 { margin: 0 0 0.3em 0; }
h2 { margin: 1.6em 0 0.5em 0; padding-bottom: 0.2em;
     border-bottom: 1px solid #ddd; font-size: 16px; }
h2 .meta { color: #888; font-weight: normal; font-size: 13px; }
nav.toc { background: #fff; padding: 0.6em 1em; border: 1px solid #ddd;
          border-radius: 6px; margin-bottom: 1em;
          position: sticky; top: 0; z-index: 10; }
nav.toc a { margin-right: 1em; text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
.summary { background: #fff; padding: 0.75em 1em; border: 1px solid #ddd;
           border-radius: 6px; margin-bottom: 1em; }
.summary dl { margin: 0; display: grid; grid-template-columns: 220px 1fr;
              gap: 0.25em 1em; }
.summary dt { font-weight: 600; color: #555; }
.summary dd { margin: 0; }
.opinion-body { background: #fff; padding: 1em 1.25em;
                border: 1px solid #ddd; border-radius: 6px;
                white-space: pre-wrap; word-wrap: break-word;
                font-family: ui-monospace, Menlo, monospace; font-size: 13px; }
.cited { padding: 0.05em 0.35em; margin: 0 0.05em;
         border-radius: 3px; cursor: pointer;
         border: 1px solid transparent; transition: outline 0.05s; }
.cited:hover { border-color: #444; }
.cited.highlight { outline: 2px solid #000; outline-offset: -2px;
                   border-color: transparent; }
.cited .badge { font-size: 10px; color: #555; margin-left: 0.15em;
                font-family: ui-monospace, Menlo, monospace; }
.misses { background: #fff8f0; border: 1px solid #f0c890;
          padding: 0.75em 1em; border-radius: 6px; margin-top: 1em; }
.misses code { font-family: ui-monospace, Menlo, monospace;
               background: #fff; padding: 1px 4px; border-radius: 3px; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { border: 1px solid #ddd; padding: 0.35em 0.6em; text-align: left;
         font-size: 13px; }
th { background: #f0f0f0; cursor: pointer; user-select: none; }
th:hover { background: #e8e8e8; }
tr:hover td { background: #f8f8f8; }
"""

_CLUSTER_JS = """
// Click any <cited> tag to highlight every other tag in the same group.
// Click elsewhere to clear.
document.addEventListener('click', function(e) {
  document.querySelectorAll('.cited.highlight')
          .forEach(el => el.classList.remove('highlight'));
  const tag = e.target.closest('.cited');
  if (!tag) return;
  const group = tag.dataset.group;
  document.querySelectorAll('.cited[data-group="' + group + '"]')
          .forEach(el => el.classList.add('highlight'));
});
"""

_INDEX_JS = """
// Click a column header to sort.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('th').forEach((th, idx) => {
    th.addEventListener('click', () => {
      const tbody = th.closest('table').querySelector('tbody');
      const rows = Array.from(tbody.rows);
      const asc = th.dataset.sort !== 'asc';
      rows.sort((a, b) => {
        const av = a.cells[idx].innerText;
        const bv = b.cells[idx].innerText;
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? av.localeCompare(bv) : bv.localeCompare(av);
      });
      rows.forEach(r => tbody.appendChild(r));
      document.querySelectorAll('th').forEach(t => delete t.dataset.sort);
      th.dataset.sort = asc ? 'asc' : 'desc';
    });
  });
});
"""


def render_cluster_page(artifact: dict) -> str:
    """One cluster → one self-contained HTML page (references shared CSS/JS)."""
    cid = artifact["cluster_id"]
    opinions = artifact["opinions"]
    groups = artifact.get("groups", {})
    miss_details = artifact.get("unmatched_miss_details", [])
    empty_html = artifact.get("empty_html", [])

    toc = "".join(
        f'<a href="#op-{i}">{op["opinion_type"]} '
        f'<span style="color:#888">({op["source_opinion_id"]})</span></a>'
        for i, op in enumerate(opinions)
    )

    parts: list[str] = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        f'<title>Cluster {cid}</title>',
        '<link rel="stylesheet" href="style.css">',
        '</head><body>',
        f'<nav class="toc"><a href="index.html">← Index</a> | {toc}</nav>',
        f'<h1>Cluster {cid}</h1>',
        '<div class="summary"><dl>',
        f'<dt>Opinions</dt><dd>{len(opinions)}</dd>',
        f'<dt>Tags</dt><dd>{artifact["n_tags"]:,}</dd>',
        f'<dt>Groups</dt><dd>{len(groups):,}</dd>',
        f'<dt>Unmatched misses</dt><dd>{artifact["n_unmatched_misses"]}</dd>',
        f'<dt>Empty-html opinions</dt><dd>{", ".join(empty_html) if empty_html else "—"}</dd>',
        '</dl></div>',
    ]

    for i, op in enumerate(opinions):
        op_type = op["opinion_type"]
        soid = op["source_opinion_id"]
        text = op["tagged_text"]
        n_tags_op = text.count('<cited id=')
        parts.append(
            f'<h2 id="op-{i}">{op_type} '
            f'<span class="meta">(opinion id {soid} · {len(text):,} chars · '
            f'{n_tags_op:,} tags)</span></h2>'
        )
        parts.append(
            f'<div class="opinion-body">{_render_tagged_text(text)}</div>'
        )

    if miss_details:
        parts.append('<div class="misses">')
        parts.append(
            f'<strong>Unmatched citation misses '
            f'({artifact["n_unmatched_misses"]}):</strong><br>'
        )
        parts.append(
            '<small>UnmatchedCitation rows whose citation_string was not '
            'found in the tagged text. Most are eyecite mis-parses of '
            'line-numbered trial-court text.</small><ul>'
        )
        for m in miss_details:
            cite = m.get("citation_string", "")
            op_type = m.get("opinion_type", "?")
            parts.append(
                f'<li><code>{html.escape(repr(cite))}</code> '
                f'<span style="color:#888">({op_type})</span></li>'
            )
        parts.append('</ul></div>')

    parts.append('<script src="script.js"></script>')
    parts.append('</body></html>')
    return "".join(parts)


def render_index_page(summaries: list[dict]) -> str:
    rows = "".join(
        f'<tr>'
        f'<td><a href="{s["cluster_id"]}.html">{s["cluster_id"]}</a></td>'
        f'<td>{s["n_opinions"]}</td>'
        f'<td>{s["n_tags"]:,}</td>'
        f'<td>{s["n_groups"]:,}</td>'
        f'<td>{s["n_misses"]}</td>'
        f'<td>{",".join(s["opinion_types"])}</td>'
        f'</tr>'
        for s in summaries
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Phase 1 tagged artifacts — index</title>'
        '<link rel="stylesheet" href="style.css">'
        '</head><body>'
        f'<h1>Phase 1 tagged artifacts ({len(summaries):,} clusters)</h1>'
        '<p>Click a column header to sort. Click a cluster id to open.</p>'
        '<table><thead><tr>'
        '<th>cluster_id</th><th>n_opinions</th><th>n_tags</th>'
        '<th>n_groups</th><th>n_misses</th><th>opinion_types</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        '<script src="index.js"></script></body></html>'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--clusters", nargs="*", type=int, default=None,
        help="Specific cluster ids (default: all in data/tagged/).",
    )
    args = ap.parse_args()

    if not TAGGED_DIR.is_dir():
        raise SystemExit(f"ERROR: {TAGGED_DIR} not found. Run run_assemble.py first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write shared assets once.
    (OUT_DIR / "style.css").write_text(_STYLE_CSS)
    (OUT_DIR / "script.js").write_text(_CLUSTER_JS)
    (OUT_DIR / "index.js").write_text(_INDEX_JS)

    paths = (
        [TAGGED_DIR / f"{cid}.json" for cid in args.clusters]
        if args.clusters
        else sorted(TAGGED_DIR.glob("*.json"), key=lambda p: int(p.stem))
    )

    summaries: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"  SKIP — {p} not found")
            continue
        artifact = json.loads(p.read_text())
        cid = artifact["cluster_id"]
        (OUT_DIR / f"{cid}.html").write_text(render_cluster_page(artifact))
        summaries.append({
            "cluster_id": cid,
            "n_tags": artifact["n_tags"],
            "n_opinions": len(artifact["opinions"]),
            "n_misses": artifact["n_unmatched_misses"],
            "n_groups": len(artifact.get("groups", {})),
            "opinion_types": [o["opinion_type"] for o in artifact["opinions"]],
        })
        print(f'  {cid}: {artifact["n_tags"]:,} tags → {cid}.html')

    (OUT_DIR / "index.html").write_text(render_index_page(summaries))

    print()
    print(f"Wrote {len(summaries):,} cluster pages + index.html → {OUT_DIR}")
    print(f"Open: file://{(OUT_DIR / 'index.html').resolve()}")


if __name__ == "__main__":
    main()
