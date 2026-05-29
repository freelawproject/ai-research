"""Render the Phase 2 prepped batch as browsable HTML for visual review.

Reads `data/phase2_input.jsonl` (the actual Bedrock batch input) plus
`data/phase2_calls/{cluster_id}.json` (per-call CallInput metadata saved
during prep) and emits:

- `data/phase2_prep_html/index.html` — sortable cluster table.
- `data/phase2_prep_html/{cluster_id}.html` — one page per cluster, showing
  every call's bin-packed items with `tagged_text` rendered as colored
  citation pills (same styling as the Phase 1 viewer). Hover a tag for
  `id`/`group`; click to highlight every tag in the same group.
- `data/phase2_prep_html/_prompt.html` — the citation_grouping system prompt
  + tool spec, shown once (they're constant across every record).
- `data/phase2_prep_html/style.css` + `script.js` — shared assets.

Run AFTER `run_phase2.py --prep-only`. Pure stdlib, no AWS.

Usage:
    python render_prepped.py
    python render_prepped.py --clusters 100887 1722
"""

import argparse
import html
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "citator-pipeline")
)
from utils.instructions import citation_grouping  # noqa: E402
from utils.paginate_and_call import parse_record_id  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
JSONL_PATH = DATA_DIR / "phase2_input.jsonl"
CALLS_DIR = DATA_DIR / "phase2_calls"
OUT_DIR = DATA_DIR / "phase2_prep_html"

_CITED_RE = re.compile(
    r'<cited id="(\d+)" group="(g\d+)">(.*?)</cited>', re.DOTALL
)

_COLORS = [
    "#ffd4d4", "#ffe5c9", "#fff3c2", "#e8fac2", "#caf5d4", "#c2f0e9",
    "#c4e8fa", "#cfd9fc", "#dccffc", "#eccffc", "#fad0ee", "#fbcfd6",
    "#ffd9b3", "#ffe9b3", "#e5f0a0", "#b8e6a0", "#a8e8d0", "#a8d8f0",
]


def _group_color(group_id: str) -> str:
    try:
        n = int(group_id.lstrip("g"))
    except ValueError:
        n = abs(hash(group_id))
    return _COLORS[n % len(_COLORS)]


