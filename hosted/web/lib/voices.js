/**
 * How a voice list is presented, kept pure and separate from how it is fetched.
 *
 * The plane answers `GET /v1/voices` with a composed list — the engine's own
 * voices first, then the OpenAI aliases and the saved profiles — and says which
 * kind each one is. The page must never invent a voice: everything the picker
 * offers arrives from the plane. This module only sorts, groups and filters what
 * arrived, so "the list is served by the plane" stays true even after the page
 * has rearranged it.
 */

export const ENGINE_KIND = "engine";

/** The languages the plane actually named, in a stable order, deduplicated. */
export function languageOptions(voices) {
  const seen = new Set();
  for (const voice of voices || []) {
    const lang = voice && voice.language;
    if (typeof lang === "string" && lang.trim()) seen.add(lang.trim());
  }
  return [...seen].sort();
}

/**
 * Filter by language, then group by kind.
 *
 * `language` of "" or "all" keeps every voice. A voice whose language the plane
 * did not name is only offered under "all" — it is never guessed into a bucket.
 */
export function groupVoices(voices, { language = "all" } = {}) {
  const wanted = (language || "all").trim().toLowerCase();
  const kept = (voices || []).filter((voice) => {
    if (wanted === "" || wanted === "all") return true;
    return String(voice.language || "").trim().toLowerCase() === wanted;
  });
  const groups = { engine: [], alias: [], profile: [], other: [] };
  for (const voice of kept) {
    const kind = groups[voice.kind] ? voice.kind : "other";
    groups[kind].push(voice);
  }
  return groups;
}

/** The voice that should be selected first: the engine's default, else the first. */
export function defaultVoiceId(voices) {
  const list = voices || [];
  const marked = list.find((voice) => voice && voice.default);
  if (marked) return marked.id;
  const engine = list.find((voice) => voice && voice.kind === ENGINE_KIND);
  return engine ? engine.id : list[0]?.id ?? "";
}

/** A one-line description of the composed list, for a heading. */
export function describeCatalogue(catalogue) {
  if (!catalogue) return "The engine did not answer; no voices to offer.";
  const count = Number(catalogue.count) || 0;
  if (count === 0) {
    return catalogue.engine && catalogue.engine.reachable === false
      ? "The engine is not reachable, so there are no voices to offer."
      : "The engine reported no voices.";
  }
  const kinds = catalogue.kinds || {};
  const parts = [];
  if (kinds.engine) parts.push(`${kinds.engine} from the engine`);
  if (kinds.alias) parts.push(`${kinds.alias} OpenAI ${kinds.alias === 1 ? "alias" : "aliases"}`);
  if (kinds.profile) parts.push(`${kinds.profile} saved ${kinds.profile === 1 ? "profile" : "profiles"}`);
  const engineName = catalogue.engine && catalogue.engine.display_name;
  const who = engineName ? `the active engine (${engineName})` : "the engine";
  return `${count} ${count === 1 ? "voice" : "voices"} from ${who}: ${parts.join(", ")}.`;
}

/** Group headings, in the order the picker shows them. */
export const KIND_LABELS = {
  engine: "Engine voices",
  alias: "OpenAI aliases",
  profile: "Saved profiles",
  other: "Other",
};

/**
 * The one sentence every preview speaks.
 *
 * Fixed on purpose: a library that previews each voice with different words
 * would be reporting how two sentences sound, not how two voices do. It is
 * English, which is a real limit for the Hindi voices in this catalogue, and the
 * page says so where the sentence is shown rather than here.
 */
export const PREVIEW_TEXT =
  "This is a preview of one voice from the engine's own catalogue.";

/**
 * The voices a search and a kind keep, in the order the plane sent them.
 *
 * The search reads the fields the plane filled — id, name and language — and
 * nothing else, so it cannot match a voice on something the plane never said.
 * Case is folded and whitespace trimmed; an empty query keeps everything. This
 * narrows and reorders nothing: it is a filter, so the result is always a subset
 * of what arrived, which is what keeps "the list comes from the plane" true.
 */
export function filterVoices(voices, { query = "", kind = "all" } = {}) {
  const needle = String(query ?? "").trim().toLowerCase();
  const wantedKind = String(kind ?? "all").trim().toLowerCase();
  return (voices || []).filter((voice) => {
    if (!voice || typeof voice !== "object") return false;
    if (wantedKind !== "" && wantedKind !== "all") {
      const voiceKind = String(voice.kind || "other").trim().toLowerCase();
      if (voiceKind !== wantedKind) return false;
    }
    if (!needle) return true;
    return [voice.id, voice.name, voice.language].some(
      (field) => typeof field === "string" && field.toLowerCase().includes(needle),
    );
  });
}

/**
 * How many voices of each kind the plane reported, in the picker's own order.
 *
 * `groupVoices` answers the same question the moment it groups; this one answers
 * it without filtering anything, which is what a "N of M" line needs.
 */
export function kindCounts(voices) {
  const counts = { engine: 0, alias: 0, profile: 0, other: 0 };
  for (const voice of voices || []) {
    const kind = voice && counts[voice.kind] !== undefined ? voice.kind : "other";
    counts[kind] += 1;
  }
  return counts;
}