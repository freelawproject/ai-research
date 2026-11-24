"use strict";

const FILTER_KEY = "caseNameAnnotator.filters";
const IGNORED_WORDS = new Set(["v", "in", "re", "et", "al", "and", "the", "of", "ex", "rel"]);

function storageGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
}

function inTextField(target) {
  return target instanceof HTMLElement &&
    (target.tagName === "TEXTAREA" || target.tagName === "SELECT" ||
     (target.tagName === "INPUT" && target.type !== "checkbox"));
}

/* ---------- list page ---------- */

function initList() {
  const form = document.getElementById("filters");
  // Restore the last-used filters when the page is opened without any.
  if (!location.search) {
    const saved = storageGet(FILTER_KEY);
    if (saved) { location.replace("/?" + saved); return; }
  } else {
    storageSet(FILTER_KEY, location.search.slice(1));
  }
  form.querySelectorAll(".seg button").forEach((button) => {
    button.addEventListener("click", () => {
      form.elements[button.dataset.name].value = button.dataset.value;
      submitFilters(form);
    });
  });
  form.elements.stratum.addEventListener("change", () => submitFilters(form));
  form.elements.doc_type.addEventListener("change", () => submitFilters(form));
}

function submitFilters(form) {
  const params = new URLSearchParams();
  ["split", "stratum", "doc_type"].forEach((name) => {
    if (form.elements[name].value) params.set(name, form.elements[name].value);
  });
  // "_" keeps an explicit "no filters" choice from being replaced by the saved one.
  const query = params.toString() || "_=1";
  storageSet(FILTER_KEY, query);
  location.href = "/?" + query;
}

/* ---------- review page ---------- */

function wordsOf(text) {
  return new Set((text.toLowerCase().match(/[a-z0-9]+/g) || []));
}

// Full captions only: list words that are not in the text the model reads.
function showStrayWords() {
  const el = document.getElementById("stray");
  if (!el) return;
  // Wide-window records are checked against all the text shown (8,000 characters).
  const doc = document.getElementById("doc");
  const wide = doc.dataset.wide === "yes";
  const source = wordsOf((wide ? doc : document.getElementById("doc-input")).textContent);
  const stray = [...wordsOf(document.getElementById("value").value)]
    .filter((word) => !source.has(word) && !IGNORED_WORDS.has(word));
  el.hidden = stray.length === 0;
  el.textContent = stray.length
    ? "Not found in the first " + (wide ? "8,000" : "2,000") + " characters: " + stray.join(", ")
    : "";
}

function collapseSpaces(text) {
  return text.trim().split(/\s+/).join(" ");
}

// Highlight the "Use" button of every source whose value is in the field now.
function markActiveCandidate() {
  const current = collapseSpaces(document.getElementById("value").value);
  document.querySelectorAll("[data-use]").forEach((button) => {
    const candidate = document.getElementById(button.dataset.use).dataset.value;
    const active = current !== "" && collapseSpaces(candidate) === current;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function useCandidate(button) {
  document.getElementById("value").value =
    document.getElementById(button.dataset.use).dataset.value;
  showStrayWords();
  markActiveCandidate();
}

function showError(message) {
  const error = document.getElementById("error");
  error.textContent = message;
  error.hidden = false;
}

async function saveLabel(config, status) {
  document.getElementById("error").hidden = true;
  const body = {
    value: document.getElementById("value").value,
    flags: [...document.querySelectorAll("input[name=flag]:checked")].map((box) => box.value),
    notes: document.getElementById("notes").value,
    status: status,
    split: config.filters.split,
    stratum: config.filters.stratum,
    doc_type: config.filters.doc_type,
  };
  let response;
  try {
    response = await fetch("/api/label/" + config.field + "/" + config.clusterId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    showError("Could not reach the app. Check that it is still running, then save again.");
    return;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showError(typeof data.detail === "string" ? data.detail : "The save was rejected.");
    return;
  }
  location.href = data.next_id
    ? "/review/" + config.field + "/" + data.next_id + "?" + config.query
    : "/?" + config.query;
}

// The document has no case name: clear the field, set the flag, accept.
function saveNoCaption(config) {
  document.getElementById("value").value = "";
  const box = document.querySelector('input[name=flag][value="no_caption"]');
  if (box) box.checked = true;
  markActiveCandidate();
  saveLabel(config, "reviewed");
}

function initReview(config) {
  const value = document.getElementById("value");
  value.addEventListener("input", showStrayWords);
  value.addEventListener("input", markActiveCandidate);
  showStrayWords();
  markActiveCandidate();

  document.querySelectorAll("[data-use]").forEach((button) => {
    button.addEventListener("click", () => useCandidate(button));
  });
  document.getElementById("save").addEventListener("click", () => saveLabel(config, "reviewed"));
  document.getElementById("skip").addEventListener("click", () => saveLabel(config, "skipped"));
  document.getElementById("none").addEventListener("click", () => saveNoCaption(config));

  document.addEventListener("keydown", (event) => {
    if (inTextField(event.target)) {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        saveLabel(config, "reviewed");
      } else if (event.key === "Escape") {
        event.target.blur();
      }
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const actions = {
      d: () => saveLabel(config, "reviewed"),
      s: () => saveLabel(config, "skipped"),
      x: () => saveNoCaption(config),
      e: () => value.focus(),
      j: () => document.getElementById("next") && document.getElementById("next").click(),
      k: () => document.getElementById("prev") && document.getElementById("prev").click(),
    };
    if (actions[event.key]) {
      event.preventDefault();
      actions[event.key]();
    } else if (/^[1-9]$/.test(event.key)) {
      const button = document.querySelector('[data-use="cand-' + event.key + '"]');
      if (button) { event.preventDefault(); button.click(); }
    }
  });
}