def _render_tagged_text(tagged_text: str) -> str:
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
h2 { margin: 1.8em 0 0.5em 0; padding-bottom: 0.2em;
     border-bottom: 2px solid #999; font-size: 18px; }
h3 { margin: 1.2em 0 0.4em 0; padding-bottom: 0.15em;
     border-bottom: 1px solid #ddd; font-size: 15px; }
h2 .meta, h3 .meta { color: #888; font-weight: normal; font-size: 13px; }
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
.call-block { background: #fdfdfd; padding: 0.5em 1em 1em;
              border: 1px solid #ccc; border-radius: 6px; margin-bottom: 1em; }
.call-meta { color: #666; font-size: 12px; margin-bottom: 0.5em; }
.call-meta code { background: #efefef; padding: 1px 4px; border-radius: 3px; }
.opinion-body { background: #fff; padding: 0.8em 1em;
                border: 1px solid #ddd; border-radius: 4px;
                white-space: pre-wrap; word-wrap: break-word;
                font-family: ui-monospace, Menlo, monospace; font-size: 12.5px;
                max-height: 600px; overflow: auto; }
.opinion-head { color: #444; font-size: 12px; margin: 0.4em 0 0.2em; }
.opinion-head code { background: #efefef; padding: 1px 4px; border-radius: 3px; }
.cited { padding: 0.05em 0.35em; margin: 0 0.05em;
         border-radius: 3px; cursor: pointer;
         border: 1px solid transparent; transition: outline 0.05s; }
.cited:hover { border-color: #444; }
.cited.highlight { outline: 2px solid #000; outline-offset: -2px;
                   border-color: transparent; }
.cited .badge { font-size: 10px; color: #555; margin-left: 0.15em;
                font-family: ui-monospace, Menlo, monospace; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { border: 1px solid #ddd; padding: 0.35em 0.6em; text-align: left;
         font-size: 13px; }
th { background: #f0f0f0; cursor: pointer; user-select: none; }
th:hover { background: #e8e8e8; }
tr:hover td { background: #f8f8f8; }
pre.prompt { background: #fff; padding: 1em; border: 1px solid #ddd;
             border-radius: 6px; white-space: pre-wrap; word-wrap: break-word;
             font-family: ui-monospace, Menlo, monospace; font-size: 12.5px;
             line-height: 1.5; }
pre.schema { background: #f6f8fa; padding: 1em; border: 1px solid #ddd;
             border-radius: 6px; font-family: ui-monospace, Menlo, monospace;
             font-size: 12px; line-height: 1.4; overflow: auto;
             max-height: 600px; }
"""

_CLUSTER_JS = """
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


def load_records_by_cluster() -> dict[int, dict[int, dict]]:
    """Parse phase2_input.jsonl into {cluster_id: {call_idx: record}}."""
    by_cluster: dict[int, dict[int, dict]] = defaultdict(dict)
    if not JSONL_PATH.exists():
        raise SystemExit(
            f"ERROR: {JSONL_PATH} not found. Run `python run_phase2.py --prep-only` first."
        )
    with open(JSONL_PATH) as f:
        for line in f:
            rec = json.loads(line)
            parsed = parse_record_id(rec.get("recordId", ""))
            if parsed is None:
                continue
            cid, call_idx = parsed
            by_cluster[cid][call_idx] = rec
    return by_cluster


def load_calls_metadata(cluster_id: int) -> list[list[dict]]:
    """Return the saved CallInput items list for one cluster
    (each inner list is one call's items)."""
    path = CALLS_DIR / f"{cluster_id}.json"
    if not path.exists():
        return []
    serialized = json.loads(path.read_text())
    return [c["items"] for c in serialized]


def render_cluster_page(cluster_id: int, calls_records: dict[int, dict],
                        calls_items: list[list[dict]]) -> str:
    """One cluster → one HTML page. Each call shown with its items + tagged_text."""
    n_calls = len(calls_records)
    sorted_indices = sorted(calls_records)
    n_opinions = sum(len(items) for items in calls_items)
    total_tokens = sum(it.get("tokens", 0) for items in calls_items for it in items)

    toc = "".join(
        f'<a href="#call-{i}">call {i}</a>'
        for i in sorted_indices
    )

    parts: list[str] = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        f'<title>Prepped — cluster {cluster_id}</title>',
        '<link rel="stylesheet" href="style.css">',
        '</head><body>',
        f'<nav class="toc"><a href="index.html">← Index</a> '
        f'| <a href="_prompt.html">Prompt + tool</a> | {toc}</nav>',
        f'<h1>Prepped batch — cluster {cluster_id}</h1>',
        '<div class="summary"><dl>',
        f'<dt>Calls (LLM records)</dt><dd>{n_calls}</dd>',
        f'<dt>Total opinions/chunks (items)</dt><dd>{n_opinions}</dd>',
        f'<dt>Total input tokens (sum of items)</dt><dd>{total_tokens:,}</dd>',
        '</dl></div>',
    ]

    for call_idx in sorted_indices:
        rec = calls_records[call_idx]
        items = calls_items[call_idx] if call_idx < len(calls_items) else []
        call_tokens = sum(it.get("tokens", 0) for it in items)
        record_id = rec.get("recordId", f"{cluster_id}_call_{call_idx}")
        max_tok = rec.get("modelInput", {}).get("max_tokens", "?")

        parts.append(f'<h2 id="call-{call_idx}">Call {call_idx} '
                     f'<span class="meta">({len(items)} items · {call_tokens:,} input tokens · '
                     f'max_tokens={max_tok})</span></h2>')
        parts.append('<div class="call-block">')
        parts.append(f'<div class="call-meta">'
                     f'recordId: <code>{html.escape(record_id)}</code></div>')

        for item in sorted(items, key=lambda x: x.get("opinion_handle", 0)):
            handle = item.get("opinion_handle", "?")
            op_type = item.get("opinion_type", "?")
            soid = item.get("source_opinion_id", "?")
            ci = item.get("chunk_index", 1)
            tc = item.get("total_chunks", 1)
            chunk_start = item.get("chunk_start", 0)
            tokens = item.get("tokens", 0)
            text = item.get("tagged_text", "")
            n_tags = text.count('<cited id=')

            parts.append(
                f'<h3 id="call-{call_idx}-h{handle}">opinion_handle <code>{handle}</code> '
                f'<span class="meta">— {op_type} · '
                f'source_opinion_id {soid} · '
                f'chunk {ci}/{tc} · '
                f'chunk_start {chunk_start:,} · '
                f'{len(text):,} chars · {tokens:,} tokens · {n_tags:,} tags</span></h3>'
            )
            parts.append(
                f'<div class="opinion-body">{_render_tagged_text(text)}</div>'
            )
        parts.append('</div>')

    parts.append('<script src="script.js"></script>')
    parts.append('</body></html>')
    return "".join(parts)


def render_index_page(summaries: list[dict]) -> str:
    n_calls_total = sum(s["n_calls"] for s in summaries)
    rows = "".join(
        f'<tr>'
        f'<td><a href="{s["cluster_id"]}.html">{s["cluster_id"]}</a></td>'
        f'<td>{s["n_calls"]}</td>'
        f'<td>{s["n_opinions"]}</td>'
        f'<td>{s["n_tokens"]:,}</td>'
        f'</tr>'
        for s in summaries
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Phase 2 prepped batch — index</title>'
        '<link rel="stylesheet" href="style.css">'
        '</head><body>'
        f'<h1>Phase 2 prepped batch ({len(summaries):,} clusters · '
        f'{n_calls_total:,} LLM calls)</h1>'
        '<nav class="toc"><a href="_prompt.html">System prompt + tool spec</a></nav>'
        '<p>Click a column header to sort. Click a cluster id to open.</p>'
        '<table><thead><tr>'
        '<th>cluster_id</th><th>n_calls</th><th>n_opinions</th><th>input_tokens</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        '<script src="index.js"></script></body></html>'
    )


def render_prompt_page(sample_record: dict | None) -> str:
    """A shared page showing the system prompt + tool spec (constant across records)."""
    prompt_html = html.escape(citation_grouping)
    sample_html = ""
    if sample_record:
        mi = sample_record.get("modelInput", {})
        tool = mi.get("tools", [{}])[0]
        schema_pretty = json.dumps(tool.get("input_schema", {}), indent=2, ensure_ascii=False)
        sample_html = (
            f'<h2>Tool spec (forced via tool_choice)</h2>'
            f'<div class="summary"><dl>'
            f'<dt>Tool name</dt><dd><code>{html.escape(tool.get("name", "?"))}</code></dd>'
            f'<dt>Description</dt><dd>{html.escape(tool.get("description", ""))}</dd>'
            f'<dt>max_tokens</dt><dd>{mi.get("max_tokens", "?")}</dd>'
            f'<dt>tool_choice</dt><dd><code>{html.escape(json.dumps(mi.get("tool_choice", {})))}</code></dd>'
            f'</dl></div>'
            f'<h3>Input schema</h3>'
            f'<pre class="schema">{html.escape(schema_pretty)}</pre>'
        )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Phase 2 — system prompt + tool spec</title>'
        '<link rel="stylesheet" href="style.css">'
        '</head><body>'
        '<nav class="toc"><a href="index.html">← Index</a></nav>'
        '<h1>System prompt + tool spec</h1>'
        '<p>These are constant across every batch record.</p>'
        '<h2>System prompt</h2>'
        f'<pre class="prompt">{prompt_html}</pre>'
        f'{sample_html}'
        '</body></html>'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--clusters", nargs="*", type=int, default=None,
        help="Specific cluster ids (default: all in the prepped JSONL).",
    )
    args = ap.parse_args()

    by_cluster = load_records_by_cluster()
    if not by_cluster:
        raise SystemExit("ERROR: no records parsed from JSONL.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "style.css").write_text(_STYLE_CSS)
    (OUT_DIR / "script.js").write_text(_CLUSTER_JS)
    (OUT_DIR / "index.js").write_text(_INDEX_JS)

    cluster_ids = (
        sorted(set(args.clusters) & set(by_cluster))
        if args.clusters else sorted(by_cluster)
    )

    summaries: list[dict] = []
    sample_record = None
    for cid in cluster_ids:
        calls_records = by_cluster[cid]
        items = load_calls_metadata(cid)
        if not items:
            print(f"  {cid}: SKIP — no calls metadata at {CALLS_DIR / f'{cid}.json'}")
            continue
        if sample_record is None and calls_records:
            sample_record = next(iter(calls_records.values()))
        (OUT_DIR / f"{cid}.html").write_text(
            render_cluster_page(cid, calls_records, items)
        )
        n_tokens = sum(it.get("tokens", 0) for ll in items for it in ll)
        n_opinions = sum(len(ll) for ll in items)
        summaries.append({
            "cluster_id": cid,
            "n_calls": len(calls_records),
            "n_opinions": n_opinions,
            "n_tokens": n_tokens,
        })
        print(f"  {cid}: {len(calls_records)} calls, "
              f"{n_opinions} items, {n_tokens:,} tokens → {cid}.html")

    (OUT_DIR / "index.html").write_text(render_index_page(summaries))
    (OUT_DIR / "_prompt.html").write_text(render_prompt_page(sample_record))

    print()
    print(f"Wrote {len(summaries):,} cluster pages + index + prompt → {OUT_DIR}")
    print(f"Open: file://{(OUT_DIR / 'index.html').resolve()}")


if __name__ == "__main__":
    main()
