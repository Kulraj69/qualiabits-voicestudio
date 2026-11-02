"use client";

import { useMemo, useState } from "react";

import { filterVoices, KIND_LABELS, kindCounts, PREVIEW_TEXT } from "../lib/voices.js";

/**
 * The Voice Library.
 *
 * The Speak page answers one question — what should this sentence sound like —
 * and answers it with a dropdown. This page answers the one before it: what is in
 * the catalogue at all, and what does each voice sound like next to the others.
 *
 * Two rules hold it together.
 *
 * Nothing is invented. Every card is a voice the plane reported, and the search
 * and the kind filter only narrow that list, so a card on this page is a voice
 * the engine will accept. There is no sample audio bundled here, no "popular"
 * ordering, no ratings: the only thing a card can be pressed for is a real
 * synthesis through the plane.
 *
 * Every preview is the same sentence, on purpose, and every preview is metered
 * under the same workspace as everything else. That is what makes two voices
 * comparable and what makes the request attributable.
 */
export default function VoiceLibraryView({ catalogue }) {
  const voices = catalogue?.voices ?? [];
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [busyVoice, setBusyVoice] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const counts = useMemo(() => kindCounts(voices), [voices]);
  const shown = useMemo(() => filterVoices(voices, { query, kind }), [voices, query, kind]);

  async function preview(voice) {
    setBusyVoice(voice);
    setError("");
    setResult(null);
    try {
      const response = await fetch("/api/speech", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ input: PREVIEW_TEXT, voice, response_format: "wav" }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setError(data?.detail || `The plane answered ${response.status}.`);
        return;
      }
      const blob = await response.blob();
      setResult({
        voice,
        url: URL.createObjectURL(blob),
        bytes: blob.size,
        seconds: response.headers.get("x-qualiabits-audio-seconds"),
        engineSeconds: response.headers.get("x-qualiabits-engine-seconds"),
      });
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusyVoice("");
    }
  }

  if (voices.length === 0) {
    return (
      <div className="stack">
        <p className="page-head__lede">
          {catalogue?.engine?.reachable === false
            ? "The engine behind the control plane is not reachable, so there are no voices to browse. Start the engine and reload."
            : "The control plane reported no voices."}
        </p>
      </div>
    );
  }

  const otherKinds = Object.entries(counts).filter(
    ([key, count]) => key !== "other" && count > 0,
  );

  return (
    <div className="stack">
      <form className="card stack" onSubmit={(event) => event.preventDefault()}>
        <div className="row">
          <label className="field field--grow">
            <span className="field__label">Search the catalogue</span>
            <input
              type="search"
              data-testid="voice-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="id, name or language — for example hf_ or hi"
            />
            <span className="field__hint">
              Searches what the plane sent and nothing else.
            </span>
          </label>
          {otherKinds.length > 1 ? (
            <label className="field">
              <span className="field__label">Kind</span>
              <select
                data-testid="voice-kind"
                value={kind}
                onChange={(event) => setKind(event.target.value)}
              >
                <option value="all">All kinds ({voices.length})</option>
                {otherKinds.map(([key, count]) => (
                  <option key={key} value={key}>
                    {KIND_LABELS[key] || key} ({count})
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>

        <p className="subtle" data-testid="voice-count">
          {shown.length} of {voices.length} voices. Every card here is a voice the
          engine reported to the plane; this page adds none.
        </p>
      </form>

      <section className="card card--pad-sm">
        <p className="card__title">What a preview proves, and what it does not</p>
        <p className="card__hint">
          Every preview speaks the same sentence, so two voices can be compared:{" "}
          <span className="mono">&ldquo;{PREVIEW_TEXT}&rdquo;</span> It is English,
          which is a limit for a catalogue that holds Hindi voices too. Pressing
          Preview is a real synthesis, metered under your workspace exactly like
          the Speak page. It proves that bytes came back, how long they are, and
          that the plane counted them. It does not say a voice sounds good: nobody
          here has judged that.
        </p>
      </section>

      {error ? (
        <div className="notice notice--error" role="alert">
          <p className="notice__title">The plane refused it</p>
          <p>{error}</p>
        </div>
      ) : null}

      {result ? (
        <section className="player" data-testid="voice-preview-player" aria-label="The audio the engine returned">
          <div>
            <p className="notice__title">Previewing: {result.voice}</p>
            <p className="muted mono" style={{ fontSize: "0.85rem" }}>
              {result.seconds ? `${result.seconds} s of audio` : "duration not measured"} ·{" "}
              {result.bytes.toLocaleString("en-US")} bytes
              {result.engineSeconds ? ` · engine took ${result.engineSeconds}s` : ""}
            </p>
          </div>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <audio key={result.url} src={result.url} controls autoPlay />
          <div className="row">
            <a className="btn btn--ghost btn--sm" href={result.url} download={`preview-${result.voice}.wav`}>
              Download WAV
            </a>
          </div>
        </section>
      ) : null}

      {shown.length === 0 ? (
        <p className="muted">
          No voice the plane reported matches that search. Clear it to see all{" "}
          {voices.length}.
        </p>
      ) : (
        <div className="grid grid--cards" data-testid="voice-grid">
          {shown.map((voice) => (
            <article className="card card--pad-sm" key={voice.id} data-voice-id={voice.id}>
              <p className="mono" style={{ wordBreak: "break-all" }}>
                {voice.id}
              </p>
              <p className="card__hint">
                <span className="badge badge--engine">{KIND_LABELS[voice.kind] || voice.kind}</span>
                {voice.default ? <span className="badge">engine default</span> : null}
                {voice.language ? <span className="badge">{voice.language}</span> : null}
              </p>
              {voice.name && voice.name !== voice.id ? (
                <p className="card__hint">The plane calls it {voice.name}.</p>
              ) : null}
              <div className="row">
                <button
                  type="button"
                  className="btn btn--sm"
                  data-testid="voice-preview"
                  disabled={busyVoice !== ""}
                  onClick={() => preview(voice.id)}
                >
                  {busyVoice === voice.id ? "Speaking…" : "Preview"}
                </button>
                <a
                  className="btn btn--ghost btn--sm"
                  data-testid="voice-use"
                  href={`/speak?voice=${encodeURIComponent(voice.id)}`}
                >
                  Use in Speak
                </a>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}