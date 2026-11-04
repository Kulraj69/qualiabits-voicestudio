"use client";

import { useMemo, useState } from "react";

import { describeDub, dubFilename, hasWordsToSpeak } from "../lib/dubbing.js";
import { defaultVoiceId, groupVoices, KIND_LABELS } from "../lib/voices.js";
import SearchableSelect from "./SearchableSelect.jsx";

/**
 * The Dub page.
 *
 * A recording goes up. The plane hears it, then says the same words back in the
 * voice chosen here, and this page shows both halves side by side: the engine's
 * own transcript, and the audio it produced from it. The transcript is shown
 * *because* it is unedited — a dub says what was heard, mistakes included, and a
 * page that hid the words would be selling a fidelity that was never claimed.
 *
 * Two things this page will not do. It will not translate: there is no
 * translation model in this product, so the dubbed file is the same language it
 * heard, and the copy says so rather than implying otherwise. And it will not
 * offer a voice the plane did not report: the picker is built from the plane's
 * catalogue, exactly as the Speak page's is.
 */
function formatBytes(count) {
  const value = Number(count);
  if (!Number.isFinite(value)) return "";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${value} bytes`;
}

/** The engine's own STT option names; the plane drops anything else. */
const LANGUAGES = [
  { value: "", label: "Let the engine decide" },
  { value: "hi", label: "Hindi (hi)" },
  { value: "en", label: "English (en)" },
  { value: "hinglish", label: "Hinglish" },
  { value: "pa", label: "Punjabi (pa)" },
  { value: "ta", label: "Tamil (ta)" },
  { value: "bn", label: "Bengali (bn)" },
  { value: "mr", label: "Marathi (mr)" },
];

export default function DubView({ catalogue, initialDubs }) {
  const voices = catalogue?.voices ?? [];
  const groups = useMemo(() => groupVoices(voices), [voices]);
  const voiceChoices = useMemo(
    () =>
      Object.entries(groups).flatMap(([kind, list]) =>
        list.map((item) => ({
          value: item.id,
          group: KIND_LABELS[kind] || kind,
          label: `${item.name}${item.default ? " (engine default)" : ""}`,
        })),
      ),
    [groups],
  );

  const [file, setFile] = useState(null);
  const [voice, setVoice] = useState(defaultVoiceId(voices));
  const [language, setLanguage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [dubs, setDubs] = useState(initialDubs || []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    if (!file) {
      setError("Choose a recording first.");
      return;
    }
    if (!voice) {
      setError("Pick a voice the plane offers.");
      return;
    }
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      body.append("voice", voice);
      if (language) body.append("language", language);
      const response = await fetch("/api/dubs", { method: "POST", body });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(data?.detail || `The plane answered ${response.status}.`);
        return;
      }
      const record = data.dub;
      setResult(record);
      setDubs((current) => [record, ...current]);
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={submit}>
        <div className="row">
          <label className="field field--grow">
            <span className="field__label">Recording to dub</span>
            <input
              data-testid="dub-file"
              type="file"
              accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setResult(null);
                setError("");
              }}
            />
            <span className="field__hint">
              {file
                ? `${file.name} — ${formatBytes(file.size)}`
                : "A WAV from the Speak page works; so does a phone recording."}
            </span>
          </label>

          <div className="field">
            <span className="field__label">Voice to say it in</span>
            {/* The package's own searchable control, not a native select
                (AGENTS.md, "Shared select controls"), fed by the plane's list. */}
            <SearchableSelect
              options={voiceChoices}
              value={voice}
              onChange={setVoice}
              ariaLabel="Voice the recording is dubbed into"
              testId="dub-voice"
              placeholder="Pick a voice"
              searchPlaceholder="voice id or name"
            />
          </div>

          <div className="field">
            <span className="field__label">Language of the recording</span>
            <SearchableSelect
              options={LANGUAGES.map((item) => ({ ...item, value: item.value || "auto" }))}
              value={language || "auto"}
              onChange={(next) => setLanguage(next === "auto" ? "" : next)}
              ariaLabel="Language the recording is heard in"
              testId="dub-language"
              placeholder="Let the engine decide"
              searchPlaceholder="language"
            />
          </div>
        </div>

        <div className="row">
          <button
            className="btn"
            type="submit"
            data-testid="dub-submit"
            disabled={busy || !file || !voice}
          >
            {busy ? "Dubbing…" : "Dub"}
          </button>
          <span className="subtle" style={{ fontSize: "0.85rem" }}>
            The words are not translated — they are heard again, in another voice. The
            transcript below is the engine&apos;s own, unedited.
          </span>
        </div>

        {error ? (
          <div className="notice notice--error" role="alert" data-testid="dub-error">
            <p className="notice__title">The plane refused it</p>
            <p>{error}</p>
          </div>
        ) : null}
      </form>

      {result && hasWordsToSpeak(result.transcript) ? (
        <section className="card stack" aria-label="The dub the plane produced">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <p className="notice__title">
              Dubbed in {result.voice} — id {result.id}
            </p>
            <span
              className="mono muted"
              style={{ fontSize: "0.8rem" }}
              data-testid="dub-meta"
            >
              {describeDub(result)}
            </span>
          </div>

          <p className="field__label">What the engine heard</p>
          <pre className="transcript" data-testid="dub-transcript">
            {result.transcript}
          </pre>

          <div className="player">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <audio src={`/api/dubs/${result.id}/audio`} controls />
            <div className="row">
              <a
                className="btn btn--ghost btn--sm"
                data-testid="dub-download"
                href={`/api/dubs/${result.id}/audio`}
                download={dubFilename(result.id)}
              >
                Download WAV
              </a>
            </div>
          </div>
        </section>
      ) : null}

      {dubs.length > 0 ? (
        <section className="card stack" aria-label="Dubs this workspace has made">
          <p className="notice__title">Your dubs</p>
          <div className="table-wrap">
            <table data-testid="dub-list">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Voice</th>
                  <th scope="col">What it heard</th>
                  <th scope="col">Audio</th>
                </tr>
              </thead>
              <tbody>
                {dubs.map((row) => (
                  <tr key={row.id}>
                    <td className="mono">{row.id}</td>
                    <td>{row.voice}</td>
                    <td>
                      {String(row.transcript ?? "").slice(0, 60)}
                      {String(row.transcript ?? "").length > 60 ? "…" : ""}
                    </td>
                    <td>
                      <a href={`/api/dubs/${row.id}/audio`} download={dubFilename(row.id)}>
                        WAV
                      </a>
                      {Number.isFinite(Number(row.audio_seconds))
                        ? ` · ${Number(row.audio_seconds).toFixed(2)} s`
                        : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}