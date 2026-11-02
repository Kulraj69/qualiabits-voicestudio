/**
 * The plane's own upload ceiling, read from the plane rather than guessed here.
 *
 * The real-engine run found this the hard way. The plane refuses an oversized
 * upload on the declared `content-length`, before it reads a byte — and it closes
 * the connection to do it. When the dashboard was already streaming 25 MiB at it,
 * that close arrived mid-write and the plane's polite 413 became an `EPIPE` in
 * the Next server: the customer saw a bare 500 with an empty body, on the very
 * path where this project promises "refused with a sentence naming the limit".
 *
 * The fix is to ask the plane what its ceiling is and refuse before sending
 * anything. The number must come from the plane: a ceiling hard-coded in the web
 * app would be a second source of truth, and the app would start refusing uploads
 * the plane would have accepted (or the reverse). `GET /healthz` publishes it.
 */

import { cpJson } from "./controlplane.js";

/**
 * The plane's upload ceiling in bytes, or null when it cannot be read.
 *
 * null is a real answer and is handled as one: the app forwards the upload and
 * lets the plane do the refusing, exactly as it did before this module existed.
 */
export async function planeUploadCeiling() {
  const { status, data } = await cpJson("/healthz");
  if (status !== 200 || !data || typeof data !== "object") return null;
  const value = Number(data.transcribe_max_bytes);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** A byte count as the plane words it, so the two sentences read the same. */
export function describeCeiling(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  const mebibyte = 1024 * 1024;
  if (value >= mebibyte) return `${Math.trunc(value / mebibyte)} MiB`;
  return `${Math.trunc(value)} bytes`;
}

/**
 * The refusal sentence, in the plane's own words for the same number.
 *
 * `hosted/control_plane/app.py` writes
 * `f"That upload is larger than {window}. An upload is at most {window}."`
 * and the browser run compares this sentence with the plane's byte for byte, so
 * the two cannot drift apart quietly.
 */
export function tooLargeSentence(bytes) {
  const window = describeCeiling(bytes);
  if (!window) return "That upload is larger than this plane accepts.";
  return `That upload is larger than ${window}. An upload is at most ${window}.`;
}