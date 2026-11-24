"use strict";

// Comparison rules (cleanEntry, sameEntry, suggestLevel, lcsPairs, asText,
// historyText, settleWithEval, withSplit, FLAG_FIELDS, LEVELS) come from
// tiers.js, loaded before this file.

const PAGE_COUNT = Number(document.body.dataset.pageCount);
const FIRST_LIST_PAGE = Number(document.body.dataset.firstListPage);
const ZOOM_STEPS = [1, 1.25, 1.5, 2, 2.5, 3, 4];
const STORE_KEY = "encyclopedia-viewer";
// Later prompt versions reuse this eval set for everything but the history split.
const BASE_EVAL = "v1";
const TEXT_FIELDS = [["court", "Court"], ["history", "History"], ["topics", "Topics"]];
const LEVEL_LABEL = { same: "same", cosmetic: "cosmetic", field: "wrong field", ocr: "OCR error", hallucination: "hallucination" };
const LEVEL_BADGE = { same: "badge-green", cosmetic: "badge-gray", field: "badge-violet", ocr: "badge-amber", hallucination: "badge-red" };

const state = {
  page: FIRST_LIST_PAGE,
  zoom: 1,
  engines: [],          // [{name, pages: Set}]
  shown: [],            // engine names currently shown
  onlyOutput: false,
  sources: [],          // scan versions available
  source: "original",
  onlyDiffs: false,
  evalVersion: null,    // prompt version whose eval progress is loaded
  evalPages: {},        // page -> {complete, open, total} for that version
  view: null,           // what the output panel currently shows
};

const el = (id) => document.getElementById(id);

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, label, key) {
  const node = make("button", className, label);
  node.type = "button";
  if (key) node.append(" ", make("kbd", "", key));
  return node;
}

function toast(message) {
  const box = el("toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove("show"), 3200);
}

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    if (Number.isInteger(saved.page)) state.page = saved.page;
    if (ZOOM_STEPS.includes(saved.zoom)) state.zoom = saved.zoom;
    if (Array.isArray(saved.shown)) state.shown = saved.shown;
    state.onlyOutput = Boolean(saved.onlyOutput);
    if (typeof saved.source === "string") state.source = saved.source;
    state.onlyDiffs = Boolean(saved.onlyDiffs);
  } catch (err) { /* storage unavailable: defaults apply */ }
  const params = new URLSearchParams(location.search);
  const fromUrl = Number(params.get("page"));
  if (Number.isInteger(fromUrl) && fromUrl >= 1) state.page = fromUrl;
  if (params.get("engines")) state.shown = params.get("engines").split(",");
  state.page = Math.min(Math.max(state.page, 1), PAGE_COUNT);
}

function savePrefs() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      page: state.page, zoom: state.zoom, shown: state.shown,
      onlyOutput: state.onlyOutput, source: state.source, onlyDiffs: state.onlyDiffs,
    }));
  } catch (err) { /* ignore */ }
}

// Engine folders are named <model>__<prompt version>__<scan>.
function shortName(engine) {
  const [model, version] = engine.split("__");
  return version && version !== "v1" ? `${model} ${version}` : model;
}

function promptVersion(engine) {
  return engine.split("__")[1] || "v1";
}

/* ---------- header controls: engines, scan version, page list, zoom ---------- */

function renderEngineButtons() {
  const box = el("engines");
  box.replaceChildren();
  state.engines.forEach((engine) => {
    const node = make("button", "", engine.name);
    node.type = "button";
    node.setAttribute("aria-pressed", String(state.shown.includes(engine.name)));
    node.append(make("span", "count", `${engine.pages.size} pp`));
    node.addEventListener("click", () => toggleEngine(engine.name));
    box.append(node);
  });
  el("engine-hint").textContent = state.engines.length
    ? "Show two engines of the same prompt version to build its eval set."
    : "No outputs yet. Add outputs/<engine>/page_NNNN.json files.";
}

function toggleEngine(name) {
  state.shown = state.shown.includes(name)
    ? state.shown.filter((n) => n !== name)
    : [...state.shown, name];
  savePrefs();
  renderEngineButtons();
  loadOutputs();
}

function renderSourceButtons() {
  const box = el("sources");
  box.replaceChildren();
  if (state.sources.length < 2) return;
  state.sources.forEach((name) => {
    const node = make("button", "", name);
    node.type = "button";
    node.setAttribute("aria-pressed", String(name === state.source));
    node.addEventListener("click", () => {
      state.source = name;
      savePrefs();
      renderSourceButtons();
      showScan();
    });
    box.append(node);
  });
}

function showScan() {
  const scan = el("scan");
  scan.alt = `Scan of page ${state.page} (${state.source})`;
  scan.src = `/page/${state.source}/${state.page}`;
}

