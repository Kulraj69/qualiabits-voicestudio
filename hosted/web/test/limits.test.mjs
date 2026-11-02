import assert from "node:assert/strict";
import { test } from "node:test";

import { describeCeiling, tooLargeSentence } from "../lib/limits.js";

test("a ceiling reads the way the plane words it", () => {
  assert.equal(describeCeiling(25 * 1024 * 1024), "25 MiB");
  assert.equal(describeCeiling(1024 * 1024), "1 MiB");
  assert.equal(describeCeiling(1024 * 1024 + 5), "1 MiB");
  assert.equal(describeCeiling(4096), "4096 bytes");
  assert.equal(describeCeiling(0), "");
  assert.equal(describeCeiling("nonsense"), "");
});

test("the refusal names the limit twice, in the plane's own wording", () => {
  // Byte for byte the sentence `hosted/control_plane/app.py` raises for the same
  // ceiling. The browser run compares the two live; this pins the shape here.
  assert.equal(
    tooLargeSentence(25 * 1024 * 1024),
    "That upload is larger than 25 MiB. An upload is at most 25 MiB.",
  );
  assert.equal(
    tooLargeSentence(1024),
    "That upload is larger than 1024 bytes. An upload is at most 1024 bytes.",
  );
});

test("an unknown ceiling still produces a sentence, and never a fake number", () => {
  const sentence = tooLargeSentence(0);
  assert.match(sentence, /larger than/);
  assert.doesNotMatch(sentence, /\d/);
});
