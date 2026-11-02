import assert from "node:assert/strict";
import { test } from "node:test";

import {
  srtTimestamp,
  toJson,
  toPlainText,
  toSrt,
  transcriptFilename,
} from "../lib/transcript.js";

test("srt timestamps are the form SubRip readers accept", () => {
  assert.equal(srtTimestamp(0), "00:00:00,000");
  assert.equal(srtTimestamp(1.25), "00:00:01,250");
  assert.equal(srtTimestamp(61.5), "00:01:01,500");
  assert.equal(srtTimestamp(3661.004), "01:01:01,004");
});

test("a rounding carry does not produce a 1000-millisecond field", () => {
  assert.equal(srtTimestamp(1.9999), "00:00:02,000");
  assert.equal(srtTimestamp(59.9999), "00:01:00,000");
});

test("a bad timestamp is zero rather than NaN in a subtitle file", () => {
  assert.equal(srtTimestamp(undefined), "00:00:00,000");
  assert.equal(srtTimestamp(-3), "00:00:00,000");
  assert.equal(srtTimestamp("nonsense"), "00:00:00,000");
});

test("the engine's segments keep their own times, in order", () => {
  const srt = toSrt({
    text: "यह एक हिन्दी वाक्य है।",
    segments: [
      { start: 0, end: 1.5, text: " यह एक" },
      { start: 1.5, end: 2.25, text: " हिन्दी वाक्य है।" },
    ],
  });
  assert.equal(
    srt,
    "1\n00:00:00,000 --> 00:00:01,500\nयह एक\n\n" +
      "2\n00:00:01,500 --> 00:00:02,250\nहिन्दी वाक्य है।\n\n",
  );
});

test("no segments is one cue covering the recording, and says so by having no times", () => {
  const srt = toSrt({ text: "plain answer" }, { duration: 3.5 });
  assert.equal(srt, "1\n00:00:00,000 --> 00:00:03,500\nplain answer\n\n");
});

test("no segments and no duration still produces a file that opens", () => {
  assert.equal(
    toSrt({ text: "plain answer" }),
    "1\n00:00:00,000 --> 00:00:00,000\nplain answer\n\n",
  );
});

test("an empty answer produces no subtitle file rather than a cue with no text", () => {
  assert.equal(toSrt({ text: "" }), "");
  assert.equal(toSrt(null), "");
});

test("plain text prefers the engine's own text field and never invents one", () => {
  assert.equal(toPlainText({ text: "hello" }), "hello");
  assert.equal(toPlainText("hello"), "hello");
  assert.equal(toPlainText({ segments: [{ text: "x" }] }), "");
  assert.equal(toPlainText(null), "");
});

test("json is the answer verbatim, formatted", () => {
  const payload = { task: "transcribe", text: "hi", segments: [] };
  assert.equal(toJson(payload), JSON.stringify(payload, null, 2));
  assert.equal(toJson(null), "{}");
});

test("a download name is derived from the upload and stays safe", () => {
  assert.equal(transcriptFilename("call 01.wav", "txt"), "call-01.transcript.txt");
    // An upload name is attacker-controlled; a traversal shape must not survive
  // into a filename the browser is handed.
  assert.equal(transcriptFilename("../../etc/passwd", "json"), "passwd.transcript.json");
  assert.equal(transcriptFilename("...", "json"), "recording.transcript.json");
  assert.equal(transcriptFilename("-rf", "srt"), "rf.transcript.srt");
  assert.equal(transcriptFilename("", "srt"), "recording.transcript.srt");
    // A name with nothing Latin left in it falls back rather than transliterating:
  // guessing a spelling is how a download ends up called something nobody chose.
  assert.equal(transcriptFilename("recordings/फोन.mp3", "srt"), "recording.transcript.srt");
  assert.equal(transcriptFilename("call-2026-11-02.wav", "txt"), "call-2026-11-02.transcript.txt");
});