function renderRail() {
  const rail = el("rail");
  const withOutput = new Set();
  state.engines.forEach((engine) => engine.pages.forEach((p) => withOutput.add(p)));
  rail.replaceChildren();
  for (let page = 1; page <= PAGE_COUNT; page += 1) {
    if (state.onlyOutput && !withOutput.has(page) && page !== state.page) continue;
    const item = make("li");
    const node = make("button", page < FIRST_LIST_PAGE ? "front" : "", `p. ${page}`);
    node.type = "button";
    node.dataset.page = String(page);
    if (page === state.page) node.setAttribute("aria-current", "true");
    const review = state.evalPages[page];
    if (review) {
      const mark = review.complete
        ? make("span", "rail-mark done", "✓")
        : make("span", "rail-mark todo", String(review.open));
      mark.title = review.complete
        ? "Eval set complete for this page"
        : `${review.open} entries still to decide`;
      node.append(mark);
    } else if (withOutput.has(page)) {
      const dots = make("span", "rail-mark dots", "●");
      dots.title = "Has model output; not reviewed yet";
      node.append(dots);
    }
    item.append(node);
    rail.append(item);
  }
  const current = rail.querySelector('[aria-current="true"]');
  if (current) current.scrollIntoView({ block: "center" });
}

function applyZoom() {
  el("scan").style.width = `${state.zoom * 100}%`;
  el("zoom-level").textContent = `${Math.round(state.zoom * 100)}%`;
}

function stepZoom(direction) {
  const index = ZOOM_STEPS.indexOf(state.zoom) + direction;
  if (index < 0 || index >= ZOOM_STEPS.length) return;
  state.zoom = ZOOM_STEPS[index];
  applyZoom();
  savePrefs();
}

/* ---------- lining entries up and comparing them ---------- */

function entryKey(entry) {
  const cite = Array.isArray(entry.citations) && entry.citations.length ? entry.citations[0] : "";
  return `${entry.name || ""}|${cite}`.toLowerCase().replace(/[^a-z0-9|]/g, "");
}

// Line the engines' entries up row by row. Each row holds one entry per
// engine (or null where an engine has no counterpart).
function alignEntries(lists) {
  let rows = lists[0].map((entry) => [entry]);
  for (let k = 1; k < lists.length; k += 1) {
    const next = lists[k];
    const rowKeys = rows.map((r) => entryKey(r.find(Boolean)));
    const pairs = lcsPairs(rowKeys, next.map(entryKey));
    const merged = [];
    let i = 0, j = 0;
    const pad = (r) => { while (r.length < k) r.push(null); return r; };
    const flush = (toI, toJ) => {
      // Unmatched stretches pair up in order, so near-miss names still share a row.
      while (i < toI && j < toJ) { merged.push([...pad(rows[i]), next[j]]); i += 1; j += 1; }
      while (i < toI) { merged.push([...pad(rows[i]), null]); i += 1; }
      while (j < toJ) { merged.push([...pad([]), next[j]]); j += 1; }
    };
    pairs.forEach(([pi, pj]) => {
      flush(pi, pj);
      merged.push([...pad(rows[i]), next[j]]); i += 1; j += 1;
    });
    flush(rows.length, next.length);
    rows = merged;
  }
  return rows;
}

/* ---------- the eval set for the current page ---------- */

// A row needs no review when the two models agree, or differ only in
// punctuation or spacing (scoring ignores those). The first model's
// wording is kept for a punctuation-only row.
function autoRow(pair, base) {
  if (base) {
    // The earlier eval set settles the text; only the split can stay open.
    const entry = settleWithEval(pair, base);
    if (!entry) return { status: "open", source: null, entry: null, judgments: {} };
    const source = sameEntry(pair[0], pair[1]) ? "both" : "eval";
    return { status: "auto", source, entry, judgments: {} };
  }
  if (sameEntry(pair[0], pair[1])) {
    return { status: "auto", source: "both", entry: cleanEntry(pair[0]), judgments: {} };
  }
  if (pair[0] && pair[1] && suggestLevel(pair[0], pair[1]) === "cosmetic") {
    return { status: "auto", source: "punctuation", entry: cleanEntry(pair[0]), judgments: {} };
  }
  return { status: "open", source: null, entry: null, judgments: {} };
}

// Work out each row's state: taken from the saved file where the reviewer
// already decided, otherwise accepted automatically or left open.
function buildEvalRows(aligned, saved, base) {
  return aligned.map((pair, index) => {
    const kept = saved && saved.rows[index];
    if (kept && (kept.status === "picked" || kept.status === "edited")) {
      return { status: kept.status, source: kept.source, entry: kept.entry, judgments: kept.judgments || {} };
    }
    return autoRow(pair, base && base[index]);
  });
}

