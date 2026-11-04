/**
 * The pure half of the Dub page.
 *
 * Everything here is a function of a plane response and nothing else — no fetch,
 * no DOM — so it can be tested without a browser, the same way the transcript
 * helpers are. The page stays thin: it calls these and renders what they return.
 */

/** The name a dubbed file downloads as. The id is the plane's, not a page counter. */
export function dubFilename(id) {
  return `dub-${id}.wav`;
}

/**
 * One line of the plane's own numbers for a finished dub.
 *
 * The ear and the mouth are reported apart, because they are the two things a
 * customer can actually spend effort on. A number the plane did not send is left
 * out rather than shown as a zero: `null` engine seconds is not "0.00s spent".
 */
export function describeDub(record) {
  if (!record) return "";
  const parts = [];
  const ear = Number(record.transcribe_seconds);
  const mouth = Number(record.speech_seconds);
  if (Number.isFinite(ear) && Number.isFinite(mouth)) {
    parts.push(`the engine spent ${ear.toFixed(2)} s hearing it and ${mouth.toFixed(2)} s saying it`);
  }
  const seconds = Number(record.audio_seconds);
  if (Number.isFinite(seconds)) parts.push(`${seconds.toFixed(2)} s of audio back`);
  const characters = Number(record.transcript_characters);
  if (Number.isFinite(characters)) parts.push(`${characters} characters heard`);
  return parts.join(" · ");
}

/**
 * Whether the transcript the engine returned is what this page will speak.
 *
 * Always true by design — the plane refuses an empty transcript rather than
 * speaking nothing — but stated as a function so a test can hold the rule down
 * and the page reads it rather than assuming it.
 */
export function hasWordsToSpeak(transcript) {
  return typeof transcript === "string" && transcript.trim().length > 0;
}