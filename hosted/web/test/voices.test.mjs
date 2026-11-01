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