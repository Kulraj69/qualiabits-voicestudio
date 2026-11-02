/**
 * The browser harness's own claims, checked against its source.
 *
 * `e2e/dashboard.mjs` is the only place a few of the acceptance page's rows can
 * point at, and it is not run by `npm test` — it needs a Chromium, a plane, an
 * engine and, on the real path, several minutes of CPU. So the file that decides
 * what the run records is read here instead: if somebody deletes the
 * transcription model's fingerprint, drops the ceiling case, or stops pinning the
 * Whisper weights, these tests fail even though no browser was launched.
 *
 * This is not a substitute for the run. It is the guard that keeps the run's
 * evidence from quietly getting thinner between runs.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HARNESS = readFileSync(path.join(WEB, "e2e", "dashboard.mjs"), "utf8");
const REAL_ENGINE = readFileSync(path.join(WEB, "e2e", "real-engine.sh"), "utf8");

test("the harness records the transcription model's own bytes", () => {
  assert.match(HARNESS, /function asrFingerprint\(\)/);
  assert.match(HARNESS, /transcription_model: asrFingerprint\(\)/);
  assert.match(HARNESS, /files\[name\] = sha256\(file\)/);
});

test("the transcription weights are checked against the hash the script pins", () => {
  assert.match(HARNESS, /transcription_model_pinned/);
  assert.match(
    HARNESS,
    /asr\.files\["model\.bin"\] === result\.transcription_model_pinned/,
  );
  assert.match(REAL_ENGINE, /QB_E2E_ASR_MODEL_PINNED="\$SHA_ASR_MODEL"/);
  assert.match(REAL_ENGINE, /QB_E2E_ASR_MODEL="\$ASR"/);
});

test("the ceiling case sends one byte past the plane's own limit", () => {
  // 25 MiB is the plane's default (hosted/control_plane/config.py). The harness
  // must not lower it for the run: a smaller limit would be a test's number.
  assert.match(HARNESS, /25 \* 1024 \* 1024 \+ 1/);
  assert.match(HARNESS, /fetch\("\/api\/transcribe", \{ method: "POST", body \}\)/);
  assert.match(HARNESS, /overCeiling\.status === 413/);
});

test("the three transcript downloads are read back, not merely clicked", () => {
  for (const testId of ["download-txt", "download-srt", "download-json"]) {
    assert.match(HARNESS, new RegExp(`\\["[a-z]+", "${testId}"\\]`));
  }
  assert.match(HARNESS, /page\.waitForEvent\("download"\)/);
  assert.match(HARNESS, /readFileSync\(downloadPath, "utf8"\)/);
  assert.match(HARNESS, /transcript\.downloads = Object\.fromEntries/);
});

test("the run's own revision and worktree state are recorded, not assumed", () => {
  assert.match(HARNESS, /tested_commit: git\(\["rev-parse", "HEAD"\]\)/);
  assert.match(HARNESS, /worktree_dirty: status === null \? null : status\.length > 0/);
});