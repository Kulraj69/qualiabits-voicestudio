import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatBytes,
  formatCount,
  formatDateTime,
  formatSeconds,
  isTerminal,
  keyPrefix,
  statusLabel,
} from "../lib/format.js";

test("an unmeasured duration is a dash, never a zero standing in for unknown", () => {
  assert.equal(formatSeconds(null), "—");
  assert.equal(formatSeconds(undefined), "—");
  assert.equal(formatSeconds(""), "—");
  assert.equal(formatSeconds("nonsense"), "—");
});

test("a measured duration keeps three decimals, trimmed", () => {
  assert.equal(formatSeconds(1.83), "1.83 s");
  assert.equal(formatSeconds(1.826), "1.826 s");
  assert.equal(formatSeconds(0), "0 s");
  assert.equal(formatSeconds(16.477), "16.477 s");
});

test("sub-second audio is not rounded away to zero", () => {
  assert.equal(formatSeconds(0.25), "0.250 s");
});

test("byte and count formatting is thousands-separated, dashes when unknown", () => {
  assert.equal(formatBytes(790946), "790,946");
  assert.equal(formatBytes(null), "—");
  assert.equal(formatCount(1133926), "1,133,926");
  assert.equal(formatCount(undefined), "—");
});

test("a key is only ever shown as its public id", () => {
  assert.equal(keyPrefix("abc123"), "qbv_abc123");
  assert.equal(keyPrefix(""), "—");
  assert.equal(keyPrefix(null), "—");
});

test("job statuses read as the five the plane defines", () => {
  assert.equal(statusLabel("queued"), "Queued");
  assert.equal(statusLabel("running"), "Running");
  assert.equal(statusLabel("succeeded"), "Done");
  assert.equal(statusLabel("failed"), "Failed");
  assert.equal(statusLabel("cancelled"), "Cancelled");
  assert.equal(statusLabel("weird"), "weird");
});

test("only the three terminal statuses stop the page polling", () => {
  assert.equal(isTerminal("queued"), false);
  assert.equal(isTerminal("running"), false);
  assert.equal(isTerminal("succeeded"), true);
  assert.equal(isTerminal("failed"), true);
  assert.equal(isTerminal("cancelled"), true);
});

test("a missing timestamp is a dash; a real one is rendered", () => {
  assert.equal(formatDateTime(null), "—");
  assert.equal(formatDateTime(""), "—");
  assert.equal(formatDateTime("not a date"), "not a date");
  assert.match(formatDateTime("2026-11-01T16:18:14+00:00"), /2026/);
});