// Refresh the suggested level for each model, keeping the reviewer's overrides.
function judgeRow(row, pair, engines) {
  if (row.status === "open") { row.judgments = {}; return; }
  const next = {};
  engines.forEach((engine, k) => {
    const suggested = suggestLevel(pair[k], row.entry);
    const before = row.judgments[engine];
    const overridden = before && LEVELS.includes(before.level) && before.level !== before.suggested
      && before.suggested === suggested && suggested !== "same";
    next[engine] = { suggested, level: overridden ? before.level : suggested };
  });
  row.judgments = next;
}

function evalSummary(view) {
  const open = view.rows.filter((r) => r.status === "open").length;
  return { open, total: view.rows.length, complete: open === 0 };
}

async function saveEval() {
  const view = state.view;
  if (!view || !view.evalMode || view.blocked) return;
  const page = view.page;
  try {
    const response = await fetch(`/api/eval/${view.version}/${page}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engines: view.engines, rows: view.rows }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    if (state.evalVersion === view.version) state.evalPages[page] = body;
    renderRail();
  } catch (err) {
    toast(`Eval set for page ${page} was not saved: ${err.message}`);
  }
}

// Load review progress for one prompt version (shown in the page list).
async function loadEvalSummary(version) {
  if (state.evalVersion === version) return;
  state.evalVersion = version;
  state.evalPages = {};
  if (version) {
    try {
      const response = await fetch(`/api/eval/${version}`);
      if (response.ok) state.evalPages = (await response.json()).pages;
    } catch (err) { /* the page list just shows no progress */ }
  }
  renderRail();
}

function firstOpenRow(view, after) {
  const count = view.rows.length;
  for (let step = 1; step <= count; step += 1) {
    const index = (after + step + count) % count;
    if (view.rows[index].status === "open") return index;
  }
  return -1;
}

function decide(index, change) {
  const view = state.view;
  const row = view.rows[index];
  Object.assign(row, change);
  judgeRow(row, view.aligned[index], view.engines);
  view.editing = -1;
  if (row.status !== "open") {
    const next = firstOpenRow(view, index);
    view.active = next === -1 ? index : next;
  } else view.active = index;
  renderCompare(true);
  saveEval();
}

function acceptModel(index, k) {
  const view = state.view;
  const entry = view.aligned[index][k];
  if (!entry) { toast(`${shortName(view.engines[k])} has no entry on this row.`); return; }
  // With an earlier eval set, a pick only chooses that model's history split.
  const base = view.base && view.base[index];
  const accepted = base ? withSplit(base, cleanEntry(entry)) : cleanEntry(entry);
  decide(index, { status: "picked", source: view.engines[k], entry: accepted });
}

function reopenRow(index) {
  const view = state.view;
  const row = view.rows[index];
  if (row.status !== "picked" && row.status !== "edited") return;
  if (row.status === "edited"
    && !window.confirm(`Discard the hand-corrected entry ${index + 1}? This cannot be undone.`)) return;
  decide(index, autoRow(view.aligned[index], view.base && view.base[index]));
}

function cycleLevel(index, engine) {
  const row = state.view.rows[index];
  const judgment = row.judgments[engine];
  if (!judgment || judgment.suggested === "same") return;
  judgment.level = LEVELS[(LEVELS.indexOf(judgment.level) + 1) % LEVELS.length];
  renderCompare(true);
  saveEval();
}

/* ---------- rendering the output panel ---------- */

// Append `text` to `node`, marking the words that are not in `other`.
function appendDiffText(node, text, other) {
  if (other === undefined || text === other) { node.append(text); return false; }
  const parts = text.split(/(\s+)/);
  const words = parts.filter((part) => part.trim());
  const kept = new Set(lcsPairs(words, other.split(/\s+/).filter(Boolean)).map(([i]) => i));
  let wordIndex = 0;
  parts.forEach((part) => {
    if (!part.trim()) { node.append(part); return; }
    if (kept.has(wordIndex)) node.append(part);
    else node.append(make("mark", "diff", part));
    wordIndex += 1;
  });
  return true;
}

function fieldRow(label, node) {
  const wrap = make("div", "entry-row");
  wrap.append(make("span", "k", label), node);
  return wrap;
}

// Render one entry. `other` is the same row's entry from the other engine
// (undefined when nothing is being compared).
function renderEntry(entry, other, number) {
  const cell = make("div", "cell entry");
  if (!entry) {
    cell.classList.add("missing");
    cell.append(make("p", "muted small", "No matching entry in this output."));
    return cell;
  }
  const comparing = other !== undefined;
  const top = make("div", "entry-top");
  top.append(make("span", "entry-n", String(number)));
  const name = make("span", "entry-name");
  appendDiffText(name, entry.name || "(no name)", comparing ? asText(other && other.name) : undefined);
  top.append(name);
  const flags = make("span", "flags");
  FLAG_FIELDS.forEach(([field, label]) => {
    if (!entry[field]) return;
    const mismatch = comparing && !(other && other[field]);
    flags.append(make("span", `badge badge-amber${mismatch ? " diff-badge" : ""}`, label));
  });
  if (flags.childElementCount) top.append(flags);
  cell.append(top);

  const body = make("div", "entry-body");
  const addText = ([field, label]) => {
    const value = asText(entry[field]);
    const theirs = comparing ? asText(other && other[field]) : undefined;
    if (!value && !theirs) return;
    const node = make("span", field === "topics" ? "muted" : "");
    if (!value) node.append(make("mark", "diff", "none"));
    else appendDiffText(node, value, theirs);
    body.append(fieldRow(label, node));
  };
  addText(TEXT_FIELDS[0]);
  const mine = Array.isArray(entry.citations) ? entry.citations.map(String) : [];
  const theirs = comparing && other && Array.isArray(other.citations) ? other.citations.map(String) : [];
  const cites = make("span", "cites");
  mine.forEach((cite, index) => {
    const chip = make("span", "cite");
    if (comparing && !theirs.includes(cite)) {
      chip.classList.add("diff-chip");
      appendDiffText(chip, cite, theirs[index] || "");
    } else chip.textContent = cite;
    cites.append(chip);
  });
  if (mine.length) body.append(fieldRow("Cites", cites));
  else if (theirs.length) body.append(fieldRow("Cites", make("mark", "diff", "none")));
  addText(TEXT_FIELDS[1]);
  const itemKey = (item) => JSON.stringify([item.action, item.case_name, item.citations]);
  const otherItems = new Set(
    (comparing && other && Array.isArray(other.history_items) ? other.history_items : []).map(itemKey));
  (Array.isArray(entry.history_items) ? entry.history_items : []).forEach((item) => {
    const node = make("span", "cites");
    if (comparing && !otherItems.has(itemKey(item))) node.classList.add("split-diff");
    node.append(make("span", "history-action", item.action));
    if (item.case_name) node.append(make("span", "history-case", item.case_name));
    item.citations.forEach((cite) => node.append(make("span", "cite", cite)));
    body.append(fieldRow("↳", node));
  });
  addText(TEXT_FIELDS[2]);
  cell.append(body);
  return cell;
}

function statusCell(view, index) {
  const row = view.rows[index];
  const cell = make("div", "cell status-cell");
  const labels = {
    auto: ["badge badge-green", {
      punctuation: "✓ same but for punctuation",
      eval: `✓ settled by the ${BASE_EVAL} eval set`,
    }[row.source] || "✓ same in both"],
    picked: ["badge badge-indigo", `✓ ${shortName(asText(row.source))}`],
    edited: ["badge badge-violet", "✓ edited"],
    open: ["badge badge-amber", "to decide"],
  };
  cell.append(make("span", ...labels[row.status]));
  if (row.status === "picked" || row.status === "edited") {
    view.engines.forEach((engine) => {
      const judgment = row.judgments[engine];
      if (!judgment || judgment.level === "same") return;
      const chip = button(`level ${LEVEL_BADGE[judgment.level]}`, `${shortName(engine)}: ${LEVEL_LABEL[judgment.level]}`);
      chip.title = judgment.level === judgment.suggested
        ? "Suggested level. Click to change."
        : `Changed by you (suggested: ${LEVEL_LABEL[judgment.suggested] || judgment.suggested}). Click to change.`;
      if (judgment.level !== judgment.suggested) chip.classList.add("level-set");
      chip.addEventListener("click", (event) => { event.stopPropagation(); cycleLevel(index, engine); });
      cell.append(chip);
    });
  }
  const actions = make("div", "row-actions");
  const edit = button("btn-outline", "Edit");
  edit.addEventListener("click", (event) => { event.stopPropagation(); openEditor(index); });
  actions.append(edit);
  if (row.status === "picked" || row.status === "edited") {
    const undo = button("btn-outline", "Undo");
    undo.addEventListener("click", (event) => { event.stopPropagation(); reopenRow(index); });
    actions.append(undo);
  }
  cell.append(actions);
  return cell;
}

function openEditor(index) {
  const view = state.view;
  view.editing = index;
  view.active = index;
  renderCompare(true);
  const first = document.querySelector(".editor input, .editor textarea");
  if (first) first.focus();
}

function editorCell(view, index) {
  const row = view.rows[index];
  const pair = view.aligned[index];
  const cell = make("form", "cell editor");
  cell.noValidate = true;
  const fields = {};
  const fill = (entry) => {
    const clean = cleanEntry(entry || {});
    fields.name.value = clean.name;
    fields.court.value = clean.court || "";
    fields.citations.value = clean.citations.join("\n");
    fields.history.value = view.splitHistory
      ? clean.history_items.map((item) => [item.action, item.case_name || "", item.citations.join("; ")]
        .join(" | ")).join("\n")
      : clean.history || "";
    fields.topics.value = clean.topics || "";
    FLAG_FIELDS.forEach(([field]) => { fields[field].checked = clean[field]; });
  };
  const addField = (key, label, control, wide) => {
    const wrap = make("label", `field${wide ? " wide" : ""}`);
    wrap.append(make("span", "field-name", label), control);
    fields[key] = control;
    cell.append(wrap);
  };
  const text = () => { const node = make("input"); node.type = "text"; return node; };
  const area = (rows) => { const node = make("textarea"); node.rows = rows; return node; };
  const head = make("div", "editor-head wide");
  head.append(make("strong", "", `Edit entry ${index + 1}`), make("span", "muted small", "Start from:"));
  view.engines.forEach((engine, k) => {
    if (!pair[k]) return;
    const copy = button("btn-outline", shortName(engine));
    copy.addEventListener("click", () => fill(pair[k]));
    head.append(copy);
  });
  cell.append(head);
  addField("name", "Case name", text(), true);
  addField("court", "Court", text());
  if (view.splitHistory) {
    addField("history", "History: one item per line, as  action | case name | citation; citation", area(3), true);
  } else addField("history", "History", text());
  addField("citations", "Citations (one per line)", area(3));
  addField("topics", "Topics", area(3));
  const checks = make("div", "editor-checks wide");
  FLAG_FIELDS.forEach(([field, label]) => {
    const wrap = make("label", "check");
    const box = make("input");
    box.type = "checkbox";
    fields[field] = box;
    wrap.append(box, ` ${label}`);
    checks.append(wrap);
  });
  cell.append(checks);
  const actions = make("div", "editor-actions wide");
  const save = button("btn-primary", "Save as accepted", "⌘↵");
  save.type = "submit";
  const cancel = button("btn-outline", "Cancel", "Esc");
  cancel.addEventListener("click", () => { view.editing = -1; renderCompare(true); });
  actions.append(save, cancel);
  cell.append(actions);
  const base = view.base && view.base[index];
  fill(row.entry || (base ? withSplit(base, pair.find(Boolean) && cleanEntry(pair.find(Boolean))) : pair[0] || pair[1]));

  cell.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = fields.name.value.trim();
    if (!name && !fields.continued_from_previous_page.checked) {
      toast("Give the entry a case name (only a carried-over piece may have none).");
      fields.name.focus();
      return;
    }
    const historyItems = !view.splitHistory ? [] : fields.history.value.split("\n")
      .map((line) => line.split("|").map((part) => part.trim()))
      .filter((parts) => parts[0])
      .map(([action, caseName, cites]) => ({
        action,
        case_name: caseName || null,
        citations: (cites || "").split(";").map((c) => c.trim()).filter(Boolean),
      }));
    const entry = {
      name,
      court: fields.court.value.trim() || null,
      citations: fields.citations.value.split("\n").map((c) => c.trim()).filter(Boolean),
      history: view.splitHistory ? historyText(historyItems) : fields.history.value.trim() || null,
      history_items: historyItems,
      topics: fields.topics.value.trim() || null,
      continued_from_previous_page: fields.continued_from_previous_page.checked,
      continues_on_next_page: fields.continues_on_next_page.checked,
    };
    const match = view.engines.find((engine, k) => sameEntry(pair[k], entry));
    if (match && sameEntry(pair[0], pair[1])) decide(index, { status: "auto", source: "both", entry });
    else if (match) decide(index, { status: "picked", source: match, entry });
    else decide(index, { status: "edited", source: "edited", entry });
  });
  cell.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { view.editing = -1; renderCompare(true); }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) cell.requestSubmit();
  });
  return cell;
}

function compareBar(view) {
  const bar = make("div", "cmp-bar");
  if (view.blocked) {
    bar.append(make("p", "error-note", view.blocked));
    return bar;
  }
  if (view.evalMode) {
    const summary = evalSummary(view);
    bar.append(summary.complete
      ? make("span", "badge badge-green", `✓ Page complete: ${summary.total} entries accepted`)
      : make("span", "badge badge-amber", `${summary.open} of ${summary.total} entries to decide`));
    const settled = view.rows.filter((r) => r.status === "auto" && r.source === "eval").length;
    if (view.base) {
      bar.append(make("span", "small tally",
        `Only history splits are queued; ${settled} other differences settled by the ${BASE_EVAL} eval set`));
    }
    const skipped = view.rows.filter((r) => r.status === "auto" && r.source === "punctuation").length;
    if (skipped) {
      bar.append(make("span", "small tally", `${skipped} punctuation-only differences accepted automatically`));
    }
    view.engines.forEach((engine) => {
      const count = (level) => view.rows.filter((row) => row.status !== "open"
        && row.judgments[engine] && row.judgments[engine].level === level).length;
      const tally = make("span", "small tally");
      tally.append(make("strong", "", `${shortName(engine)}: `),
        `${count("hallucination")} hallucination · ${count("ocr")} OCR error · ${count("field")} wrong field`);
      bar.append(tally);
    });
  } else if (view.engines.length > 1) {
    bar.append(make("span", "muted small",
      "Show exactly two engines of the same prompt version to build an eval set."));
  }
  if (view.engines.length > 1) {
    const toggle = make("label", "small cmp-toggle");
    const box = make("input");
    box.type = "checkbox";
    box.checked = state.onlyDiffs;
    box.addEventListener("change", () => {
      state.onlyDiffs = box.checked;
      savePrefs();
      renderCompare(false);
    });
    toggle.append(box, " Only real differences");
    bar.append(toggle);
  }
  return bar;
}

function compareLegend(view) {
  const legend = make("div", "cmp-legend muted small");
  legend.append(make("mark", "diff", "word"), " = text the other output does not have");
  if (view.evalMode && !view.blocked) {
    const keys = [["1", `accept ${shortName(view.engines[0])}`], ["2", `accept ${shortName(view.engines[1])}`],
      ["e", "edit"], ["u", "undo"], ["n", "next to decide"]];
    keys.forEach(([key, label]) => legend.append(" · ", make("kbd", "", key), ` ${label}`));
    legend.append(" · or click a version to accept it");
  }
  return legend;
}

function renderCompare(keepScroll) {
  const view = state.view;
  const compare = el("compare");
  const oldScroller = compare.querySelector(".cmp-scroller");
  const scrollTop = keepScroll && oldScroller ? oldScroller.scrollTop : 0;
  const reviewing = view.evalMode && !view.blocked;
  const comparing = view.engines.length > 1;

  const grid = make("div", "cmp-grid");
  grid.style.gridTemplateColumns = `repeat(${view.engines.length}, minmax(0, 1fr))${reviewing ? " 204px" : ""}`;
  view.engines.forEach((name) => {
    const head = make("div", "cmp-head");
    head.append(make("h2", "", name));
    const output = view.outputs[name];
    if (!output) head.append(make("span", "muted small", "no output for this page"));
    else {
      const cites = output.entries.reduce(
        (sum, e) => sum + (Array.isArray(e.citations) ? e.citations.length : 0), 0);
      head.append(make("span", "muted small", `${output.entries.length} entries · ${cites} citations`));
      if (output.error) head.append(make("p", "error-note", output.error));
    }
    grid.append(head);
  });
  if (reviewing) {
    const head = make("div", "cmp-head");
    head.append(make("h2", "", "Eval set"));
    grid.append(head);
  }

  let shownRows = 0;
  view.aligned.forEach((pair, index) => {
    const row = reviewing ? view.rows[index] : null;
    const differs = comparing && !pair.every((entry) => sameEntry(entry, pair[0]));
    // "Real" = more than punctuation; only worked out for two engines.
    const real = differs && (view.engines.length !== 2 || suggestLevel(pair[0], pair[1]) !== "cosmetic");
    if (state.onlyDiffs && comparing && !real && view.editing !== index) return;
    shownRows += 1;
    const cells = pair.map((entry, k) => {
      const other = comparing ? (pair[k === 0 ? 1 : 0] || null) : undefined;
      const cell = renderEntry(entry, other, index + 1);
      if (reviewing && row.status === "picked" && row.source === view.engines[k]) {
        cell.classList.add("chosen");
      }
      // Only a row still to decide reacts to a click. A decided row changes
      // through Undo or Edit, so a stray click cannot replace a decision.
      if (reviewing && row.status === "open" && entry) {
        cell.classList.add("pickable");
        cell.tabIndex = 0;
        cell.setAttribute("role", "button");
        cell.setAttribute("aria-label", `Accept the ${shortName(view.engines[k])} version of entry ${index + 1}`);
        cell.addEventListener("click", () => acceptModel(index, k));
        cell.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); acceptModel(index, k); }
        });
      }
      return cell;
    });
    if (reviewing) cells.push(statusCell(view, index));
    cells.forEach((cell) => {
      cell.dataset.row = String(index);
      if (real) cell.classList.add("row-differs");
      if (reviewing && row.status !== "open" && differs) cell.classList.add("row-decided");
      if (reviewing && view.active === index) cell.classList.add("row-active");
      grid.append(cell);
    });
    if (reviewing && row.status === "edited" && view.editing !== index) {
      const accepted = renderEntry(row.entry, undefined, index + 1);
      accepted.classList.add("accepted-edit");
      accepted.prepend(make("span", "badge badge-violet", "Accepted (edited)"));
      grid.append(accepted);
    }
    if (reviewing && view.editing === index) grid.append(editorCell(view, index));
  });

  const scroller = make("div", "scroller cmp-scroller");
  scroller.tabIndex = 0;
  scroller.append(grid);
  if (!view.aligned.length) scroller.append(make("p", "center-note", "No entries for this page."));
  else if (!shownRows) scroller.append(make("p", "center-note", "No real differences on this page."));
  compare.replaceChildren(compareBar(view), ...(comparing ? [compareLegend(view)] : []), scroller);
  scroller.scrollTop = scrollTop;
}

function scrollToActive() {
  const cell = document.querySelector(".cell.row-active");
  if (cell) cell.scrollIntoView({ block: "center" });
}

async function loadOutputs() {
  const compare = el("compare");
  const page = state.page;
  // Always in the order of the engine buttons, whatever order they were clicked in.
  const shown = state.engines.map((e) => e.name).filter((n) => state.shown.includes(n));
  state.view = null;
  if (!shown.length) {
    compare.replaceChildren(make("p", "center-note", state.engines.length
      ? "Select an engine above to see its output."
      : "No model outputs yet."));
    return;
  }
  try {
    // An eval set belongs to one prompt version, so both engines must share it.
    const versions = [...new Set(shown.map(promptVersion))];
    const version = shown.length === 2 && versions.length === 1 ? versions[0] : null;
    loadEvalSummary(version);
    const useBase = version !== null && version !== BASE_EVAL;
    const [outputsResponse, evalResponse, baseResponse] = await Promise.all([
      fetch(`/api/page/${page}`),
      version ? fetch(`/api/eval/${version}/${page}`) : null,
      useBase ? fetch(`/api/eval/${BASE_EVAL}/${page}`) : null]);
    if (!outputsResponse.ok) throw new Error(`HTTP ${outputsResponse.status}`);
    if (evalResponse && !evalResponse.ok) throw new Error(`HTTP ${evalResponse.status}`);
    if (baseResponse && !baseResponse.ok) throw new Error(`HTTP ${baseResponse.status}`);
    const outputs = (await outputsResponse.json()).outputs;
    const saved = evalResponse ? (await evalResponse.json()).saved : null;
    const baseSaved = baseResponse ? (await baseResponse.json()).saved : null;
    // Only a finished page of the earlier eval set can settle differences.
    const baseEntries = baseSaved && baseSaved.complete ? baseSaved.entries : null;
    if (page !== state.page) return;   // a newer navigation won

    shown.forEach((name) => {
      if (outputs[name]) outputs[name].entries = cleanEntries(outputs[name].entries);
    });
    const lists = shown.map((name) => (outputs[name] ? outputs[name].entries : []));
    // The earlier eval set is lined up as a third, hidden column.
    const lined = alignEntries(baseEntries ? [...lists, baseEntries] : lists);
    const aligned = lined.map((row) => row.slice(0, shown.length));
    const base = baseEntries ? lined.map((row) => row[shown.length] || null) : null;
    const view = { page, engines: shown, outputs, aligned, rows: [], active: -1, editing: -1,
      version, base, splitHistory: version !== null && version !== "v1",
      evalMode: version !== null && shown.every((name) => outputs[name]), blocked: null };
    if (view.evalMode && saved) {
      const sameEngines = saved.engines.length === 2
        && saved.engines.every((name) => shown.includes(name));
      if (!sameEngines) {
        view.blocked = `This page's eval set was built from ${saved.engines.map(shortName).join(" and ")}. `
          + "Show those two engines to continue it.";
      } else if (useBase && baseSaved && !baseSaved.complete) {
        view.blocked = `Page ${page} of the ${BASE_EVAL} eval set has entries still to decide. `
          + "Finish it first; this page is left as it is until then.";
      } else if (saved.rows.length !== aligned.length) {
        view.blocked = "The model outputs changed since this page was reviewed, so the saved choices "
          + "no longer line up. Nothing will be saved here until that is sorted out.";
      }
    }
    state.view = view;
    if (view.evalMode && !view.blocked) {
      view.rows = buildEvalRows(aligned, saved, base);
      view.rows.forEach((row, index) => judgeRow(row, aligned[index], shown));
      view.active = firstOpenRow(view, -1);
      const statuses = (rows) => JSON.stringify(rows.map((r) => [r.status, r.source,
        Object.keys(r.judgments || {}).sort().map((engine) => [
          engine, r.judgments[engine].level, r.judgments[engine].suggested])]));
      if (!saved || statuses(saved.rows) !== statuses(view.rows)) saveEval();
    }
    renderCompare(false);
  } catch (err) {
    const note = make("div", "center-note");
    note.append(make("p", "error-note", `Could not load outputs for page ${page}: ${err.message}`));
    const retry = button("btn-outline", "Retry");
    retry.addEventListener("click", loadOutputs);
    note.append(retry);
    compare.replaceChildren(note);
  }
}

