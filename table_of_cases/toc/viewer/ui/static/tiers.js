"use strict";

// How far one version of an entry is from another. Pure functions, no page
// access, so the same file runs in the browser and in Node for the tests.
// toc/tiers.py has the same rules; both are checked against
// tests/tier_cases.json, so change the two together.

const FLAG_FIELDS = [
  ["continued_from_previous_page", "continued from prev. page"],
  ["continues_on_next_page", "continues on next page"],
];
// Tiers a reviewer can set, mildest first.
const LEVELS = ["cosmetic", "field", "ocr", "hallucination"];

// Longest common subsequence of two arrays; returns matched index pairs.
function lcsPairs(left, right) {
  const rows = left.length, cols = right.length;
  const table = Array.from({ length: rows + 1 }, () => new Uint16Array(cols + 1));
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      table[i][j] = left[i] === right[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const pairs = [];
  let i = 0, j = 0;
  while (i < rows && j < cols) {
    if (left[i] === right[j]) { pairs.push([i, j]); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) i += 1;
    else j += 1;
  }
  return pairs;
}

function asText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function cleanEntry(entry) {
  return {
    name: asText(entry.name),
    court: entry.court ? String(entry.court) : null,
    citations: Array.isArray(entry.citations) ? entry.citations.map(String) : [],
    // A history never keeps its closing full stop (same rule as the runners).
    history: entry.history ? String(entry.history).trim().replace(/\.$/, "") || null : null,
    // The history split into phrases, when the prompt provides it.
    history_items: (Array.isArray(entry.history_items) ? entry.history_items : []).map((item) => ({
      action: asText(item.action),
      case_name: item.case_name ? String(item.case_name) : null,
      citations: Array.isArray(item.citations) ? item.citations.map(String) : [],
    })),
    topics: entry.topics ? String(entry.topics) : null,
    continued_from_previous_page: Boolean(entry.continued_from_previous_page),
    continues_on_next_page: Boolean(entry.continues_on_next_page),
  };
}

function cleanEntries(entries) {
  return entries.map(cleanEntry);
}

function sameEntry(a, b) {
  return Boolean(a && b) && JSON.stringify(cleanEntry(a)) === JSON.stringify(cleanEntry(b));
}

function lettersOnly(text) {
  return asText(text).replace(/[^A-Za-z0-9]/g, "");
}

function words(text) {
  return asText(text).split(/\s+/).map(lettersOnly).filter(Boolean);
}

// Characters that are easily mistaken for one another in print.
const LOOKALIKES = ["035689", "17", "1lIi", "ceo", "ae", "rt", "ft", "nu", "mn", "hb", "vy", "gq",
  "5S", "8B", "2Z", "0OoQD"];

// True when `word` is `target` with one or two characters swapped for
// look-alikes (918 for 913, Sce for See). Same length, nothing added or lost.
function isMisread(word, target) {
  if (target === undefined || word.length !== target.length) return false;
  let swaps = 0;
  for (let i = 0; i < word.length; i += 1) {
    if (word[i] === target[i]) continue;
    if (!LOOKALIKES.some((group) => group.includes(word[i]) && group.includes(target[i]))) return false;
    swaps += 1;
  }
  return swaps > 0 && swaps <= 2;
}

// Compare one field. "ocr" when every difference is a look-alike character
// swap; "hallucination" for any other change (a word added, dropped or
// altered); "same" when the letters and digits match.
function fieldLevel(modelText, acceptedText) {
  // Same letters and digits = same, however they are spaced or punctuated.
  if (lettersOnly(modelText) === lettersOnly(acceptedText)) return "same";
  const mine = words(modelText), theirs = words(acceptedText);
  if (mine.length === theirs.length) {
    // Same number of words: compare position by position.
    const changed = mine.map((word, k) => [word, theirs[k]]).filter(([a, b]) => a !== b);
    return changed.every(([a, b]) => isMisread(a, b)) ? "ocr" : "hallucination";
  }
  const pairs = lcsPairs(mine, theirs);
  let level = "ocr", i = 0, j = 0;
  const gap = (toI, toJ) => {
    const extra = mine.slice(i, toI), wanted = theirs.slice(j, toJ);
    if (extra.length !== wanted.length) level = "hallucination";
    else if (!extra.every((word, k) => isMisread(word, wanted[k]))) level = "hallucination";
    i = toI; j = toJ;
  };
  pairs.forEach(([pi, pj]) => { gap(pi, pj); i += 1; j += 1; });
  gap(mine.length, theirs.length);
  return level;
}

// The history split in comparable form (letters and digits only).
function splitKey(entry) {
  return JSON.stringify(entry.history_items.map((item) => [
    lettersOnly(item.action), lettersOnly(item.case_name), item.citations.map(lettersOnly),
  ]));
}

// Join split history back into one string, in printed order (same rule as
// toc.schema.history_text).
function historyText(items) {
  const parts = items.map((item) => [item.action, item.case_name || "", item.citations.join(", ")]
    .filter(Boolean).join(" "));
  return parts.join(", ") || null;
}

// An accepted entry built from an earlier eval set's entry (`base`: the name,
// court, citations and flags already reviewed) plus the history as one
// model split it. With no model, the history has no split.
function withSplit(base, model) {
  return {
    ...cleanEntry(base),
    topics: null,
    history: model ? model.history : cleanEntry(base).history,
    history_items: model ? model.history_items : [],
  };
}

// Decide a row from an earlier eval set, so that only the history split is
// left for review. Returns the accepted entry, or null when the split needs
// a decision: the models that read the history correctly split it
// differently, or no model read it correctly.
function settleWithEval(pair, base) {
  const wanted = lettersOnly(cleanEntry(base).history);
  if (!wanted) return withSplit(base, null);
  const correct = pair.filter(Boolean).map(cleanEntry)
    .filter((entry) => lettersOnly(entry.history) === wanted);
  if (!correct.length) return null;
  if (!correct.every((entry) => splitKey(entry) === splitKey(correct[0]))) return null;
  return withSplit(base, correct[0]);
}

// All the text of an entry in printed order, as one string.
function wholeText(entry) {
  return [entry.name, entry.court, entry.citations.join(" "), entry.history, entry.topics]
    .map(asText).join(" ");
}

// Suggest how far one version of an entry is from another:
//   same           identical
//   cosmetic       only punctuation or spacing differs; every letter and
//                  digit is the same
//   field          the text is right but sits in the wrong field, a list is
//                  split at the wrong place (including the history split),
//                  or a page-break flag is wrong
//   ocr            look-alike characters swapped (918 for 913, Sce for See)
//   hallucination  any other change: a word or citation added, dropped or
//                  altered, or the whole entry missing
function suggestLevel(model, accepted) {
  if (!model || !accepted) return "hallucination";
  const a = cleanEntry(model), b = cleanEntry(accepted);
  if (JSON.stringify(a) === JSON.stringify(b)) return "same";
  const fields = [[a.name, b.name], [a.court, b.court], [a.history, b.history], [a.topics, b.topics],
    [a.citations.join(" "), b.citations.join(" ")]];
  const fieldsMatch = fields.every(([mine, theirs]) => fieldLevel(mine, theirs) === "same");
  // Judge the wording on the entry as a whole, so text that only moved
  // between fields is not counted as invented or missing.
  const wording = fieldLevel(wholeText(a), wholeText(b));
  if (wording !== "same") return wording;
  if (!fieldsMatch || a.citations.length !== b.citations.length) return "field";
  if (splitKey(a) !== splitKey(b)) return "field";
  if (FLAG_FIELDS.some(([field]) => a[field] !== b[field])) return "field";
  return "cosmetic";
}

if (typeof module !== "undefined") {
  module.exports = {
    cleanEntry, sameEntry, suggestLevel, fieldLevel, isMisread, lcsPairs, historyText,
    settleWithEval, withSplit,
  };
}
