"""Render Phase 2 grouped outputs as browsable HTML for visual review.

Reads `data/grouped/{cluster_id}.json` plus the Phase 1 tagged artifact
(`../experiments_05262026/data/tagged/{cluster_id}.json`) for source context,
and the four audit jsonl logs. Per cluster, emits a page with:

- Summary header (n_cases, n_occurrences, audit tallies).
- One **case card** per cited_case: mainCitationString / parallelCitationString
  / caseName / ocr fields + the occurrences list (each with opinion_type,
  source_opinion_id, offset, citation_string, and a tagged/untagged pill).
- A **source-text overlay** per opinion: the Phase 1 tagged_text re-rendered,
  but each `<cited>` tag is colored by which cited_case it was assigned to
  (post-grouping). Tags the model implicitly rejected are grayed out. Hover
  any tag to see id + assigned case; click to highlight every tag belonging
  to the same case across the page.
- An **audit panel** filtered to this cluster (untagged accepted/dropped,
  implicit rejections, invalid ids, caseName warnings).

`data/grouped_html/index.html` is a sortable cluster table.

Run AFTER `run_phase2.py` has produced the grouped outputs.

Usage:
    python render_grouped.py
    python render_grouped.py --clusters 100887 117935
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
from utils.postprocess_offsets import _normalize_canonical  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
GROUPED_DIR = DATA_DIR / "grouped"
TAGGED_DIR = Path(__file__).parent.parent / "experiments_05262026" / "data" / "tagged"
OUT_DIR = DATA_DIR / "grouped_html"

UNTAGGED_LOG = DATA_DIR / "grouped_untagged_log.jsonl"
UNROUTED_LOG = DATA_DIR / "grouped_unrouted_ids.jsonl"
ID_CONFLICTS_LOG = DATA_DIR / "grouped_id_conflicts.jsonl"
INVALID_LOG = DATA_DIR / "grouped_invalid_ids.jsonl"
CASENAME_WARN_LOG = DATA_DIR / "grouped_casename_warnings.jsonl"

_CITED_RE = re.compile(
    r'<cited id="(\d+)" group="(g\d+)">(.*?)</cited>', re.DOTALL
)

_COLORS = [
    "#ffd4d4", "#ffe5c9", "#fff3c2", "#e8fac2", "#caf5d4", "#c2f0e9",
    "#c4e8fa", "#cfd9fc", "#dccffc", "#eccffc", "#fad0ee", "#fbcfd6",
    "#ffd9b3", "#ffe9b3", "#e5f0a0", "#b8e6a0", "#a8e8d0", "#a8d8f0",
]


def _case_color(case_idx: int) -> str:
    return _COLORS[case_idx % len(_COLORS)]


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _filter_to_cluster(rows: list[dict], cluster_id: int) -> list[dict]:
    return [r for r in rows if r.get("cluster_id") == cluster_id]


def render_overlay(
    tagged_text: str,
    id_to_case: dict[int, int],
    case_for_untagged: list[dict],
    source_opinion_id: int,
    id_to_metadata_map: dict,
    chunk_start: int = 0,
) -> str:
    """Re-render `tagged_text` with each <cited> tag colored by assigned case.

    id_to_case: {Phase 1 id → cited_case index in this cluster's output}. Ids
        absent from the map are model-rejected; rendered with the rejected style.
    case_for_untagged: list of {offset, snippet, case_idx} for untagged
        occurrences whose offset falls inside this chunk; rendered inline.
    chunk_start: this chunk's start offset within the original opinion text
        (used to filter untagged occurrences whose offsets are stored opinion-
        relative).
    """
    # Tag-driven pass first; then splice untagged inline at their offsets.
    pieces: list[tuple[int, int, str]] = []  # (start, end, html) in chunk coords

    for m in _CITED_RE.finditer(tagged_text):
        id_ = int(m.group(1))
        group = m.group(2)
        inner = m.group(3)
        case_idx = id_to_case.get(id_)
        # Compute the opinion-relative offset via id_to_metadata_map so the key
        # matches what occurrences in case cards store.
        meta = id_to_metadata_map.get(str(id_))
        occ_key = f'{source_opinion_id}-{meta["offset"]}' if meta else ""
        if case_idx is None:
            cls = "cited rejected"
            color = "#eee"
            title = f"id={id_} group={group} — model REJECTED"
            case_attr = ' data-rejected="1"'
        else:
            cls = "cited"
            color = _case_color(case_idx)
            title = f"id={id_} group={group} → case #{case_idx}"
            case_attr = f' data-case="{case_idx}"'
        pieces.append((m.start(), m.end(),
            f'<span class="{cls}" data-id="{id_}"{case_attr} '
            f'data-occ-key="{occ_key}" '
            f'title="{html.escape(title)}" style="background:{color};">'
            f'{html.escape(inner)}<span class="badge">·{id_}</span></span>'))

    # Untagged: snippets that the model added. Filter to those that fall in
    # this chunk and grep-locate them now (their stored offset is opinion-
    # relative; we already validated they were found, so str.find works).
    for u in case_for_untagged:
        # u: {offset (opinion-relative), snippet, case_idx}
        rel = u["offset"] - chunk_start
        if rel < 0 or rel >= len(tagged_text):
            continue
        # Verify the snippet still matches at this offset (sanity)
        if not tagged_text.startswith(u["snippet"], rel):
            # Try str.find as a fallback (chunked text might shift)
            idx = tagged_text.find(u["snippet"])
            if idx == -1:
                continue
            rel = idx
        case_idx = u["case_idx"]
        color = _case_color(case_idx)
        snippet = u["snippet"]
        title = f"UNTAGGED (model added) → case #{case_idx}: {snippet[:60]}"
        occ_key = f'{source_opinion_id}-{u["offset"]}'
        pieces.append((rel, rel + len(snippet),
            f'<span class="cited untagged" data-case="{case_idx}" '
            f'data-occ-key="{occ_key}" '
            f'title="{html.escape(title)}" style="background:{color}; border-style:dashed;">'
            f'{html.escape(snippet)}<span class="badge">U</span></span>'))

    # Sort by start; skip overlapping pieces (keep first encountered).
    pieces.sort(key=lambda p: p[0])
    out: list[str] = []
    cursor = 0
    for start, end, html_chunk in pieces:
        if start < cursor:  # overlap; skip
            continue
        out.append(html.escape(tagged_text[cursor:start]))
        out.append(html_chunk)
        cursor = end
    out.append(html.escape(tagged_text[cursor:]))
    return "".join(out)


_STYLE_CSS = """
body { font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       padding: 1em 1.5em; max-width: 1700px; margin: 0 auto;
       background: #fafafa; color: #222; }
h1 { margin: 0 0 0.3em 0; }
h2 { margin: 1.2em 0 0.5em 0; padding-bottom: 0.2em;
     border-bottom: 2px solid #999; font-size: 17px; }
h3 { margin: 1.1em 0 0.35em 0; padding-bottom: 0.15em;
     border-bottom: 1px solid #ddd; font-size: 14px; }
h2 .meta, h3 .meta { color: #888; font-weight: normal; font-size: 12px; }
nav.toc { background: #fff; padding: 0.5em 1em; border: 1px solid #ddd;
          border-radius: 6px; margin-bottom: 1em;
          position: sticky; top: 0; z-index: 20; }
nav.toc a { margin-right: 1em; text-decoration: none; }
.summary { background: #fff; padding: 0.6em 1em; border: 1px solid #ddd;
           border-radius: 6px; margin-bottom: 1em; }
.summary dl { margin: 0; display: grid; grid-template-columns: 240px 1fr;
              gap: 0.2em 1em; }
.summary dt { font-weight: 600; color: #555; }
.summary dd { margin: 0; }

/* Two-column layout: cases on the left, source overlay on the right. */
.layout { display: grid; grid-template-columns: 540px 1fr; gap: 1.5em;
          align-items: start; }
.left-pane { position: sticky; top: 3.5em; max-height: calc(100vh - 4.5em);
             overflow-y: auto; padding-right: 0.5em; }
.right-pane { min-width: 0; }  /* allow children to shrink/wrap in grid */
.left-pane h2 { margin-top: 0; }
.right-pane h2 { margin-top: 0; }

.diff-summary { background: #f5faff; padding: 0.4em 0.8em;
                border: 1px solid #cde; border-radius: 4px;
                margin-bottom: 0.6em; font-size: 12px; color: #345; }
.diff-summary strong { color: #024; }
.filters { background: #fff; padding: 0.5em 0.7em; border: 1px solid #ddd;
           border-radius: 4px; margin-bottom: 0.6em;
           display: grid; grid-template-columns: 60px 1fr; gap: 0.3em 0.6em;
           align-items: baseline; font-size: 11px; }
.filters .filter-label { font-weight: 600; color: #555; text-align: right; }
.filters .filter-buttons { display: flex; gap: 0.25em; flex-wrap: wrap; }
.filter-btn { padding: 0.15em 0.55em; font-size: 11px; cursor: pointer;
              border: 1px solid #bbb; background: #fff; border-radius: 3px;
              line-height: 1.3; }
.filter-btn:hover { background: #f0f0f0; }
.filter-btn.active { background: #345; color: #fff; border-color: #345; }
.filter-group-select { font-size: 11px; padding: 0.15em 0.3em;
                       border: 1px solid #bbb; border-radius: 3px;
                       min-width: 0; max-width: 100%; }

/* Export/import row */
.review-toolbar { display: flex; gap: 0.5em; margin-bottom: 0.6em;
                  font-size: 11px; align-items: center; flex-wrap: wrap; }
.review-toolbar button { padding: 0.2em 0.7em; font-size: 11px;
                          cursor: pointer; border: 1px solid #aaa;
                          background: #fff; border-radius: 3px; }
.review-toolbar button:hover { background: #f0f0f0; }
.review-toolbar .storage-note { color: #888; flex: 1; min-width: 0;
                                 font-style: italic; }
.badges { margin-bottom: 0.3em; }
.badge-flag { display: inline-block; padding: 1px 5px; border-radius: 3px;
              font-size: 9.5px; font-weight: 700; margin-right: 0.3em;
              letter-spacing: 0.3px; }
.case-meta.diff { color: #345; background: #f0f5fa; padding: 2px 5px;
                  border-radius: 3px; margin-top: 0.25em; font-size: 11px; }
.case-card { background: #fff; padding: 0.5em 0.8em 0.6em;
             border: 1px solid #ddd;
             border-left: 4px solid var(--case-color, #ccc);
             border-radius: 4px; margin-bottom: 0.5em; cursor: pointer; }
.case-card.filtered-out { display: none; }
.case-card:hover { background: #f9f9f9; }
.case-card.highlight { background: #fffbea; border-left-color: #cc0; }
.case-card h4 { margin: 0 0 0.25em; font-size: 13px; line-height: 1.3; }
.case-card .case-meta { color: #666; font-size: 11.5px; margin: 0.15em 0;
                        word-wrap: break-word; }
.case-card .case-meta code { background: #efefef; padding: 1px 4px; border-radius: 3px; }
.case-card .parallel { color: #555; font-style: italic; }
.case-card .ocr { color: #a60; font-size: 11.5px; margin-top: 0.2em; }
.case-card details { margin-top: 0.3em; }
.case-card summary { font-size: 11px; color: #666; cursor: pointer; }
.occ-list { font-family: ui-monospace, Menlo, monospace; font-size: 11px;
            margin: 0.3em 0 0 1em; padding-left: 0.5em; }
.occ-list li { margin: 0.1em 0; word-wrap: break-word; }
.occ-item { cursor: pointer; padding: 2px 4px; border-radius: 2px;
            display: flex; align-items: baseline; gap: 0.3em; flex-wrap: wrap; }
.occ-item:hover { background: #eef; }
.occ-item.highlight { background: #ffeb88; outline: 1px solid #aa0; }
.occ-item.state-approved { background: #e8f5e8; }
.occ-item.state-rejected { background: #fce8e6; }
.occ-item.state-approved .pill, .occ-item.state-rejected .pill { opacity: 0.6; }

/* Per-occurrence verify buttons (approve / reject) */
.vbtn { display: inline-block; cursor: pointer; user-select: none;
        font-size: 11px; line-height: 1; padding: 1px 5px; border-radius: 3px;
        border: 1px solid #bbb; background: #fff; color: #777;
        font-weight: 700; flex-shrink: 0; min-width: 16px; text-align: center; }
.vbtn:hover { background: #f0f0f0; }
.vbtn.vbtn-approve.active { background: #1a5; color: #fff; border-color: #1a5; }
.vbtn.vbtn-reject.active { background: #c33; color: #fff; border-color: #c33; }

.correction-input { width: 100%; margin-top: 0.25em; padding: 3px 5px;
                    font-size: 11px; border: 1px solid #c33;
                    background: #fffafa; border-radius: 3px;
                    font-family: inherit; resize: vertical; min-height: 2.2em;
                    box-sizing: border-box; }
.id-label code { background: #f0f0f0; padding: 0 3px; border-radius: 2px; }
.id-label.untagged-id { color: #705; font-style: italic; }
.missing-list { margin: 0.3em 0 0 1em; padding-left: 0.5em;
                font-family: ui-monospace, Menlo, monospace; font-size: 11px;
                list-style: '↳ '; }
.missing-item { cursor: pointer; padding: 1px 3px; border-radius: 2px; }
.missing-item:hover { background: #eef; }
.missing-item.highlight { background: #ffeb88; }

/* Mark-all button on each case card */
.mark-all-btn { font-size: 10px; padding: 1px 6px; cursor: pointer;
                border: 1px solid #aaa; background: #fff; border-radius: 3px;
                margin-left: 0.4em; vertical-align: middle; }
.mark-all-btn:hover { background: #f0f0f0; }

/* All-reviewed badges */
.reviewed-badge, .corrections-badge {
    display: none; padding: 1px 5px; border-radius: 3px;
    font-size: 9.5px; font-weight: 700; margin-right: 0.3em;
    letter-spacing: 0.3px;
}
.reviewed-badge { background: #c8eccd; color: #064; }
.corrections-badge { background: #fde0c4; color: #842; }
.case-card.all-approved .reviewed-badge { display: inline-block; }
.case-card.has-corrections .corrections-badge { display: inline-block; }
.case-card.all-approved { border-left-color: #1a5 !important; opacity: 0.75; }
.case-card.has-corrections { border-left-color: #d70 !important; }
.case-card.all-approved h4 { text-decoration: line-through; }

/* Source-tag reviewed style — green outline + ✓ for approve, red + ✗ for reject */
.cited.state-approved { outline: 2px solid #1a5; outline-offset: -2px; }
.cited.state-approved::after { content: " ✓"; color: #064; font-weight: 700;
                                font-size: 10px; margin-left: 2px; }
.cited.state-rejected { outline: 2px solid #c33; outline-offset: -2px;
                         background: #ffe8e6 !important; }
.cited.state-rejected::after { content: " ✗"; color: #802; font-weight: 700;
                                font-size: 10px; margin-left: 2px; }

/* Top progress meter */
.review-progress { background: #f0f5fa; padding: 0.4em 0.8em;
                   border: 1px solid #cde; border-radius: 4px;
                   font-size: 12px; color: #345; margin-bottom: 0.6em; }
.review-progress strong { color: #024; }
.review-bar { height: 4px; background: #ddd; border-radius: 2px;
              margin-top: 0.3em; overflow: hidden; }
.review-bar-fill { height: 100%; background: #1a5; transition: width 0.2s; }
.pill { display: inline-block; padding: 0 0.35em; border-radius: 3px;
        font-size: 9.5px; vertical-align: middle; }
.pill.tagged { background: #d4e8ff; color: #024; }
.pill.untagged { background: #f0d0ff; color: #402; }

.opinion-body { background: #fff; padding: 0.8em 1em;
                border: 1px solid #ddd; border-radius: 4px;
                white-space: pre-wrap; word-wrap: break-word;
                font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
.cited { padding: 0.05em 0.35em; margin: 0 0.05em;
         border-radius: 3px; cursor: pointer;
         border: 1px solid transparent; transition: outline 0.05s; }
.cited:hover { border-color: #444; }
.cited.highlight { outline: 2px solid #000; outline-offset: -2px;
                   border-color: transparent; }
.cited.rejected { color: #888; text-decoration: line-through; opacity: 0.7; }
.cited .badge { font-size: 10px; color: #555; margin-left: 0.15em;
                font-family: ui-monospace, Menlo, monospace; }
.audit { background: #fffbeb; padding: 0.5em 1em; border: 1px solid #f0d890;
         border-radius: 4px; margin-top: 1em; }
.audit h3 { margin-top: 0; border: none; }
.audit details { margin: 0.4em 0; }
.audit summary { cursor: pointer; font-weight: 600; }
.audit pre { background: #fff; padding: 0.5em; border: 1px solid #ddd;
             border-radius: 3px; font-size: 11px; overflow: auto;
             max-height: 240px; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { border: 1px solid #ddd; padding: 0.35em 0.6em; text-align: left;
         font-size: 13px; }
th { background: #f0f0f0; cursor: pointer; user-select: none; }
th:hover { background: #e8e8e8; }
tr:hover td { background: #f8f8f8; }

/* Narrow viewports: drop to single-column. */
@media (max-width: 1100px) {
  .layout { grid-template-columns: 1fr; }
  .left-pane { position: static; max-height: none; overflow: visible; }
}
"""

_CLUSTER_JS = """
const CLUSTER_ID = document.querySelector('.left-pane').dataset.clusterId;
const REVIEW_KEY = 'phase2_review_' + CLUSTER_ID;

// ─ State shape ─
// localStorage[REVIEW_KEY] = JSON of { occKey: {state: 'approved'|'rejected', correction?: string} }
// Legacy boolean values are auto-migrated on load (true → {state: 'approved'}).
function loadReview() {
  try {
    const raw = JSON.parse(localStorage.getItem(REVIEW_KEY)) || {};
    // Migrate legacy bool → object
    const migrated = {};
    Object.keys(raw).forEach(k => {
      if (raw[k] === true) migrated[k] = {state: 'approved'};
      else if (typeof raw[k] === 'object' && raw[k] && raw[k].state) migrated[k] = raw[k];
    });
    return migrated;
  } catch { return {}; }
}
function saveReview(state) {
  localStorage.setItem(REVIEW_KEY, JSON.stringify(state));
}

// Sync one occurrence's state across the page (vbtns, <li> styling, source tag styling).
function syncOccState(occKey, entry) {
  const state = entry ? entry.state : null;
  // Vbtns: highlight the active one
  document.querySelectorAll('.vbtn[data-occ-key="' + occKey + '"]').forEach(b => {
    b.classList.toggle('active', state && b.dataset.action === state);
  });
  // Li styling
  document.querySelectorAll('.occ-item[data-occ-key="' + occKey + '"]').forEach(li => {
    li.classList.toggle('state-approved', state === 'approved');
    li.classList.toggle('state-rejected', state === 'rejected');
  });
  // Source tag styling
  document.querySelectorAll('.opinion-body [data-occ-key="' + occKey + '"]').forEach(el => {
    el.classList.toggle('state-approved', state === 'approved');
    el.classList.toggle('state-rejected', state === 'rejected');
  });
  // Correction textarea: show only when rejected; restore value
  document.querySelectorAll('.correction-input[data-occ-key="' + occKey + '"]').forEach(ta => {
    if (state === 'rejected') {
      ta.style.display = 'block';
      if (entry && typeof entry.correction === 'string') ta.value = entry.correction;
    } else {
      ta.style.display = 'none';
    }
  });
}

function updateCardReviewed(card) {
  const keys = (card.dataset.occKeys || '').split(',').filter(Boolean);
  card.classList.remove('all-approved', 'has-corrections');
  if (!keys.length) return;
  const state = loadReview();
  const reviewed = keys.map(k => state[k] && state[k].state).filter(Boolean);
  if (reviewed.length !== keys.length) return; // not all reviewed
  const anyRejected = reviewed.some(s => s === 'rejected');
  if (anyRejected) card.classList.add('has-corrections');
  else card.classList.add('all-approved');
}
function updateAllCards() {
  document.querySelectorAll('.case-card').forEach(updateCardReviewed);
}

function updateProgress() {
  const state = loadReview();
  const allOccs = new Set();
  document.querySelectorAll('.occ-item[data-occ-key]').forEach(li => allOccs.add(li.dataset.occKey));
  document.querySelectorAll('.rejected-card[data-occ-keys]').forEach(c => {
    (c.dataset.occKeys || '').split(',').filter(Boolean).forEach(k => allOccs.add(k));
  });
  const total = allOccs.size;
  let approved = 0, rejected = 0;
  allOccs.forEach(k => {
    if (state[k] && state[k].state === 'approved') approved++;
    else if (state[k] && state[k].state === 'rejected') rejected++;
  });
  const reviewed = approved + rejected;
  const cards = document.querySelectorAll('.case-card');
  const fullyReviewedCards = document.querySelectorAll('.case-card.all-approved, .case-card.has-corrections');
  const pct = total ? Math.round(100 * reviewed / total) : 0;
  const text = document.querySelector('.review-text');
  if (text) {
    text.innerHTML =
      'Reviewed: <strong>' + reviewed + '</strong> / ' + total + ' (' +
      '<span style="color:#1a5;">' + approved + ' approved</span>, ' +
      '<span style="color:#c33;">' + rejected + ' rejected</span>) · ' +
      '<strong>' + fullyReviewedCards.length + '</strong> / ' +
      cards.length + ' cards complete';
  }
  const fill = document.querySelector('.review-bar-fill');
  if (fill) fill.style.width = pct + '%';
}

document.addEventListener('DOMContentLoaded', () => {
  // Restore state on load.
  const state = loadReview();
  Object.keys(state).forEach(k => syncOccState(k, state[k]));
  updateAllCards();
  updateProgress();

  // Vbtn click → set/toggle state.
  document.querySelectorAll('.vbtn[data-occ-key]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const key = btn.dataset.occKey;
      const action = btn.dataset.action;  // 'approve' or 'reject'
      const s = loadReview();
      if (s[key] && s[key].state === action) {
        delete s[key];  // toggle off
      } else {
        const correction = (s[key] && s[key].correction) || '';
        s[key] = action === 'rejected' || action === 'reject'
          ? {state: 'rejected', correction}
          : {state: 'approved'};
        if (action === 'approve') s[key] = {state: 'approved'};
      }
      saveReview(s);
      syncOccState(key, s[key]);
      const card = btn.closest('.case-card');
      if (card) updateCardReviewed(card);
      updateProgress();
      applyFilters();
    });
  });

  // Correction textarea: save on blur.
  document.querySelectorAll('.correction-input').forEach(ta => {
    ta.addEventListener('blur', e => {
      const key = ta.dataset.occKey;
      const s = loadReview();
      if (s[key] && s[key].state === 'rejected') {
        s[key] = {state: 'rejected', correction: ta.value};
        saveReview(s);
      }
    });
    ta.addEventListener('click', e => e.stopPropagation());  // don't navigate on click
  });

  // Mark-all button: approve every occurrence in this case.
  document.querySelectorAll('.mark-all-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const card = btn.closest('.case-card');
      if (!card) return;
      const keys = (card.dataset.occKeys || '').split(',').filter(Boolean);
      const s = loadReview();
      const anyUnapproved = keys.some(k => !(s[k] && s[k].state === 'approved'));
      keys.forEach(k => {
        if (anyUnapproved) s[k] = {state: 'approved'};
        else delete s[k];
        syncOccState(k, s[k]);
      });
      saveReview(s);
      updateCardReviewed(card);
      updateProgress();
      applyFilters();
    });
  });

  // Export reviews → download JSON
  const exportBtn = document.getElementById('export-reviews');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      const payload = {
        cluster_id: CLUSTER_ID,
        exported_at: new Date().toISOString(),
        state: loadReview(),
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'reviews_' + CLUSTER_ID + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    });
  }

  // Import reviews ← read JSON
  const importBtn = document.getElementById('import-reviews');
  const importInput = document.getElementById('import-file-input');
  if (importBtn && importInput) {
    importBtn.addEventListener('click', () => importInput.click());
    importInput.addEventListener('change', () => {
      const file = importInput.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const payload = JSON.parse(reader.result);
          if (!payload.state) throw new Error('missing state field');
          if (payload.cluster_id && String(payload.cluster_id) !== CLUSTER_ID) {
            if (!confirm('File is for cluster ' + payload.cluster_id + ' but this page is ' +
                         CLUSTER_ID + '. Import anyway?')) return;
          }
          saveReview(payload.state);
          // Restore visuals
          document.querySelectorAll('.vbtn.active').forEach(b => b.classList.remove('active'));
          document.querySelectorAll('.state-approved, .state-rejected').forEach(el => {
            el.classList.remove('state-approved', 'state-rejected');
          });
          Object.keys(payload.state).forEach(k => syncOccState(k, payload.state[k]));
          updateAllCards();
          updateProgress();
          applyFilters();
          alert('Imported ' + Object.keys(payload.state).length + ' review entries.');
        } catch (err) {
          alert('Import failed: ' + err.message);
        }
        importInput.value = '';
      };
      reader.readAsText(file);
    });
  }
});

// ─ Navigation click handler (jump source / highlight peers) ─
document.addEventListener('click', function(e) {
  if (e.target.closest('.filter-btn, .filter-group-select, summary, .vbtn, .mark-all-btn, .correction-input, .review-toolbar')) return;

  const occ = e.target.closest('.occ-item[data-occ-key], .missing-item[data-occ-key]');
  if (occ && occ.dataset.occKey) {
    document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));
    occ.classList.add('highlight');
    const target = document.querySelector('.opinion-body [data-occ-key="' + occ.dataset.occKey + '"]');
    if (target) {
      target.classList.add('highlight');
      target.scrollIntoView({behavior: 'smooth', block: 'center'});
    }
    return;
  }

  const card_or_tag = e.target.closest('[data-case], [data-rejected]');
  if (!card_or_tag) return;
  document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));
  const clickedFromCard = !!card_or_tag.closest('.case-card');

  let firstSourceTag = null;
  if (card_or_tag.dataset.case !== undefined && card_or_tag.dataset.case !== '') {
    const caseIdx = card_or_tag.dataset.case;
    document.querySelectorAll('[data-case="' + caseIdx + '"]').forEach(el => el.classList.add('highlight'));
    firstSourceTag = document.querySelector('.opinion-body .cited[data-case="' + caseIdx + '"]');
  } else if (card_or_tag.dataset.rejected === '1') {
    const id = card_or_tag.dataset.id;
    document.querySelectorAll('.rejected-card[data-id="' + id + '"]').forEach(el => el.classList.add('highlight'));
    document.querySelectorAll('.cited.rejected[data-id="' + id + '"]').forEach(el => el.classList.add('highlight'));
    firstSourceTag = document.querySelector('.opinion-body .cited.rejected[data-id="' + id + '"]');
  }
  if (clickedFromCard && firstSourceTag) {
    firstSourceTag.scrollIntoView({behavior: 'smooth', block: 'center'});
  }
});

// ─ Four-dimensional filters: Kind, Review, Opinion, Group ─
const filters = {kind: 'all', review: 'all', opinion: 'all', group: 'all'};

function passesKind(card) {
  const type = card.dataset.cardType;
  const f = filters.kind;
  if (f === 'all') return true;
  if (f === 'changed') return card.dataset.changed === '1';
  if (f === 'rejected') return type === 'rejected';
  const key = 'flag' + f.charAt(0).toUpperCase() + f.slice(1);
  return type === 'case' && card.dataset[key] === '1';
}
function passesReview(card) {
  const f = filters.review;
  if (f === 'all') return true;
  if (f === 'unreviewed') {
    return !card.classList.contains('all-approved') && !card.classList.contains('has-corrections');
  }
  if (f === 'approved') return card.classList.contains('all-approved');
  if (f === 'rejected_state') return card.classList.contains('has-corrections');
  return true;
}
function passesOpinion(card) {
  if (filters.opinion === 'all') return true;
  return (card.dataset.opinionTypes || '').split(',').includes(filters.opinion);
}
function passesGroup(card) {
  if (filters.group === 'all') return true;
  return (card.dataset.groupIds || '').split(',').includes(filters.group);
}
function applyFilters() {
  document.querySelectorAll('.case-card').forEach(card => {
    card.classList.toggle('filtered-out',
      !(passesKind(card) && passesReview(card) && passesOpinion(card) && passesGroup(card)));
  });
  document.querySelectorAll('.filter-btn').forEach(b => {
    b.classList.toggle('active', filters[b.dataset.filterType] === b.dataset.filter);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.filter-btn').forEach(b => {
    b.addEventListener('click', () => {
      filters[b.dataset.filterType] = b.dataset.filter;
      applyFilters();
    });
  });
  const groupSel = document.querySelector('.filter-group-select');
  if (groupSel) {
    groupSel.addEventListener('change', () => {
      filters.group = groupSel.value;
      applyFilters();
    });
  }
  applyFilters();
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
        const av = a.cells[idx].innerText, bv = b.cells[idx].innerText;
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


def classify_case_diff(
    case: dict,
    id_to_metadata_map: dict,
    artifact_groups: dict[str, list[int]],
    pos_to_id: dict[tuple[int, int], int],
) -> tuple[set[str], dict]:
    """Classify a cited_case relative to Phase 1's eyecite groupings.

    `accepted_ids` is reconstructed from the case's tagged occurrences by
    mapping `(source_opinion_id, offset)` → Phase 1 id via `pos_to_id`.
    `untagged` is detected from the `occurrences` list's `source` field.

    Returns (flags, details) where flags is a set of:
      "confirmed" — accepted_ids exactly equals one Phase 1 group's ids
      "partial"   — accepted_ids ⊊ one Phase 1 group (some ids elsewhere)
      "merged"    — accepted_ids spans 2+ Phase 1 groups
      "added"     — has untagged_occurrences (citations eyecite missed)
      "ocr"       — ocr_corrected = true
      "fallback"  — case has null mainCit + null caseName (postprocess
                    fallback for ids the model omitted, or model emission
                    with no recognizable canonical form)
    A case with only "confirmed" is unchanged. Any other flag → changed.
    """
    occurrences = case.get("occurrences", [])
    accepted_ids: list[int] = []
    for o in occurrences:
        if o.get("source") != "tagged":
            continue
        key = (o["source_opinion_id"], o["offset"])
        if key in pos_to_id:
            accepted_ids.append(pos_to_id[key])
    has_untagged = any(o.get("source") == "untagged" for o in occurrences)

    flags: set[str] = set()
    details: dict = {}

    source_groups: set[str] = set()
    for id_ in accepted_ids:
        meta = id_to_metadata_map.get(str(id_))
        if meta:
            source_groups.add(meta["group_id"])
    details["source_groups"] = sorted(source_groups)

    if len(source_groups) > 1:
        flags.add("merged")
    elif len(source_groups) == 1:
        only_group = next(iter(source_groups))
        group_ids = set(artifact_groups.get(only_group, []))
        accepted_set = set(accepted_ids)
        if accepted_set == group_ids:
            flags.add("confirmed")
        else:
            flags.add("partial")
            details["group_id"] = only_group
            details["missing_ids"] = sorted(group_ids - accepted_set)
    # else: no tagged occurrences — pure untagged-added case.

    if has_untagged:
        flags.add("added")
    if case.get("ocr_corrected"):
        flags.add("ocr")
    if case.get("mainCitationString") is None and case.get("caseName") is None:
        flags.add("fallback")

    if not flags:
        flags.add("empty")

    return flags, details


def render_case_card(
    case_idx: int, case: dict, flags: set[str], details: dict,
    *,
    id_to_case: dict[int, int] | None = None,
    all_cases: list[dict] | None = None,
    id_to_metadata_map: dict | None = None,
    pos_to_id: dict[tuple[int, int], int] | None = None,
) -> str:
    color = _case_color(case_idx)
    occurrences = case.get("occurrences", [])
    occ_keys: list[str] = []
    occ_items = []
    for o in occurrences:
        pill_class = o.get("source", "tagged")
        occ_key = f'{o["source_opinion_id"]}-{o["offset"]}'
        occ_keys.append(occ_key)
        # Phase 1 id + group_id (for tagged occurrences); "untagged" for ones
        # the model added.
        if pill_class == "untagged":
            id_label = '<span class="id-label untagged-id">untagged</span>'
        else:
            phase1_id = (pos_to_id or {}).get((o["source_opinion_id"], o["offset"]))
            if phase1_id is not None:
                gid = id_to_metadata_map.get(str(phase1_id), {}).get("group_id", "?") if id_to_metadata_map else "?"
                id_label = (f'<span class="id-label">id <code>{phase1_id}</code> · '
                            f'<code>{gid}</code></span>')
            else:
                id_label = '<span class="id-label">id ?</span>'
        occ_items.append(
            f'<li class="occ-item" data-occ-key="{occ_key}" title="Click row to jump; click ✓ to approve, ✗ to flag with correction">'
            f'<span class="vbtn vbtn-approve" data-action="approve" data-occ-key="{occ_key}" title="Approve">✓</span>'
            f'<span class="vbtn vbtn-reject" data-action="reject" data-occ-key="{occ_key}" title="Reject with correction">✗</span>'
            f'<span class="pill {pill_class}">{pill_class}</span> '
            f'{id_label} · '
            f'<code>{html.escape(o["opinion_type"])}</code> · '
            f'{html.escape(repr(o["citation_string"][:60]))}'
            f'<textarea class="correction-input" data-occ-key="{occ_key}" '
            f'placeholder="What should this occurrence be? (correction / note)" '
            f'style="display:none;"></textarea>'
            f'</li>'
        )

    ocr_html = ""
    if case.get("ocr_corrected"):
        note = case.get("ocr_note") or ""
        ocr_html = f'<div class="ocr">OCR corrected: {html.escape(note)}</div>'

    main_cit = case.get("mainCitationString") or "<null>"
    case_name = case.get("caseName") or "<null>"
    parallel = case.get("parallelCitationString")
    parallel_html = (
        f'<div class="case-meta parallel">parallel: {html.escape(parallel)}</div>'
        if parallel else ''
    )

    # Collapsed occurrence list to keep cards compact in the narrow left pane.
    # Auto-expand small cases (≤3 occurrences) for at-a-glance readability.
    open_attr = "open" if len(occurrences) <= 3 else ""
    occ_html = (
        f'<details {open_attr}>'
        f'<summary>{len(occurrences)} occurrence(s)</summary>'
        f'<ul class="occ-list">{"".join(occ_items)}</ul>'
        f'</details>'
    )

    # Diff badges
    _BADGE_LABELS = {
        "confirmed": ("CONFIRMED", "#dff5dd", "#1a5"),
        "partial":   ("PARTIAL/SPLIT", "#fff0cc", "#960"),
        "merged":    ("MERGED", "#d6e8ff", "#159"),
        "added":     ("+UNTAGGED", "#f5d8ff", "#705"),
        "ocr":       ("OCR FIX", "#ffdacc", "#a40"),
        "casename":  ("CASENAME WARN", "#fde4d4", "#a31"),
        "fallback":  ("FALLBACK / NULL NAME", "#e6e6e6", "#555"),
        "empty":     ("EMPTY", "#eee", "#666"),
    }
    badges_html = "".join(
        f'<span class="badge-flag" style="background:{bg};color:{fg};">{label}</span>'
        for flag in ("confirmed", "partial", "merged", "added", "ocr", "casename", "fallback", "empty")
        if flag in flags
        for label, bg, fg in [_BADGE_LABELS[flag]]
    )

    # Details about the diff (e.g., which groups merged, which ids are missing)
    diff_html = ""
    if "merged" in flags:
        gs = ", ".join(details.get("source_groups", []))
        diff_html += f'<div class="case-meta diff">merged from: <code>{gs}</code></div>'
    if "partial" in flags:
        gid = details.get("group_id", "?")
        missing = details.get("missing_ids", [])
        # For each missing id: which other case got it? Under the new
        # postprocess every id is routed (either by the model or by Phase
        # 1 fallback), so "missing" only means "in a sibling case," never
        # truly absent. Render as clickable items.
        missing_items = []
        for mid in missing:
            meta = (id_to_metadata_map or {}).get(str(mid), {})
            occ_key = (f'{meta["source_opinion_id"]}-{meta["offset"]}'
                       if meta else "")
            dest_case = (id_to_case or {}).get(mid)
            if dest_case is not None and all_cases and dest_case < len(all_cases):
                dest = all_cases[dest_case]
                dest_label = (dest.get("caseName")
                              or dest.get("mainCitationString") or "<null-name>")
                dest_html = (f'→ case <code>#{dest_case}</code> '
                             f'({html.escape(dest_label[:60])})')
            else:
                dest_html = '→ <strong style="color:#a40;">unrouted</strong>'
            cite = meta.get("citation_string", "?")
            missing_items.append(
                f'<li class="occ-item missing-item" data-occ-key="{occ_key}" '
                f'title="Click to jump to this id in the source overlay">'
                f'id <code>{mid}</code> '
                f'<code>{html.escape(cite[:40])}</code> {dest_html}</li>'
            )
        diff_html += (f'<div class="case-meta diff">partial of <code>{gid}</code>; '
                      f'{len(missing)} id(s) elsewhere:'
                      f'<ul class="missing-list">{"".join(missing_items)}</ul></div>')
    if "casename" in flags:
        warns = details.get("casename_warnings", [])
        warning_msgs = "".join(
            f'<li>{html.escape(w.get("message", w.get("reason", "(no message)")))}</li>'
            for w in warns
        )
        diff_html += (f'<div class="case-meta diff" style="background:#fde4d4;">'
                      f'<strong>caseName warning:</strong>'
                      f'<ul style="margin:0.2em 0 0 1em; padding-left:0.5em;">'
                      f'{warning_msgs}</ul></div>')

    # Data attrs for JS filtering
    flag_attrs = " ".join(f'data-flag-{f}="1"' for f in flags)
    is_changed = "1" if flags - {"confirmed"} else "0"

    opinion_types = sorted({o["opinion_type"] for o in occurrences})
    # Collect the Phase 1 group_ids this case touches so the group filter can target it.
    case_group_ids: set[str] = set()
    if pos_to_id and id_to_metadata_map:
        for o in occurrences:
            if o.get("source") != "tagged":
                continue
            phase1_id = pos_to_id.get((o["source_opinion_id"], o["offset"]))
            if phase1_id is not None:
                meta = id_to_metadata_map.get(str(phase1_id))
                if meta:
                    case_group_ids.add(meta["group_id"])
    return (
        f'<div class="case-card" data-card-type="case" data-case="{case_idx}" '
        f'data-changed="{is_changed}" {flag_attrs} '
        f'data-opinion-types="{",".join(opinion_types)}" '
        f'data-group-ids="{",".join(sorted(case_group_ids))}" '
        f'data-occ-keys="{",".join(occ_keys)}" '
        f'style="--case-color:{color};">'
        f'<div class="badges">{badges_html}'
        f'<span class="reviewed-badge">✓ ALL APPROVED</span>'
        f'<span class="corrections-badge">⚠ HAS CORRECTIONS</span>'
        f'</div>'
        f'<h4>#{case_idx} — {html.escape(case_name)} '
        f'<button class="mark-all-btn" title="Mark every occurrence in this case reviewed">mark all</button>'
        f'</h4>'
        f'<div class="case-meta">main: <code>{html.escape(main_cit)}</code></div>'
        f'{parallel_html}'
        f'{ocr_html}'
        f'{diff_html}'
        f'{occ_html}'
        f'</div>'
    )


def render_audit_panel(cluster_id: int) -> str:
    """Render the audit log entries for this cluster, in collapsible sections."""
    sections = [
        ("Untagged occurrences (accepted + dropped)", UNTAGGED_LOG),
        ("Unrouted ids (model omitted; restored via Phase 1 fallback)", UNROUTED_LOG),
        ("Id conflicts (resolved by occurrence-count winner rule)", ID_CONFLICTS_LOG),
        ("Invalid (fabricated) ids", INVALID_LOG),
        ("caseName warnings", CASENAME_WARN_LOG),
    ]
    parts = ['<div class="audit"><h3>Audit logs (this cluster)</h3>']
    any_content = False
    for title, path in sections:
        rows = _filter_to_cluster(_load_jsonl(path), cluster_id)
        if not rows:
            parts.append(
                f'<details><summary>{title} (0)</summary>'
                f'<p style="color:#888;">none</p></details>'
            )
            continue
        any_content = True
        # Pretty-print each row as JSON
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        parts.append(
            f'<details {"open" if len(rows) < 20 else ""}>'
            f'<summary>{title} ({len(rows)})</summary>'
            f'<pre>{html.escape(body)}</pre></details>'
        )
    parts.append('</div>')
    if not any_content:
        # Nothing in any audit log: show a one-line "clean" note instead.
        return '<div class="audit"><h3>Audit logs (this cluster)</h3><p style="color:#666;">All clean — no audit entries.</p></div>'
    return "".join(parts)


def render_cluster_page(cluster_id: int, grouped: dict, tagged: dict) -> str:
    cases = grouped.get("cited_cases", [])

    # id → case_idx map for the source overlay
    id_to_case: dict[int, int] = {}
    for ci, case in enumerate(cases):
        for o in case.get("occurrences", []):
            if o.get("source") == "tagged":
                # We need the Phase 1 id of this occurrence — look it up by
                # (source_opinion_id, offset) in the tagged artifact's
                # id_to_metadata_map.
                pass
    # Build (source_opinion_id, offset) → id from the Phase 1 artifact
    pos_to_id: dict[tuple[int, int], int] = {}
    for id_str, meta in tagged.get("id_to_metadata_map", {}).items():
        pos_to_id[(meta["source_opinion_id"], meta["offset"])] = int(id_str)
    for ci, case in enumerate(cases):
        for o in case.get("occurrences", []):
            if o.get("source") == "tagged":
                key = (o["source_opinion_id"], o["offset"])
                if key in pos_to_id:
                    id_to_case[pos_to_id[key]] = ci

    # Untagged occurrences grouped by source_opinion_id with their case_idx
    untagged_by_opinion: dict[int, list[dict]] = defaultdict(list)
    for ci, case in enumerate(cases):
        for o in case.get("occurrences", []):
            if o.get("source") == "untagged":
                untagged_by_opinion[o["source_opinion_id"]].append({
                    "offset": o["offset"],
                    "snippet": o["citation_string"],
                    "case_idx": ci,
                })

    # TOC + summary
    parts: list[str] = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        f'<title>Grouped — cluster {cluster_id}</title>',
        '<link rel="stylesheet" href="style.css">',
        '</head><body>',
        '<nav class="toc"><a href="index.html">← Index</a> | '
        '<a href="#cases">Cases</a> | <a href="#source">Source overlay</a> '
        '| <a href="#audit">Audit</a></nav>',
        f'<h1>Grouped output — cluster {cluster_id}</h1>',
        '<div class="summary"><dl>',
        f'<dt>Cases (model-grouped)</dt><dd>{grouped.get("n_cases", 0):,}</dd>',
        f'<dt>Occurrences (tagged + untagged)</dt><dd>{grouped.get("n_occurrences", 0):,}</dd>',
        f'<dt>Untagged accepted / dropped</dt>'
        f'<dd>+{grouped.get("n_untagged_accepted", 0)} / '
        f'-{grouped.get("n_untagged_dropped", 0)}</dd>',
        f'<dt>Unrouted ids (model omitted; fallback applied)</dt>'
        f'<dd>{grouped.get("n_unrouted_ids", 0)}</dd>',
        f'<dt>Id conflicts resolved</dt>'
        f'<dd>{grouped.get("n_id_conflicts", 0)}</dd>',
        f'<dt>Invalid (fabricated) ids</dt><dd>{grouped.get("n_invalid_ids", 0)}</dd>',
        f'<dt>caseName warnings</dt><dd>{grouped.get("n_casename_warnings", 0)}</dd>',
        f'<dt>Phase 1 tag count (for reference)</dt><dd>{tagged.get("n_tags", 0):,}</dd>',
        '</dl></div>',
    ]

    # Two-column layout: cases on the left, source overlay on the right.
    parts.append('<div class="layout">')

    # Classify each case's diff vs Phase 1.
    id_to_metadata_map = tagged.get("id_to_metadata_map", {})
    artifact_groups = tagged.get("groups", {})
    case_flags: list[tuple[set[str], dict]] = [
        classify_case_diff(c, id_to_metadata_map, artifact_groups, pos_to_id)
        for c in cases
    ]

    # Attach caseName warnings to cases by normalized-mainCit match.
    # Each warning may carry either `mainCitationString` (singular, new format)
    # OR `mainCitationStrings` (plural list, old format pre-2026-05-28); index
    # by every one we see so a case's mainCit can match.
    casename_warnings = _filter_to_cluster(_load_jsonl(CASENAME_WARN_LOG), cluster_id)
    warnings_by_maincit: dict[str, list[dict]] = defaultdict(list)
    for w in casename_warnings:
        keys: list[str] = []
        if w.get("mainCitationString"):
            keys.append(_normalize_canonical(w["mainCitationString"]))
        for s in w.get("mainCitationStrings", []) or []:
            keys.append(_normalize_canonical(s))
        for k in keys:
            if k:
                warnings_by_maincit[k].append(w)
    case_warnings: list[list[dict]] = []
    for c in cases:
        key = _normalize_canonical(c.get("mainCitationString"))
        case_warnings.append(warnings_by_maincit.get(key, []))
    for i, ws in enumerate(case_warnings):
        if ws:
            case_flags[i][0].add("casename")
            case_flags[i][1]["casename_warnings"] = ws
    diff_counts = {k: 0 for k in ("confirmed", "partial", "merged", "added", "ocr", "casename", "fallback")}
    for flags, _ in case_flags:
        for f in flags:
            if f in diff_counts:
                diff_counts[f] += 1
    n_changed = sum(1 for flags, _ in case_flags if flags - {"confirmed"})

    # Under the new postprocess, every Phase 1 id is routed (model or Phase 1
    # fallback), so there are no "rejected" ids. Counts come from the
    # postprocess output directly.
    n_unrouted = grouped.get("n_unrouted_ids", 0)
    n_id_conflicts = grouped.get("n_id_conflicts", 0)
    n_fallback_cases = sum(
        1 for c in cases
        if c.get("mainCitationString") is None and c.get("caseName") is None
    )

    parts.append(f'<div class="left-pane" data-cluster-id="{cluster_id}">')
    parts.append(
        f'<h2 id="cases">Cases '
        f'<span class="meta">({len(cases)} cases · '
        f'<strong>{n_changed} changed</strong> from eyecite · '
        f'{n_fallback_cases} fallback / null-name)</span></h2>'
    )
    parts.append(
        '<div class="review-progress">'
        '<span class="review-text">Reviewed: <strong>0</strong> / 0 occurrences · '
        '<strong>0</strong> / 0 cards fully reviewed</span>'
        '<div class="review-bar"><div class="review-bar-fill" style="width:0%"></div></div>'
        '</div>'
    )
    parts.append('<div class="diff-summary">')
    parts.append(
        f'Confirmed: <strong>{diff_counts["confirmed"]}</strong> · '
        f'Partial/Split: <strong>{diff_counts["partial"]}</strong> · '
        f'Merged: <strong>{diff_counts["merged"]}</strong> · '
        f'+Untagged: <strong>{diff_counts["added"]}</strong> · '
        f'OCR: <strong>{diff_counts["ocr"]}</strong> · '
        f'caseName warn: <strong>{diff_counts["casename"]}</strong> · '
        f'Unrouted (model omitted): <strong>{n_unrouted}</strong> · '
        f'Id conflicts: <strong>{n_id_conflicts}</strong>'
    )
    parts.append('</div>')
    # Build the group_id → label map for the group filter dropdown.
    # Each group is labeled with the case(s) it appears in, or "split" if it
    # spans multiple cases.
    group_to_cases: dict[str, list[int]] = defaultdict(list)
    for ci, case in enumerate(cases):
        case_groups: set[str] = set()
        for o in case.get("occurrences", []):
            if o.get("source") != "tagged":
                continue
            phase1_id = pos_to_id.get((o["source_opinion_id"], o["offset"]))
            if phase1_id is not None:
                meta = id_to_metadata_map.get(str(phase1_id))
                if meta:
                    case_groups.add(meta["group_id"])
        for gid in case_groups:
            group_to_cases[gid].append(ci)

    all_groups: set[str] = set(artifact_groups.keys())
    def _label_group(gid: str) -> str:
        case_idxs = group_to_cases.get(gid, [])
        if len(case_idxs) == 0:
            return f"{gid} — (no case)"
        if len(case_idxs) == 1:
            c = cases[case_idxs[0]]
            name = c.get("caseName") or c.get("mainCitationString") or "<null-name>"
            return f"{gid} — {name[:35]}"
        return f"{gid} — split ({len(case_idxs)} cases)"

    def _group_sort_key(gid: str) -> int:
        try:
            return int(gid.lstrip("g"))
        except ValueError:
            return 9_999_999

    sorted_groups = sorted(all_groups, key=_group_sort_key)

    # Review toolbar (export/import + storage note)
    parts.append('<div class="review-toolbar">')
    parts.append('<button id="export-reviews" title="Download review state as JSON">Export reviews</button>')
    parts.append('<button id="import-reviews" title="Restore review state from a JSON file">Import reviews</button>')
    parts.append('<input id="import-file-input" type="file" accept="application/json" style="display:none;">')
    parts.append('<span class="storage-note">Reviews saved to browser localStorage only — export to persist.</span>')
    parts.append('</div>')

    # Filter rows: Kind / Review / Opinion / Group (compact labeled grid)
    present_opinion_types: set[str] = set()
    for c in cases:
        for o in c.get("occurrences", []):
            present_opinion_types.add(o["opinion_type"])

    parts.append('<div class="filters">')

    def _btn_row(label: str, ftype: str, options: list[tuple[str, str]]) -> str:
        btns = "".join(
            f'<button class="filter-btn" data-filter-type="{ftype}" '
            f'data-filter="{v}">{lbl}</button>'
            for lbl, v in options
        )
        return (f'<div class="filter-label">{label}</div>'
                f'<div class="filter-buttons">{btns}</div>')

    parts.append(_btn_row("Kind:", "kind", [
        ("All", "all"), ("Changed", "changed"),
        ("Partial/Split", "partial"), ("Merged", "merged"),
        ("+Untagged", "added"), ("OCR", "ocr"),
        ("caseName", "casename"), ("Fallback / null-name", "fallback"),
    ]))
    parts.append(_btn_row("Review:", "review", [
        ("All", "all"), ("Unreviewed", "unreviewed"),
        ("Has corrections", "rejected_state"),
        ("All approved", "approved"),
    ]))
    if present_opinion_types:
        opinion_opts = [("All", "all")] + [(t, t) for t in sorted(present_opinion_types)]
        parts.append(_btn_row("Opinion:", "opinion", opinion_opts))

    if sorted_groups:
        opts_html = '<option value="all">All groups</option>' + "".join(
            f'<option value="{gid}">{html.escape(_label_group(gid))}</option>'
            for gid in sorted_groups
        )
        parts.append(
            f'<div class="filter-label">Group:</div>'
            f'<div class="filter-buttons"><select class="filter-group-select" '
            f'data-filter-type="group">{opts_html}</select></div>'
        )

    parts.append('</div>')

    for ci, case in enumerate(cases):
        flags, details = case_flags[ci]
        parts.append(render_case_card(
            ci, case, flags, details,
            id_to_case=id_to_case, all_cases=cases,
            id_to_metadata_map=id_to_metadata_map, pos_to_id=pos_to_id,
        ))
    parts.append('</div>')

    parts.append('<div class="right-pane">')
    parts.append(f'<h2 id="source">Source overlay <span class="meta">'
                 f'(Phase 1 text, recolored by case assignment)</span></h2>')
    for i, op in enumerate(tagged.get("opinions", [])):
        soid = op["source_opinion_id"]
        op_type = op["opinion_type"]
        text = op["tagged_text"]
        n_tags_op = text.count('<cited id=')
        parts.append(
            f'<h3>{op_type} <span class="meta">'
            f'(opinion id {soid} · {len(text):,} chars · '
            f'{n_tags_op:,} tags)</span></h3>'
        )
        overlay = render_overlay(
            text, id_to_case,
            untagged_by_opinion.get(soid, []),
            source_opinion_id=soid,
            id_to_metadata_map=id_to_metadata_map,
            chunk_start=0,
        )
        parts.append(f'<div class="opinion-body">{overlay}</div>')
    parts.append('</div>')

    parts.append('</div>')  # /layout

    parts.append('<h2 id="audit">Audit</h2>')
    parts.append(render_audit_panel(cluster_id))

    parts.append('<script src="script.js"></script>')
    parts.append('</body></html>')
    return "".join(parts)


def render_index_page(summaries: list[dict]) -> str:
    rows = "".join(
        f'<tr>'
        f'<td><a href="{s["cluster_id"]}.html">{s["cluster_id"]}</a></td>'
        f'<td>{s["n_cases"]}</td>'
        f'<td>{s["n_occurrences"]:,}</td>'
        f'<td>{s["n_untagged_accepted"]}/{s["n_untagged_dropped"]}</td>'
        f'<td>{s["n_unrouted_ids"]}</td>'
        f'<td>{s["n_id_conflicts"]}</td>'
        f'<td>{s["n_invalid_ids"]}</td>'
        f'<td>{s["n_casename_warnings"]}</td>'
        f'</tr>'
        for s in summaries
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Phase 2 grouped outputs — index</title>'
        '<link rel="stylesheet" href="style.css">'
        '</head><body>'
        f'<h1>Phase 2 grouped outputs ({len(summaries):,} clusters)</h1>'
        '<p>Click a column header to sort. Click a cluster id to open.</p>'
        '<table><thead><tr>'
        '<th>cluster_id</th><th>n_cases</th><th>n_occurrences</th>'
        '<th>untagged ±</th><th>unrouted</th><th>id conflicts</th>'
        '<th>invalid</th><th>caseName warn</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        '<script src="index.js"></script></body></html>'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--clusters", nargs="*", type=int, default=None,
        help="Specific cluster ids (default: all in data/grouped/).",
    )
    args = ap.parse_args()

    if not GROUPED_DIR.is_dir():
        raise SystemExit(f"ERROR: {GROUPED_DIR} not found. Run run_phase2.py first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    (OUT_DIR / "style.css").write_text(_STYLE_CSS)
    (OUT_DIR / "script.js").write_text(_CLUSTER_JS)
    (OUT_DIR / "index.js").write_text(_INDEX_JS)

    paths = (
        [GROUPED_DIR / f"{c}.json" for c in args.clusters]
        if args.clusters
        else sorted(GROUPED_DIR.glob("*.json"), key=lambda p: int(p.stem))
    )

    summaries: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"  SKIP — {p} not found")
            continue
        grouped = json.loads(p.read_text())
        cluster_id = grouped["cluster_id"]
        tagged_path = TAGGED_DIR / f"{cluster_id}.json"
        if not tagged_path.exists():
            print(f"  {cluster_id}: SKIP — no Phase 1 artifact at {tagged_path}")
            continue
        tagged = json.loads(tagged_path.read_text())
        (OUT_DIR / f"{cluster_id}.html").write_text(
            render_cluster_page(cluster_id, grouped, tagged)
        )
        summaries.append({
            "cluster_id": cluster_id,
            "n_cases": grouped.get("n_cases", 0),
            "n_occurrences": grouped.get("n_occurrences", 0),
            "n_untagged_accepted": grouped.get("n_untagged_accepted", 0),
            "n_untagged_dropped": grouped.get("n_untagged_dropped", 0),
            "n_unrouted_ids": grouped.get("n_unrouted_ids", 0),
            "n_id_conflicts": grouped.get("n_id_conflicts", 0),
            "n_invalid_ids": grouped.get("n_invalid_ids", 0),
            "n_casename_warnings": grouped.get("n_casename_warnings", 0),
        })
        print(f"  {cluster_id}: {grouped.get('n_cases', 0)} cases, "
              f"{grouped.get('n_occurrences', 0)} occurrences → {cluster_id}.html")

    (OUT_DIR / "index.html").write_text(render_index_page(summaries))

    print()
    print(f"Wrote {len(summaries):,} cluster pages + index → {OUT_DIR}")
    print(f"Open: file://{(OUT_DIR / 'index.html').resolve()}")


if __name__ == "__main__":
    main()
