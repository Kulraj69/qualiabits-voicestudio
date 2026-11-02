/**
 * Turning a transcript into the files a person actually wants.
 *
 * Pure functions, no DOM: the page calls them to build download blobs, and the
 * unit tests call them with the engine's own shapes. The engine answers in
 * OpenAI's shape — a `text` field and, for `verbose_json`, `segments` with
 * `start`/`end` — and nothing here invents fields the answer did not have.
 */

/** `1.25` → `00:00:01,250`. Hours are omitted below an hour, as SRT wants. */
export function srtTimestamp(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "00:00:00,000";
  const total = Math.floor(value);
  const millis = Math.round((value - total) * 1000);
  const carry = millis === 1000 ? 1 : 0;
  const whole = total + carry;
  const ms = carry ? 0 : millis;
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const pad = (n, width = 2) => String(n).padStart(width, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)},${pad(ms, 3)}`;
}

/** The plain transcript, with no timestamps: what somebody would paste. */
export function toPlainText(payload) {
  if (!payload) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload.text === "string") return payload.text;
  return "";
}

/**
 * SubRip from the engine's segments.
 *
 * When the engine returned no segments — `response_format: json` is the usual
 * reason — a single cue covering the whole recording is written from the text
 * and the duration. That is honest: it says the timing is unknown rather than
 * fabricating per-sentence times. With no duration either, the cue ends at zero
 * and the file still opens.
 */
export function toSrt(payload, { duration = null } = {}) {
  const segments = Array.isArray(payload?.segments) ? payload.segments : [];
  const rows = [];
  if (segments.length > 0) {
    segments.forEach((segment, index) => {
      rows.push(
        [
          String(index + 1),
          `${srtTimestamp(segment.start)} --> ${srtTimestamp(segment.end)}`,
          String(segment.text ?? "").trim(),
          "",
        ].join("\n"),
      );
    });
  } else {
    const text = toPlainText(payload).trim();
    if (text) {
      const end = Number.isFinite(Number(duration)) ? Number(duration) : 0;
      rows.push(`1\n${srtTimestamp(0)} --> ${srtTimestamp(end)}\n${text}\n`);
    }
  }
  return rows.length ? `${rows.join("\n")}\n` : "";
}

/** The engine's answer, pretty-printed, for a client that wants the structure. */
export function toJson(payload) {
  return JSON.stringify(payload ?? {}, null, 2);
}

/** A filename that says what it is and where it came from. */
export function transcriptFilename(originalName, extension) {
  // The last path segment first: a browser's `File.name` is a bare name, but this
  // also has to behave for anything else that reaches it, so a directory part is
  // dropped rather than sanitised into dashes.
  const leaf = String(originalName || "recording").split(/[/\\]/).pop() || "recording";
  const base = leaf
    .replace(/\.[^.]+$/, "")
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    // A name that begins with a dot or a dash is a hidden file, or a shell
    // argument somebody's script will read as a flag. Neither is wanted here, and
    // an upload's own name is caller-supplied input.
    .replace(/^[.\-]+/, "")
    .replace(/[.\-]+$/, "")
    .slice(0, 60);
  const safe = /[A-Za-z0-9]/.test(base) ? base : "recording";
  return `${safe}.transcript.${extension}`;
}