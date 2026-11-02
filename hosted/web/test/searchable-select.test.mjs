/**
 * The pure half of the package's select box, tested without a browser.
 *
 * `components/SearchableSelect.jsx` is a client component, so nothing here can
 * render it. What it delegates — which options a query keeps, where the arrow keys
 * move, what Enter and Escape mean — is `lib/searchable-select.js`, and that is
 * what these tests hold to the behaviour the repository rule asks for: a searchable
 * select with keyboard selection and a preserved disabled state.
 *
 * The rule itself is checked where it can be: `tests/hosted/test_web_pages_claims.py`
 * reads the library's source and fails if a native `<select>` comes back.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activeAfterFilter,
  activeOption,
  filterOptions,
  nextIndex,
  normalizeQuery,
  reduceKey,
  withGroupHeaders,
} from "../lib/searchable-select.js";

const KINDS = [
  { value: "all", label: "All kinds (4)" },
  { value: "engine", label: "Engine (2)" },
  { value: "alias", label: "Alias (1)" },
  { value: "profile", label: "Saved profile (1)" },
];

test("an empty query keeps every option, in the order it arrived", () => {
  assert.deepEqual(filterOptions(KINDS, "").map((o) => o.value), [
    "all",
    "engine",
    "alias",
    "profile",
  ]);
  assert.deepEqual(filterOptions(KINDS, "   ").map((o) => o.value), [
    "all",
    "engine",
    "alias",
    "profile",
  ]);
});

test("the gallery entries come from what arrived, never from the control", () => {
  const kept = filterOptions(KINDS, "eng");
  assert.deepEqual(kept.map((o) => o.value), ["engine"]);
  assert.ok(kept.every((option) => KINDS.includes(option)));
  assert.deepEqual(filterOptions(KINDS, "nothing here"), []);
});

test("the query matches the value as well as the label, case-insensitively", () => {
  assert.deepEqual(filterOptions(KINDS, "ALIAS").map((o) => o.value), ["alias"]);
  // "profile" is in the value, and the label says "Saved profile"; both match.
  assert.deepEqual(filterOptions(KINDS, "profile").map((o) => o.value), ["profile"]);
});

test("a malformed option is dropped, not rendered as a row nobody can choose", () => {
  const rough = [null, undefined, {}, { value: "engine" }, { label: "Alias" }];
  assert.deepEqual(filterOptions(rough, "engine").map((o) => o.value), ["engine"]);
  assert.deepEqual(filterOptions(rough, "").map((o) => o.value), ["engine"]);
  assert.deepEqual(filterOptions(rough, "").length, 1);
  assert.deepEqual(filterOptions(undefined, "engine"), []);
});

test("normalizeQuery trims and folds, and survives nothing at all", () => {
  assert.equal(normalizeQuery("  EngINe "), "engine");
  assert.equal(normalizeQuery(null), "");
  assert.equal(normalizeQuery(undefined), "");
});

test("the arrow keys wrap at both ends and never leave the list", () => {
  assert.equal(nextIndex(4, 3, 1), 0);
  assert.equal(nextIndex(4, 0, -1), 3);
  assert.equal(nextIndex(4, 1, 1), 2);
  assert.equal(nextIndex(0, -1, 1), -1);
});

test("ArrowDown opens the list on the first row, ArrowUp on the last", () => {
  assert.deepEqual(reduceKey({ open: false, activeIndex: -1 }, "ArrowDown", 3), {
    open: true,
    activeIndex: 0,
    commit: false,
  });
  assert.deepEqual(reduceKey({ open: false, activeIndex: -1 }, "ArrowUp", 3), {
    open: true,
    activeIndex: 2,
    commit: false,
  });
  assert.deepEqual(reduceKey({ open: false, activeIndex: -1 }, "Enter", 3), {
    open: true,
    activeIndex: 0,
    commit: false,
  });
});

test("an empty list opens with nothing active, and Enter cannot commit it", () => {
  const opened = reduceKey({ open: false, activeIndex: -1 }, "ArrowDown", 0);
  assert.deepEqual(opened, { open: true, activeIndex: -1, commit: false });
  assert.equal(reduceKey({ open: true, activeIndex: -1 }, "Enter", 0).commit, false);
});

test("Enter commits the active row, and only a row that exists", () => {
  assert.equal(reduceKey({ open: true, activeIndex: 1 }, "Enter", 3).commit, true);
  assert.equal(reduceKey({ open: true, activeIndex: 4 }, "Enter", 3).commit, false);
  assert.equal(reduceKey({ open: false, activeIndex: -1 }, "Enter", 3).commit, false);
});

test("Escape and Tab close without committing, and forget the active row", () => {
  for (const key of ["Escape", "Tab"]) {
    assert.deepEqual(reduceKey({ open: true, activeIndex: 2 }, key, 3), {
      open: false,
      activeIndex: -1,
      commit: false,
    });
  }
});

test("Home and End jump to the ends of the filtered list", () => {
  assert.equal(reduceKey({ open: true, activeIndex: 1 }, "Home", 5).activeIndex, 0);
  assert.equal(reduceKey({ open: true, activeIndex: 1 }, "End", 5).activeIndex, 4);
});

test("a key that is not ours leaves the state alone", () => {
  const state = reduceKey({ open: true, activeIndex: 1 }, "a", 3);
  assert.deepEqual(state, { open: true, activeIndex: 1, commit: false });
});

test("the active row is a row of the filtered list, or nothing", () => {
  assert.equal(activeOption(KINDS, 0), KINDS[0]);
  assert.equal(activeOption(KINDS, 3), KINDS[3]);
  assert.equal(activeOption(KINDS, 4), null);
  assert.equal(activeOption(KINDS, -1), null);
  assert.equal(activeOption([], 0), null);
});

test("after a query change the active row is the first survivor, or nothing", () => {
  assert.equal(activeAfterFilter([{ value: "engine", label: "Engine (2)" }]), 0);
  assert.equal(activeAfterFilter([]), -1);
  assert.equal(activeAfterFilter(undefined), -1);
});

test("typing a query and walking the survivors picks a real option", () => {
  // The whole loop the component runs: narrow, move, commit.
  const shown = filterOptions(KINDS, "e");
  const first = activeAfterFilter(shown);
  const moved = reduceKey({ open: true, activeIndex: first }, "ArrowDown", shown.length);
  const chosen = activeOption(shown, moved.activeIndex);
  assert.ok(chosen, "no option was active after moving");
  assert.ok(KINDS.includes(chosen), "the chosen option was not in the list it came from");
});
test("grouped options get a header where the group changes, and nowhere else", () => {
  const rows = withGroupHeaders([
    { value: "af_alloy", group: "Engine voices", label: "af_alloy" },
    { value: "hf_alpha", group: "Engine voices", label: "hf_alpha" },
    { value: "alloy", group: "OpenAI aliases", label: "Alloy" },
    { value: "profile-1", group: "Saved profiles", label: "Studio read" },
  ]);
  assert.deepEqual(rows.filter((row) => row.header).map((row) => row.header), [
    "Engine voices",
    "OpenAI aliases",
    "Saved profiles",
  ]);
  assert.deepEqual(rows.filter((row) => row.option).map((row) => row.option.value), [
    "af_alloy",
    "hf_alpha",
    "alloy",
    "profile-1",
  ]);
});

test("an option with no group gets no header, and the list is never reordered", () => {
  const rows = withGroupHeaders([
    { value: "all", label: "All kinds" },
    { value: "engine", group: "Engine", label: "Engine" },
    { value: "loose", label: "Loose" },
  ]);
  assert.deepEqual(rows.filter((row) => row.header).map((row) => row.header), ["Engine"]);
  assert.deepEqual(rows.filter((row) => row.option).map((row) => row.option.value), [
    "all",
    "engine",
    "loose",
  ]);
});

test("headers survive filtering: the grouping is of what is left, not of the catalogue", () => {
  const all = [
    { value: "hf_alpha", group: "Engine voices", label: "hf_alpha" },
    { value: "alloy", group: "OpenAI aliases", label: "Alloy (alias)" },
  ];
  const rows = withGroupHeaders(filterOptions(all, "alias"));
  assert.deepEqual(rows.filter((row) => row.header).map((row) => row.header), ["OpenAI aliases"]);
  assert.deepEqual(rows.filter((row) => row.option).map((row) => row.option.value), ["alloy"]);
  assert.deepEqual(withGroupHeaders(filterOptions(all, "nothing")), []);
});

test("withGroupHeaders tolerates nothing at all", () => {
  assert.deepEqual(withGroupHeaders(undefined), []);
  assert.deepEqual(withGroupHeaders([null, undefined]), []);
  assert.deepEqual(withGroupHeaders([{ label: "no value" }]), []);
});
