/**
 * The long-form page's own arithmetic, kept out of the component.
 *
 * Every number here is derived from what the plane reported — the chapter list and
 * its statuses, and the limits `GET /healthz` publishes. Nothing is assumed about
 * how many chapters a manuscript "should" make: the plane split it, and this file
 * only words what came back. The one exception is the refusal sentence, which is
 * written to match the plane's own wording character for character
 * (`hosted/control_plane/app.py`, the 413 in `POST /v1/projects`) because the
 * browser run compares the two.
 */

import { statusLabel } from "./format.js";

/** A project whose every chapter has been spoken: the assembled file exists. */
export const READY = "ready";

const PROJECT_WORDS = {
  ready: "Ready to download",
  queued: "Queued",
  running: "Being spoken",
  failed: "Failed",
  cancelled: "Cancelled",
  empty: "Empty",
};

/** The plane's project word, in the page's words. Unknown words pass through. */
export function projectStatusLabel(status) {
  return PROJECT_WORDS[status] || status || "Unknown";
}

/**
 * How many chapters are in each state, counted from the chapter list itself.
 *
 * The page shows the plane's own count when it has one, and this when a caller
 * only has the chapters; both are counts of the same rows, and neither invents a
 * chapter that is not in the list.
 */
export function chapterCounts(project) {
  const counts = {};
  for (const chapter of project?.chapters || []) {
    counts[chapter.status] = (counts[chapter.status] || 0) + 1;
  }
  return counts;
}

/** The chapters the plane says are done, out of the chapters it listed. */
export function spokenOf(project) {
  const chapters = project?.chapters || [];
  const spoken = chapters.filter((chapter) => chapter.status === "succeeded").length;
  return { spoken, total: chapters.length };
}

/**
 * One sentence about where the project is.
 *
 * A project with a failed chapter says how many failed rather than how many are
 * left: the reader has something to act on, and saying "3 of 5 done" would bury
 * it.
 */
export function progressSentence(project) {
  const chapters = project?.chapters || [];
  if (chapters.length === 0) return "This project has no chapters.";
  const counts = chapterCounts(project);
  const { spoken, total } = spokenOf(project);
  if (spoken === total) return `${total} of ${total} chapters spoken — the audiobook is one file.`;
  const failed = counts.failed || 0;
  if (failed > 0) {
    return `${spoken} of ${total} chapters spoken; ${failed} failed, so there is no audiobook yet.`;
  }
  const running = counts.running || 0;
  const queued = counts.queued || 0;
  const parts = [`${spoken} of ${total} chapters spoken`];
  if (running > 0) parts.push(`${running} being spoken now`);
  if (queued > 0) parts.push(`${queued} still queued`);
  const cancelled = counts.cancelled || 0;
  if (cancelled > 0) parts.push(`${cancelled} cancelled`);
  return `${parts.join("; ")}.`;
}

/** What to do next, or "" when there is nothing left to do. */
export function nextStepSentence(project) {
  const status = project?.status;
  if (status === READY) return "";
  if (status === "failed") {
    return "A failed chapter has to be queued again; the audiobook is assembled only when every chapter is done.";
  }
  if (status === "cancelled") return "Cancelled chapters are never spoken. Queue the manuscript again to finish it.";
  return "A worker process speaks the chapters in order; this page polls the plane until they are done.";
}

/** True when the assembled audiobook exists: every chapter, and nothing else. */
export function canDownload(project) {
  const chapters = project?.chapters || [];
  return chapters.length > 0 && chapters.every((chapter) => chapter.status === "succeeded");
}

/** "Chapter 3 of 12" — the position the plane gave the chapter, not a recount. */
export function chapterLabel(chapter, total) {
  const position = Number(chapter?.position) || 0;
  return `Chapter ${position} of ${total}`;
}

/** A chapter's own status word, with the engine's sentence when it failed. */
export function chapterDetail(chapter) {
  const word = statusLabel(chapter?.status);
  if (chapter?.status === "failed" && chapter?.error) return `${word} — ${chapter.error}`;
  return word;
}

/** True when the manuscript is over the ceiling the plane published. */
export function overManuscriptLimit(text, maxChars) {
  const max = Number(maxChars);
  if (!Number.isFinite(max) || max <= 0) return false;
  return String(text ?? "").trim().length > max;
}

/**
 * The plane's 413 sentence, word for word, for the manuscript that was refused.
 *
 * Written from the character count and the ceiling rather than hard-coded, so a
 * plane that publishes a different ceiling produces a different sentence and this
 * page still agrees with it.
 */
export function tooLongManuscriptSentence(characters, maxChars) {
  const max = Number(maxChars);
  const count = Number(characters);
  if (!Number.isFinite(max) || max <= 0) {
    return "'input' is longer than this project takes.";
  }
  return (
    `'input' is ${Number.isFinite(count) ? count : 0} characters and a project takes `
    + `at most ${max}. Send it as more than one project, or shorten it.`
  );
}

/**
 * How the split works, in one line, beside the composer.
 *
 * The two numbers are the plane's; when it cannot be read, the sentence says what
 * the splitter does and promises no ceiling rather than quoting one this file made
 * up.
 */
export function describeSplit(maxChars, maxChapters) {
  const ceiling = Number(maxChars);
  const count = Number(maxChapters);
  const parts = [
    "The plane splits on paragraph, sentence and word boundaries — never inside a word.",
  ];
  if (Number.isFinite(ceiling) && ceiling > 0) {
    parts.push(`No chapter is longer than ${ceiling} characters.`);
  }
  if (Number.isFinite(count) && count > 0) {
    parts.push(`A project takes at most ${count} chapters.`);
  }
  parts.push("Each chapter is queued as its own job, and the audiobook is assembled when every one is done.");
  return parts.join(" ");
}