/* ---------- navigation and keys ---------- */

function goTo(page) {
  if (!Number.isInteger(page) || page < 1 || page > PAGE_COUNT) {
    toast(`Enter a page from 1 to ${PAGE_COUNT}.`);
    el("page-input").value = String(state.page);
    return;
  }
  state.page = page;
  savePrefs();
  history.replaceState(null, "", `?page=${page}`);
  el("page-input").value = String(page);
  el("scan-page").textContent = `p. ${page}`;
  el("prev").disabled = page === 1;
  el("next").disabled = page === PAGE_COUNT;
  showScan();
  el("stage").scrollTo(0, 0);
  renderRail();
  loadOutputs();
  if (page < PAGE_COUNT) new Image().src = `/page/${state.source}/${page + 1}`;   // warm next
}

function reviewKey(key) {
  const view = state.view;
  if (!view || !view.evalMode || view.blocked || view.editing !== -1) return false;
  if (key === "n") {
    const next = firstOpenRow(view, view.active);
    if (next === -1) { toast("Nothing left to decide on this page."); return true; }
    view.active = next;
    renderCompare(true);
    scrollToActive();
    return true;
  }
  if (view.active < 0) return false;
  if (key === "1" || key === "2") {
    if (view.rows[view.active].status !== "open") {
      toast("This entry is already decided. Use Undo or Edit to change it.");
      return true;
    }
    acceptModel(view.active, Number(key) - 1);
  }
  else if (key === "e") openEditor(view.active);
  else if (key === "u") reopenRow(view.active);
  else return false;
  scrollToActive();
  return true;
}

