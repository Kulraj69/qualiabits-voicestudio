"use client";

import { useMemo, useRef, useState } from "react";

import { toJson, toPlainText, toSrt, transcriptFilename } from "../lib/transcript.js";
import { planeDetail } from "../lib/controlplane.js";
import SearchableSelect from "./SearchableSelect.jsx";

/**
 * The Transcribe page.
 *
 * A recording goes up, the engine's transcript comes back, and the numbers the
 * plane reports about the request — the length it measured, the characters it
 * counted, the seconds the engine spent — are shown beside the text. Every one
 * of those numbers comes from the plane's response headers, not from this page's
 * own arithmetic, so a disagreement between what the page claims and what the
 * meter recorded is not possible.
 */

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

const FORMATS = [
  { value: "json", label: "json — text, no timings" },
  { value: "verbose_json", label: "verbose_json — text with segments" },
  { value: "text", label: "text — the plain transcript only" },
  { value: "srt", label: "srt — subtitles" },
  { value: "vtt", label: "vtt — WebVTT subtitles" },
];

function formatBytes(count) {
  const value = Number(count);
  if (!Number.isFinite(value)) return "";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${value} bytes`;
}

export default function TranscribeView() {
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState("hi");
  const [responseFormat, setResponseFormat] = useState("verbose_json");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  const payload = result?.payload ?? null;
  const text = useMemo(() => toPlainText(payload), [payload]);
  const segments = useMemo(
    () => (Array.isArray(payload?.segments) ? payload.segments : []),
    [payload],
  );
  const duration = result?.seconds ?? payload?.duration ?? null;

  async function submit(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    if (!file) {
      setError("Choose a recording first.");
      return;
    }
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      body.append("response_format", responseFormat);
      if (language) body.append("language", language);
      const response = await fetch("/api/transcribe", { method: "POST", body });
      const type = response.headers.get("content-type") || "";
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setError(planeDetail(data, `The plane answered ${response.status}.`));
        setResult({
          failed: true,
          status: response.status,
          uploadBytes: response.headers.get("x-qualiabits-upload-bytes"),
          seconds: response.headers.get("x-qualiabits-audio-seconds"),
        });
        return;
      }
      const headers = {
        seconds: response.headers.get("x-qualiabits-audio-seconds"),
        characters: response.headers.get("x-qualiabits-transcript-characters"),
        uploadBytes: response.headers.get("x-qualiabits-upload-bytes"),
        engineSeconds: response.headers.get("x-qualiabits-engine-seconds"),
        engineBuild: response.headers.get("x-omnivoice-backend"),
      };
      if (type.includes("application/json")) {
        const data = await response.json();
        setResult({ ...headers, payload: data, raw: null, file });
      } else {
        // srt, vtt or text: the engine's bytes, shown as it wrote them.
        const raw = await response.text();
        setResult({ ...headers, payload: null, raw, file });
      }
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  function download(extension, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = transcriptFilename(result?.file?.name, extension);
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={submit}>
        <div className="row">
          <label className="field field--grow">
            <span className="field__label">Recording</span>
            <input
              ref={fileRef}
              data-testid="transcribe-file"
              type="file"
              accept="audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
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
            <span className="field__label">Language</span>
            {/* Searchable selects, not native ones: the repository rule
                (AGENTS.md, "Shared select controls") forbids introducing a
                select element, and both of these lists are long enough that typing is
                faster than scanning. */}
            <SearchableSelect
              options={LANGUAGES.map((item) => ({ ...item, value: item.value || "auto" }))}
              value={language || "auto"}
              onChange={(next) => setLanguage(next === "auto" ? "" : next)}
              ariaLabel="Language of the recording"
              testId="transcribe-language"
              placeholder="Let the engine decide"
              searchPlaceholder="language"
            />
          </div>

          <div className="field">
            <span className="field__label">Answer format</span>
            <SearchableSelect
              options={FORMATS}
              value={responseFormat}
              onChange={setResponseFormat}
              ariaLabel="Answer format"
              testId="transcribe-format"
              placeholder="json"
              searchPlaceholder="format"
            />
          </div>
        </div>

        <div className="row">
          <button
            className="btn"
            type="submit"
            data-testid="transcribe-submit"
            disabled={busy || !file}
          >
            {busy ? "Transcribing…" : "Transcribe"}
          </button>
          <span className="subtle" style={{ fontSize: "0.85rem" }}>
            Metered by the plane under your workspace, in characters returned and
            in the length of what you send.
          </span>
        </div>

        {error ? (
          <div className="notice notice--error" role="alert" data-testid="transcribe-error">
            <p className="notice__title">The plane refused it</p>
            <p>{error}</p>
          </div>
        ) : null}
      </form>

      {result && !result.failed ? (
        <section className="card stack" aria-label="The transcript the engine returned">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <p className="notice__title">Transcript</p>
            <span className="mono muted" style={{ fontSize: "0.8rem" }} data-testid="transcribe-meta">
              {result.seconds ? `${result.seconds} s measured · ` : "duration not measured · "}
              {result.characters ? `${result.characters} characters · ` : "characters not counted · "}
              {result.uploadBytes ? `${formatBytes(result.uploadBytes)} sent · ` : ""}
              {result.engineSeconds ? `engine took ${result.engineSeconds}s` : ""}
              {result.engineBuild ? ` · engine build ${result.engineBuild}` : ""}
            </span>
          </div>

          <pre className="transcript" data-testid="transcribe-text">
            {result.raw !== null ? result.raw : text}
          </pre>

          {segments.length > 0 ? (
            <div
              className="table-wrap"
              tabIndex={0}
              role="region"
              aria-label="The transcript's segments"
            >
            <table data-testid="transcribe-segments">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Start</th>
                  <th scope="col">End</th>
                  <th scope="col">Text</th>
                </tr>
              </thead>
              <tbody>
                {segments.map((segment, index) => (
                  <tr key={`${segment.start}-${index}`}>
                    <td>{index + 1}</td>
                    <td className="mono">{Number(segment.start ?? 0).toFixed(2)}</td>
                    <td className="mono">{Number(segment.end ?? 0).toFixed(2)}</td>
                    <td>{String(segment.text ?? "").trim()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          ) : null}

          <div className="row">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              data-testid="download-txt"
              onClick={() => download("txt", result.raw ?? text, "text/plain")}
            >
              Download .txt
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              data-testid="download-srt"
              disabled={result.raw !== null && responseFormat !== "srt"}
              onClick={() =>
                download(
                  "srt",
                  result.raw !== null ? result.raw : toSrt(payload, { duration }),
                  "application/x-subrip",
                )
              }
            >
              Download .srt
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              data-testid="download-json"
              disabled={payload === null}
              onClick={() => download("json", toJson(payload), "application/json")}
            >
              Download .json
            </button>
          </div>
          <p className="subtle" style={{ fontSize: "0.8rem" }}>
            A subtitle file built here from segments the engine timed is marked as
            such; when the engine returned no segments, the cue covers the whole
            recording and says the timing is unknown rather than inventing it.
          </p>
        </section>
      ) : null}
    </div>
  );
}