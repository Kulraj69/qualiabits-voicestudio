"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatBytes, formatDateTime, formatSeconds, statusLabel, isTerminal } from "../lib/format.js";
import { defaultVoiceId, groupVoices, KIND_LABELS } from "../lib/voices.js";
import { planeDetail } from "../lib/controlplane.js";
import SearchableSelect from "./SearchableSelect.jsx";

/**
 * The Jobs page.
 *
 * Jobs are the plane's queue: a paragraph is handed over, a worker process
 * claims it and stores the audio, and the page watches the status until it is
 * done. The list is polled from the plane's own records — it does not invent a
 * status local to the browser.
 */
export default function JobsView({ catalogue, initialJobs }) {
  const voices = catalogue?.voices ?? [];
  const groups = useMemo(() => groupVoices(voices), [voices]);
  // One row per voice the plane reported, grouped by the kind it reported. The
  // group name is display only; the value on every row is a voice id.
  const voiceChoices = useMemo(
    () => Object.entries(groups).flatMap(([kind, list]) =>
      list.map((item) => ({
        value: item.id,
        group: KIND_LABELS[kind] || kind,
        label: `${item.name}${item.default ? " (engine default)" : ""}`,
      }))),
    [groups],
  );
  const [jobs, setJobs] = useState(initialJobs || []);
  const [text, setText] = useState(
    "This paragraph is queued rather than spoken in one request. A worker process claims it, asks the engine, and stores the audio, which this page then plays back.",
  );
  const [voice, setVoice] = useState(defaultVoiceId(voices));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const playing = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/jobs?limit=50");
      if (!response.ok) return;
      const data = await response.json();
      setJobs(data.jobs || []);
    } catch {
      /* a poll that failed is not worth a banner; the next one will do */
    }
  }, []);

  useEffect(() => {
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, [refresh]);

  async function queue(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ input: text, voice, response_format: "wav" }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(planeDetail(data, "The plane refused to queue it."));
        return;
      }
      setNotice(
        `Job ${data.job?.id} queued — nothing has been spoken yet. A worker will pick it up.`,
      );
      await refresh();
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  async function cancel(id) {
    const response = await fetch(`/api/jobs/${id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(planeDetail(data, "The plane refused the cancellation."));
      return;
    }
    await refresh();
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={queue}>
        <label className="field">
          <span className="field__label">A paragraph to speak</span>
          <textarea
            data-testid="job-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={4}
          />
          <span className="field__hint">{text.length} characters · the queue takes up to 20,000</span>
        </label>
        <div className="row">
          <div className="field field--grow">
            <span className="field__label">Voice</span>
            {/* A searchable select, not a native one: the repository rule
                (AGENTS.md, "Shared select controls"). The rows are the voices the
                plane reported, grouped by the kind it reported them under. */}
            <SearchableSelect
              options={voiceChoices}
              value={voice}
              onChange={setVoice}
              ariaLabel="Voice for the queued job"
              testId="job-voice"
              placeholder="Pick a voice"
              searchPlaceholder="voice id or name"
            />
          </div>
          <button className="btn" type="submit" data-testid="job-queue" disabled={busy || !text.trim()}>
            {busy ? "Queueing…" : "Queue job"}
          </button>
        </div>

        {error ? (
          <p className="notice notice--error" role="alert">
            {error}
          </p>
        ) : null}
        {notice ? (
          <p className="notice notice--ok" role="status">
            {notice}
          </p>
        ) : null}
      </form>

      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ fontSize: "1.05rem" }}>Your jobs</h2>
        <button type="button" className="btn btn--ghost btn--sm" onClick={refresh}>
          Refresh
        </button>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Status</th>
              <th>Voice</th>
              <th className="num">Chars</th>
              <th>Created</th>
              <th>Result</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={7}>
                  <p className="empty">Nothing queued yet.</p>
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id}>
                  <td className="mono">#{job.id}</td>
                  <td>
                    <span className={`badge badge--${job.status}`}>{statusLabel(job.status)}</span>
                  </td>
                  <td className="mono">{job.voice || "—"}</td>
                  <td className="num">{job.characters}</td>
                  <td className="mono">{formatDateTime(job.created_at)}</td>
                  <td>
                    {job.status === "succeeded" && job.audio ? (
                      <span className="mono" style={{ fontSize: "0.85rem" }}>
                        {formatSeconds(job.audio.seconds)} · {formatBytes(job.audio.bytes)} B
                      </span>
                    ) : job.status === "failed" ? (
                      <span className="badge badge--failed" title={job.error}>
                        {job.error ? job.error.slice(0, 60) : "failed"}
                      </span>
                    ) : job.status === "cancelled" ? (
                      <span className="subtle" style={{ fontSize: "0.85rem" }}>
                        never spoken
                      </span>
                    ) : (
                      <span className="subtle" style={{ fontSize: "0.85rem" }}>
                        waiting…
                      </span>
                    )}
                  </td>
                  <td>
                    <div className="row" style={{ gap: "var(--space-2)", justifyContent: "flex-end" }}>
                      {job.status === "succeeded" ? (
                        <>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() => {
                              const audio = playing.current;
                              if (audio) {
                                audio.src = `/api/jobs/${job.id}/audio`;
                                audio.dataset.job = String(job.id);
                                audio.play().catch(() => {});
                              }
                            }}
                          >
                            Play
                          </button>
                          <a
                            className="btn btn--ghost btn--sm"
                            href={`/api/jobs/${job.id}/audio`}
                            download={`job-${job.id}.wav`}
                          >
                            Download
                          </a>
                        </>
                      ) : job.status === "queued" ? (
                        <button
                          type="button"
                          className="btn btn--danger btn--sm"
                          onClick={() => cancel(job.id)}
                        >
                          Cancel
                        </button>
                      ) : isTerminal(job.status) ? (
                        <span className="subtle">—</span>
                      ) : (
                        <span className="subtle">—</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio ref={playing} controls hidden />
      <p className="boundary">
        Cancelling a queued job costs nothing: nothing was sent to the engine, so the
        meter does not move.
      </p>
    </div>
  );
}