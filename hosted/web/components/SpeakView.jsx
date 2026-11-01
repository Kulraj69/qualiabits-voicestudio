"use client";

import { useMemo, useRef, useState } from "react";

import { defaultVoiceId, groupVoices, KIND_LABELS, languageOptions } from "../lib/voices.js";

/** ISO 639-1 codes offered as completions. The engine may still ignore one. */
const LANGUAGE_CODES = ["en", "hi", "es", "fr", "de", "it", "pt", "ru", "ja", "zh", "ko", "ar"];

/**
 * The Speak page.
 *
 * The voice list arrives from the server component (which read it from the
 * plane) — nothing here is hard-coded. Pressing Speak posts to the app's own
 * route, which mints/uses a dashboard key and takes the audio from the plane's
 * metered route. The engine's own refusal sentence is shown verbatim if the
 * voice is not one it can speak.
 */
export default function SpeakView({ catalogue }) {
  const voices = catalogue?.voices ?? [];
  const languages = useMemo(() => languageOptions(voices), [voices]);
  const [languageFilter, setLanguageFilter] = useState("all");
  const groups = useMemo(
    () => groupVoices(voices, { language: languageFilter }),
    [voices, languageFilter],
  );

  const [text, setText] = useState("The dashboard speaks through the control plane.");
  const [voice, setVoice] = useState(defaultVoiceId(voices));
  const [language, setLanguage] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [keyRejected, setKeyRejected] = useState(false);
  const playerRef = useRef(null);

  async function speak(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    setBusy(true);
    try {
      const body = { input: text, voice, response_format: "wav" };
      if (language && language !== "auto") body.language = language;
      const response = await fetch("/api/speech", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        setKeyRejected(response.headers.get("x-qualiabits-key-state") === "rejected");
        const data = await response.json().catch(() => ({}));
        setError(data?.detail || `The plane answered ${response.status}.`);
        return;
      }
      setKeyRejected(false);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      setResult({
        url,
        bytes: blob.size,
        seconds: response.headers.get("x-qualiabits-audio-seconds"),
        engineSeconds: response.headers.get("x-qualiabits-engine-seconds"),
        backend: response.headers.get("x-omnivoice-backend"),
        voice,
      });
      requestAnimationFrame(() => playerRef.current?.play?.().catch(() => {}));
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  async function rotateKey() {
    await fetch("/api/dashboard-key", { method: "POST" });
    setKeyRejected(false);
    setError("A fresh dashboard key will be minted on the next Speak.");
  }

  if (voices.length === 0) {
    return (
      <div className="stack">
        <p className="page-head__lede">{catalogue?.engine?.reachable === false
          ? "The engine behind the control plane is not reachable, so there are no voices to offer. Start the engine and reload."
          : "The control plane reported no voices."}</p>
      </div>
    );
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={speak}>
        <div className="row">
          <label className="field field--grow">
            <span className="field__label">Voice (from the engine's catalogue)</span>
            <select
              data-testid="voice-select"
              value={voice}
              onChange={(event) => setVoice(event.target.value)}
            >
              {Object.entries(groups).map(([kind, list]) =>
                list.length === 0 ? null : (
                  <optgroup key={kind} label={KIND_LABELS[kind] || kind}>
                    {list.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                        {item.language ? ` — ${item.language}` : ""}
                        {item.default ? " (engine default)" : ""}
                      </option>
                    ))}
                  </optgroup>
                ),
              )}
            </select>
          </label>

          {languages.length > 0 ? (
            <label className="field">
              <span className="field__label">Filter by language</span>
              <select
                value={languageFilter}
                onChange={(event) => setLanguageFilter(event.target.value)}
              >
                <option value="all">All languages</option>
                {languages.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <label className="field">
            <span className="field__label">Language</span>
            <input
              type="text"
              data-testid="speak-language"
              list="language-codes"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
              placeholder="auto"
            />
            <datalist id="language-codes">
              {LANGUAGE_CODES.map((code) => (
                <option key={code} value={code} />
              ))}
            </datalist>
            <span className="field__hint">"auto" lets the voice decide.</span>
          </label>
        </div>

        <label className="field">
          <span className="field__label">Text to speak</span>
          <textarea
            data-testid="speak-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={5}
            placeholder="Type a sentence or two…"
          />
          <span className="field__hint">{text.length} characters</span>
        </label>

        <div className="row">
          <button className="btn" type="submit" data-testid="speak-submit" disabled={busy || !text.trim()}>
            {busy ? "Speaking…" : "Speak"}
          </button>
          <span className="subtle" style={{ fontSize: "0.85rem" }}>
            Metered by the plane under your workspace.
          </span>
        </div>

        {error ? (
          <div className="notice notice--error" role="alert">
            <p className="notice__title">The plane refused it</p>
            <p>{error}</p>
            {keyRejected ? (
              <p style={{ marginTop: "var(--space-2)" }}>
                <button type="button" className="linkish" onClick={rotateKey}>
                  Mint a fresh dashboard key
                </button>
              </p>
            ) : null}
          </div>
        ) : null}
      </form>

      {result ? (
        <section className="player" aria-label="The audio the engine returned">
          <div>
            <p className="notice__title">Spoken: {result.voice}</p>
            <p className="muted mono" style={{ fontSize: "0.85rem" }}>
              {result.seconds ? `${result.seconds} s of audio` : "duration not measured"} ·{" "}
              {result.bytes.toLocaleString("en-US")} bytes
              {result.engineSeconds ? ` · engine took ${result.engineSeconds}s` : ""}
              {result.backend ? ` · ${result.backend}` : ""}
            </p>
          </div>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <audio ref={playerRef} src={result.url} controls />
          <div className="row">
            <a className="btn btn--ghost btn--sm" href={result.url} download={`speech-${result.voice}.wav`}>
              Download WAV
            </a>
          </div>
        </section>
      ) : null}
    </div>
  );
}