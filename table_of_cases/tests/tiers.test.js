"use strict";

// Checks the viewer's rules against the same cases as tests/test_tiers.py.
//   node --test tests/

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const tiers = require("../toc/viewer/ui/static/tiers.js");

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "tier_cases.json"), "utf8"));

for (const item of fixture.cases) {
  test(item.name, () => {
    const model = { ...fixture.base, ...item.model };
    const gold = { ...fixture.base, ...item.gold };
    assert.strictEqual(tiers.suggestLevel(model, gold), item.tier);
  });
}

test("split history joins back in printed order", () => {
  const items = [
    { action: "reh den", case_name: null, citations: [] },
    { action: "cert den", case_name: "Poe v. Bank", citations: ["1 SCt 2", "3 LEd2d 4"] },
  ];
  assert.strictEqual(tiers.historyText(items), "reh den, cert den Poe v. Bank 1 SCt 2, 3 LEd2d 4");
  assert.strictEqual(tiers.historyText([]), null);
});

const item = (action, citations) => ({ action, case_name: null, citations });
const split = (history, items) => ({ ...fixture.base, history, history_items: items, topics: null });
const base = { ...fixture.base, history: "reh den, cert den 50 SCt 60" };
const oneItem = split(base.history, [item("reh den, cert den", ["50 SCt 60"])]);
const twoItems = split(base.history, [item("reh den", []), item("cert den", ["50 SCt 60"])]);

test("the earlier eval set settles a text difference, keeping the split", () => {
  const misread = { ...oneItem, citations: ["100 FSupp2d 918", "10 F3d 20"] };
  const settled = tiers.settleWithEval([misread, oneItem], base);
  assert.deepStrictEqual(settled.citations, base.citations);
  assert.deepStrictEqual(settled.history_items, oneItem.history_items);
  assert.strictEqual(settled.topics, null);
});

test("a model that misread the history does not decide the split", () => {
  const misread = split("reh den, cert den 50 SCt 66", [item("reh den, cert den", ["50 SCt 66"])]);
  const settled = tiers.settleWithEval([misread, twoItems], base);
  assert.deepStrictEqual(settled.history_items, twoItems.history_items);
});

test("two correct readings split differently need a decision", () => {
  assert.strictEqual(tiers.settleWithEval([oneItem, twoItems], base), null);
});

test("no correct reading of the history needs a decision", () => {
  const misread = split("reh den, cert den 50 SCt 66", [item("reh den, cert den", ["50 SCt 66"])]);
  assert.strictEqual(tiers.settleWithEval([misread, misread], base), null);
});

test("an entry without history is settled with no split", () => {
  const settled = tiers.settleWithEval([oneItem, twoItems], { ...fixture.base, history: null });
  assert.deepStrictEqual(settled.history_items, []);
});

test("a missing entry is the most severe tier", () => {
  assert.strictEqual(tiers.suggestLevel(null, fixture.base), "hallucination");
});

test("the closing full stop of a history is not part of it", () => {
  const withStop = { ...fixture.base, history: "reh den." };
  const without = { ...fixture.base, history: "reh den" };
  assert.ok(tiers.sameEntry(withStop, without));
});
