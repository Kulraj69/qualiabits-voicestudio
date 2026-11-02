import assert from "node:assert/strict";
import { test } from "node:test";

import {
  defaultVoiceId,
  describeCatalogue,
  groupVoices,
  languageOptions,
} from "../lib/voices.js";

const CATALOGUE = {
  engine: { id: "sherpa-onnx", display_name: "sherpa-onnx", default_voice: "af_alloy", reachable: true },
  voices: [
    { id: "af_alloy", name: "af_alloy", kind: "engine", language: null, default: true },
    { id: "hf_alpha", name: "hf_alpha", kind: "engine", language: null, default: false },
    { id: "alloy", name: "Alloy", kind: "alias", language: null, default: false },
    { id: "profile-1", name: "Studio read", kind: "profile", language: "hi", default: false },
  ],
  count: 4,
  kinds: { engine: 2, alias: 1, profile: 1 },
};

test("groups voices by the kind the plane reported", () => {
  const groups = groupVoices(CATALOGUE.voices);
  assert.deepEqual(groups.engine.map((v) => v.id), ["af_alloy", "hf_alpha"]);
  assert.deepEqual(groups.alias.map((v) => v.id), ["alloy"]);
  assert.deepEqual(groups.profile.map((v) => v.id), ["profile-1"]);
});

test("filtering by a language the plane named keeps only those voices", () => {
  const groups = groupVoices(CATALOGUE.voices, { language: "hi" });
  assert.deepEqual(groups.profile.map((v) => v.id), ["profile-1"]);
  assert.equal(groups.engine.length, 0);
  assert.equal(groups.alias.length, 0);
});

test("a voice with no language is never guessed into a language bucket", () => {
  const groups = groupVoices(CATALOGUE.voices, { language: "en" });
  assert.equal(groups.engine.length, 0);
  assert.equal(groups.alias.length, 0);
});

test("language options are the distinct named languages, deduplicated", () => {
  assert.deepEqual(languageOptions(CATALOGUE.voices), ["hi"]);
  assert.deepEqual(languageOptions([{ language: "hi" }, { language: "hi" }, { language: null }]), ["hi"]);
  assert.deepEqual(languageOptions([]), []);
});

test("the first-selected voice is the engine's own default, never the first row", () => {
  assert.equal(defaultVoiceId(CATALOGUE.voices), "af_alloy");
});

test("with no default marked, the first engine voice leads; then the first row", () => {
  const noDefault = CATALOGUE.voices.map((v) => ({ ...v, default: false }));
  assert.equal(defaultVoiceId(noDefault), "af_alloy");
  assert.equal(defaultVoiceId([{ id: "only-alias", kind: "alias" }]), "only-alias");
  assert.equal(defaultVoiceId([]), "");
});

test("the catalogue heading names the kinds the plane reported", () => {
  const line = describeCatalogue(CATALOGUE);
  assert.match(line, /4 voices/);
  assert.match(line, /2 from the engine/);
  assert.match(line, /1 OpenAI alias/);
  assert.match(line, /1 saved profile/);
});

test("an unreachable engine is described as unreachable, not as zero voices", () => {
  const line = describeCatalogue({
    engine: { reachable: false, id: null, display_name: null, default_voice: null },
    voices: [],
    count: 0,
    kinds: { engine: 0, alias: 0, profile: 0 },
  });
  assert.match(line, /not reachable/);
});

test("a missing catalogue is described without inventing one", () => {
  assert.match(describeCatalogue(null), /did not answer/);
});
// ── the search and the kind filter the Voice Library uses ─────────────────────
// These are the same catalogue the page receives, so the guarantee under test is
// not "the search works" but "the search can only ever return what the plane
// sent": every case below asserts a subset, never an addition.

import { filterVoices, kindCounts, PREVIEW_TEXT } from "../lib/voices.js";

test("an empty search keeps every voice the plane sent, in its order", () => {
  const kept = filterVoices(CATALOGUE.voices, { query: "" });
  assert.deepEqual(kept.map((v) => v.id), CATALOGUE.voices.map((v) => v.id));
  assert.deepEqual(filterVoices(CATALOGUE.voices).map((v) => v.id), CATALOGUE.voices.map((v) => v.id));
});

test("the search folds case and trims, and matches a substring of id or name", () => {
  assert.deepEqual(filterVoices(CATALOGUE.voices, { query: "HF_" }).map((v) => v.id), ["hf_alpha"]);
  assert.deepEqual(filterVoices(CATALOGUE.voices, { query: "  alloy  " }).map((v) => v.id), ["af_alloy", "alloy"]);
  assert.deepEqual(filterVoices(CATALOGUE.voices, { query: "studio" }).map((v) => v.id), ["profile-1"]);
});

test("the search reads the language the plane named, not a guessed one", () => {
  assert.deepEqual(filterVoices(CATALOGUE.voices, { query: "hi" }).map((v) => v.id), ["profile-1"]);
  // Nothing in the catalogue is named English, so an "en" search returns nothing
  // rather than every voice whose language the plane left null.
  assert.deepEqual(filterVoices(CATALOGUE.voices, { query: "en" }), []);
});

test("the search can only ever return voices that arrived", () => {
  for (const query of ["a", "o", "-", "af", "zzz", PREVIEW_TEXT]) {
    const kept = filterVoices(CATALOGUE.voices, { query });
    for (const voice of kept) {
      assert.ok(CATALOGUE.voices.includes(voice), `${voice.id} was not in the catalogue`);
    }
  }
});

test("the kind filter keeps one kind and never invents a bucket", () => {
  assert.deepEqual(filterVoices(CATALOGUE.voices, { kind: "alias" }).map((v) => v.id), ["alloy"]);
  assert.deepEqual(filterVoices(CATALOGUE.voices, { kind: "profile" }).map((v) => v.id), ["profile-1"]);
  assert.deepEqual(filterVoices(CATALOGUE.voices, { kind: "all" }).map((v) => v.id),
    CATALOGUE.voices.map((v) => v.id));
  assert.deepEqual(filterVoices(CATALOGUE.voices, { kind: "made-up" }), []);
});

test("kind counts are the plane's own numbers, and an unknown kind is counted apart", () => {
  assert.deepEqual(kindCounts(CATALOGUE.voices), { engine: 2, alias: 1, profile: 1, other: 0 });
  assert.deepEqual(kindCounts([{ id: "x", kind: "mystery" }]), { engine: 0, alias: 0, profile: 0, other: 1 });
  assert.deepEqual(kindCounts([]), { engine: 0, alias: 0, profile: 0, other: 0 });
});

test("the preview sentence is one fixed line, so two voices are comparable", () => {
  assert.equal(typeof PREVIEW_TEXT, "string");
  assert.ok(PREVIEW_TEXT.length > 20);
  assert.match(PREVIEW_TEXT, /preview/i);
});