function onKey(event) {
  const tag = event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (reviewKey(event.key)) { event.preventDefault(); return; }
  const actions = {
    ArrowLeft: () => goTo(Math.max(state.page - 1, 1)),
    ArrowRight: () => goTo(Math.min(state.page + 1, PAGE_COUNT)),
    "+": () => stepZoom(1), "=": () => stepZoom(1),
    "-": () => stepZoom(-1), "_": () => stepZoom(-1),
    "0": () => { state.zoom = 1; applyZoom(); savePrefs(); },
    g: () => el("page-input").select(),
  };
  const action = actions[event.key];
  if (!action) return;
  event.preventDefault();
  action();
}

async function init() {
  loadPrefs();
  el("only-output").checked = state.onlyOutput;
  applyZoom();
  try {
    const metaResponse = await fetch("/api/meta");
    if (!metaResponse.ok) throw new Error(`HTTP ${metaResponse.status}`);
    const meta = await metaResponse.json();
    state.sources = meta.sources;
    if (!state.sources.includes(state.source)) state.source = state.sources[0];
    state.engines = meta.engines.map((e) => ({ name: e.name, pages: new Set(e.pages) }));
  } catch (err) {
    toast(`Could not load the engine list: ${err.message}`);
  }
  const known = state.engines.map((e) => e.name);
  state.shown = state.shown.filter((n) => known.includes(n));
  if (!state.shown.length && known.length) state.shown = known.slice(0, 2);
  renderEngineButtons();
  renderSourceButtons();

  el("prev").addEventListener("click", () => goTo(state.page - 1));
  el("next").addEventListener("click", () => goTo(state.page + 1));
  el("page-input").addEventListener("change", (e) => goTo(Number(e.target.value)));
  el("page-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") e.target.blur();
  });
  el("zoom-in").addEventListener("click", () => stepZoom(1));
  el("zoom-out").addEventListener("click", () => stepZoom(-1));
  el("zoom-fit").addEventListener("click", () => { state.zoom = 1; applyZoom(); savePrefs(); });
  el("only-output").addEventListener("change", (e) => {
    state.onlyOutput = e.target.checked;
    savePrefs();
    renderRail();
  });
  el("rail").addEventListener("click", (e) => {
    const node = e.target.closest("button[data-page]");
    if (node) goTo(Number(node.dataset.page));
  });
  el("scan").addEventListener("error", () => toast(`Could not render page ${state.page}.`));
  document.addEventListener("keydown", onKey);
  goTo(state.page);
}

init();
