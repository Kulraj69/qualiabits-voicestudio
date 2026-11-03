/**
 * The long-form page's own arithmetic.
 *
 * These functions decide what the Projects page says about a manuscript, so what is
 * checked here is that every sentence is counted from the plane's chapter list and
 * that nothing is claimed before it is true — in particular that the audiobook is
 * offered only when every chapter is done, and that the over-length refusal quotes
 * the ceiling the plane published.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  canDownload,
  chapterCounts,
  chapterDetail,
  chapterLabel,
  describeSplit,
  nextStepSentence,
  overManuscriptLimit,
  progressSentence,
  projectStatusLabel,
  spokenOf,
  tooLongManuscriptSentence,
} from "../lib/projects.js";

const chapter = (index, status, extra = {}) => ({
  index,
  position: index + 1,
  status,
  characters: 100,
  job_id: index + 1,
  error: "",
  audio_seconds: status === "succeeded" ? 1.5 : null,
  audio_bytes: status === "succeeded" ? 70000 : null,
  audio_url: status === "succeeded" ? `/v1/jobs/${index + 1}/audio` : null,
  preview: `chapter ${index + 1} preview`,
  ...extra,
});

const project = (statuses, extra = {}) => ({
  id: 7,
  title: "किताब",
  status: "queued",
  chapter_count: statuses.length,
  chapters: statuses.map((status, index) => chapter(index, status)),
  ...extra,
});

test("the chapter statuses are counted from the chapters the plane sent", () => {
  const counts = chapterCounts(project(["succeeded", "succeeded", "queued", "failed"]));
  assert.deepEqual(counts, { succeeded: 2, queued: 1, failed: 1 });
});

test("spoken chapters are counted, never guessed from the project's word", () => {
  const { spoken, total } = spokenOf(project(["succeeded", "queued", "running"]));
  assert.equal(spoken, 1);
  assert.equal(total, 3);
});

test("a project with nothing spoken says so rather than showing a bar full of work", () => {
  assert.equal(spokenOf(project([])).spoken, 0);
  assert.equal(progressSentence(project([])), "This project has no chapters.");
});

test("a finished project reads as one file, with the plane's own chapter count", () => {
  assert.equal(
    progressSentence(project(["succeeded", "succeeded", "succeeded"])),
    "3 of 3 chapters spoken — the audiobook is one file.",
  );
});

test("a project still queued says how many are left", () => {
  assert.equal(
    progressSentence(project(["succeeded", "queued", "queued"])),
    "1 of 3 chapters spoken; 2 still queued.",
  );
});

test("a failed chapter is named rather than buried in the progress count", () => {
  const sentence = progressSentence(project(["succeeded", "failed", "queued"]));
  assert.match(sentence, /1 failed/);
  assert.match(sentence, /no audiobook yet/);
});

test("a cancelled chapter is disclosed, not silently dropped from the total", () => {
  assert.equal(
    progressSentence(project(["succeeded", "cancelled"])),
    "1 of 2 chapters spoken; 1 cancelled.",
  );
});

test("the audiobook is offered only when every chapter succeeded", () => {
  assert.equal(canDownload(project(["succeeded", "succeeded"])), true);
  assert.equal(canDownload(project(["succeeded", "queued"])), false);
  assert.equal(canDownload(project(["succeeded", "failed"])), false);
  assert.equal(canDownload(project(["succeeded", "cancelled"])), false);
  assert.equal(canDownload(project([])), false, "no chapters is not a finished audiobook");
});

test("a finished project has no next step to state", () => {
  assert.equal(nextStepSentence(project(["succeeded", "succeeded"], { status: "ready" })), "");
});

test("a queued project names the worker rather than promising audio", () => {
  const sentence = nextStepSentence(project(["queued", "queued"]));
  assert.match(sentence, /worker process/);
  assert.doesNotMatch(sentence, /ready|download/i);
});

test("a failed project says what has to happen next", () => {
  assert.match(nextStepSentence(project(["failed"], { status: "failed" })), /queued again/);
});

test("the project's word is the plane's, in the page's words", () => {
  assert.equal(projectStatusLabel("ready"), "Ready to download");
  assert.equal(projectStatusLabel("queued"), "Queued");
  assert.equal(projectStatusLabel("running"), "Being spoken");
  assert.equal(projectStatusLabel("something-new"), "something-new");
});

test("a chapter is named by the position the plane gave it", () => {
  assert.equal(chapterLabel({ position: 3 }, 12), "Chapter 3 of 12");
  assert.equal(chapterLabel({}, 2), "Chapter 0 of 2");
});

test("a failed chapter carries the engine's own sentence", () => {
  const detail = chapterDetail(chapter(0, "failed", { error: "unknown voice hf_alpha" }));
  assert.match(detail, /^Failed — unknown voice hf_alpha$/);
  assert.equal(chapterDetail(chapter(1, "queued")), "Queued");
  assert.equal(chapterDetail(chapter(1, "succeeded")), "Done");
});

test("the manuscript limit is the plane's number, and an unreadable one refuses nothing", () => {
  assert.equal(overManuscriptLimit("x".repeat(11), 10), true);
  assert.equal(overManuscriptLimit("x".repeat(10), 10), false);
  assert.equal(overManuscriptLimit("  short  ", 10), false, "whitespace is trimmed first");
  assert.equal(overManuscriptLimit("x".repeat(5000), null), false);
  assert.equal(overManuscriptLimit("x".repeat(5000), 0), false);
});

test("the refusal sentence is the plane's own wording for the plane's own number", () => {
  // hosted/control_plane/app.py, the 413 in POST /v1/projects.
  assert.equal(
    tooLongManuscriptSentence(180001, 180000),
    "'input' is 180001 characters and a project takes at most 180000. "
      + "Send it as more than one project, or shorten it.",
  );
  assert.equal(
    tooLongManuscriptSentence(900, null),
    "'input' is longer than this project takes.",
  );
});

test("the split description quotes the plane's limits and promises no others", () => {
  const described = describeSplit(5000, 120);
  assert.match(described, /never inside a word/);
  assert.match(described, /5000 characters/);
  assert.match(described, /120 chapters/);
  const unknown = describeSplit(null, null);
  assert.match(unknown, /never inside a word/);
  assert.doesNotMatch(unknown, /\d/, "no number is invented when the plane did not publish one");
});