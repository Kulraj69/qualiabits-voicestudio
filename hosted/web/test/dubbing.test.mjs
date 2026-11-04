/**
 * The pure half of the Dub page.
 *
 * No browser and no plane: these are functions of a plane response and nothing
 * else, so what is checked is what the page will render for a given record —
 * which is where a page usually starts rounding a number into a nicer one.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { describeDub, dubFilename, hasWordsToSpeak } from "../lib/dubbing.js";

test("a dub downloads under the plane's own id, not a page counter", () => {
  assert.equal(dubFilename(1), "dub-1.wav");
  assert.equal(dubFilename(42), "dub-42.wav");
});

test("the caption names the ear and the mouth separately, not one blended number", () => {
  const line = describeDub({
    transcript_seconds: undefined,
    transcribe_seconds: 3.5,
    speech_seconds: 1.25,
    audio_seconds: 1.25,
    transcript_characters: 31,
  });
  assert.match(line, /spent 3\.50 s hearing it and 1\.25 s saying it/);
  assert.match(line, /1\.25 s of audio back/);
  assert.match(line, /31 characters heard/);
});

test("an engine time the plane did not send is left out, not shown as a zero", () => {
  const line = describeDub({ audio_seconds: 1.25, transcript_characters: 10 });
  // No "0.00 s" anywhere: a missing number is missing, not spent.
  assert.doesNotMatch(line, /0\.00/);
  assert.match(line, /1\.25 s of audio back/);
  assert.equal(describeDub(null), "");
});

test("a measured zero is kept, because it is a measurement", () => {
  const line = describeDub({
    transcribe_seconds: 0,
    speech_seconds: 0,
    audio_seconds: 0,
    transcript_characters: 0,
  });
  assert.match(line, /spent 0\.00 s hearing it and 0\.00 s saying it/);
});

test("a transcript with only whitespace is not something to speak", () => {
  assert.equal(hasWordsToSpeak(""), false);
  assert.equal(hasWordsToSpeak("   "), false);
  assert.equal(hasWordsToSpeak(null), false);
  assert.equal(hasWordsToSpeak(undefined), false);
  assert.equal(hasWordsToSpeak("यह एक वाक्य है।"), true);
});