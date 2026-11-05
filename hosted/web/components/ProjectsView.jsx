"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { formatBytes, formatDateTime, formatSeconds, isTerminal, statusLabel } from "../lib/format.js";
import {
  canDownload,
  chapterDetail,
  chapterLabel,
  describeSplit,
  nextStepSentence,
  overManuscriptLimit,
  progressSentence,
  projectStatusLabel,
  spokenOf,
  tooLongManuscriptSentence,
} from "../lib/projects.js";
import { defaultVoiceId, groupVoices, KIND_LABELS } from "../lib/voices.js";
import { planeDetail } from "../lib/controlplane.js";
import SearchableSelect from "./SearchableSelect.jsx";

/** The dashboard's own class for a project's word, reusing the queue's badge set. */
const BADGE = {
  ready: "succeeded",
  queued: "queued",
  running: "running",
  failed: "failed",
  cancelled: "cancelled",
};

/**
 * Long-form work: a manuscript in, one audiobook out.
 *
 * The queue page answers "speak this paragraph". A book is longer than the queue
 * takes and longer than one HTTP request should wait for, so this page is the same
 * queue one level up: the plane splits the manuscript into chapters, queues each
 * one as a job, a worker speaks them, and the plane joins the stored chapters into
 * a single file that this page offers when — and only when — every chapter is done.
 *
 * Three things this page refuses to do, because each of them is how a page like
 * this starts lying:
 *
 * - It does not split the manuscript itself. The chapters shown are the plane's
 *   chapters, read back from the plane, and the composer's own over-length refusal
 *   quotes the plane's ceiling, which is read from the plane's `/healthz`.
 * - It does not claim an audiobook exists before every chapter is `succeeded`. The
 *   download is offered on that condition alone, and the plane answers 409 with the
 *   chapters that are still waiting for anyone who asks anyway.
 * - It does not invent progress. The sentence beside each project is counted from
 *   the chapter statuses the plane reported.
 */
export default function ProjectsView({ catalogue, initialProjects, limits }) {
  const voices = catalogue?.voices ?? [];
  const groups = useMemo(() => groupVoices(voices), [voices]);
  const voiceChoices = useMemo(
    () => Object.entries(groups).flatMap(([kind, list]) =>
      list.map((item) => ({
        value: item.id,
        group: KIND_LABELS[kind] || kind,
        label: `${item.name}${item.default ? " (engine default)" : ""}`,
      }))),
    [groups],
  );

  const manuscriptMax = Number(limits?.project_max_chars) || 0;
  const [projects, setProjects] = useState(initialProjects || []);
  const [title, setTitle] = useState("");
  const [text, setText] = useState(
    "पहला पैराग्राफ: यहाँ पहली अध्याय की शुरुआत लिखें।\n\n"
      + "दूसरा पैराग्राफ: और यहाँ दूसरी।\n\n"
      + "तीसरा पैराग्राफ: यहाँ किताब समाप्त होती है।",
  );
  const [voice, setVoice] = useState(defaultVoiceId(voices));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const overLimit = overManuscriptLimit(text, manuscriptMax);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/projects?limit=20");
      if (!response.ok) return;
      const data = await response.json();
      setProjects(data.projects || []);
    } catch {
      /* a poll that failed is not worth a banner; the next one will do */
    }
  }, []);

  // Poll only while something is still moving: a page that keeps asking the plane
  // for finished work is noise in the plane's log and on the customer's bill of
  // attention. `isTerminal` is the queue's own rule, reused rather than restated.
  const anythingMoving = projects.some((project) =>
    (project.chapters || []).some((chapter) => !isTerminal(chapter.status)),
  );
  useEffect(() => {
    if (!anythingMoving) return undefined;
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [anythingMoving, refresh]);

  async function create(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (overLimit) {
      setError(tooLongManuscriptSentence(text.trim().length, manuscriptMax));
      return;
    }
    setBusy(true);
    try {
      const response = await fetch("/api/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title, input: text, voice, response_format: "wav" }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(planeDetail(data, "The plane refused to queue the manuscript."));
        return;
      }
      const chapters = data.project?.chapters?.length ?? 0;
      setNotice(
        `${chapters} chapter${chapters === 1 ? "" : "s"} queued as ${chapters} `
          + "job(s) — nothing has been spoken yet. A worker speaks them in order.",
      );
      await refresh();
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  async function cancel(id) {
    setError("");
    const response = await fetch(`/api/projects/${id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(planeDetail(data, "The plane refused the cancellation."));
      return;
    }
    setNotice(`Cancelled ${data.cancelled_jobs?.length ?? 0} queued chapter(s). Spoken chapters are kept.`);
    await refresh();
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={create}>
        <label className="field">
          <span className="field__label">Title (optional)</span>
          <input
            data-testid="project-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={120}
            placeholder="What this book is called"
          />
        </label>
        <label className="field">
          <span className="field__label">The manuscript</span>
          <textarea
            data-testid="project-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={8}
          />
          <span className="field__hint" data-testid="project-hint">
            {text.length} characters
            {manuscriptMax > 0 ? ` · the plane takes up to ${manuscriptMax} per project` : ""}
            {" · "}
            {describeSplit(manuscriptMax, Number(limits?.chapter_max_count) || 0)}
          </span>
        </label>
        <div className="row">
          <div className="field field--grow">
            <span className="field__label">Voice for every chapter</span>
            {/* The package's own searchable control, not a native select
                (AGENTS.md, "Shared select controls"). */}
            <SearchableSelect
              options={voiceChoices}
              value={voice}
              onChange={setVoice}
              ariaLabel="Voice for the whole manuscript"
              testId="project-voice"
              placeholder="Pick a voice"
              searchPlaceholder="voice id or name"
            />
          </div>
          <button
            className="btn"
            type="submit"
            data-testid="project-create"
            disabled={busy || !text.trim() || overLimit}
          >
            {busy ? "Queueing…" : "Split and queue"}
          </button>
        </div>

        {error ? (
          <p className="notice notice--error" role="alert" data-testid="project-error">
            {error}
          </p>
        ) : null}
        {overLimit ? (
          // The refusal is shown before the button is pressed, in the plane's own
          // words for the plane's own ceiling, so the customer is never told a
          // different limit by the page than the one the plane enforces.
          <p className="notice notice--error" role="alert" data-testid="project-over">
            {tooLongManuscriptSentence(text.trim().length, manuscriptMax)}
          </p>
        ) : null}
        {notice ? (
          <p className="notice notice--ok" role="status" data-testid="project-notice">
            {notice}
          </p>
        ) : null}
      </form>

      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ fontSize: "1.05rem" }}>Your projects</h2>
        <button type="button" className="btn btn--ghost btn--sm" onClick={refresh}>
          Refresh
        </button>
      </div>

      {projects.length === 0 ? (
        <p className="empty">No manuscripts queued yet.</p>
      ) : (
        projects.map((project) => {
          const { spoken, total } = spokenOf(project);
          const ready = canDownload(project);
          return (
            <section className="card stack" key={project.id} data-testid={`project-${project.id}`}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                <div>
                  <p className="card__title">
                    {project.title || "Untitled manuscript"}{" "}
                    <span className="subtle mono" style={{ fontSize: "0.8rem" }}>#{project.id}</span>
                  </p>
                  <p className="card__hint" data-testid={`project-progress-${project.id}`}>
                    {progressSentence(project)}
                  </p>
                </div>
                <span
                  className={`badge badge--${BADGE[project.status] || "queued"}`}
                  data-testid={`project-status-${project.id}`}
                >
                  {projectStatusLabel(project.status)}
                </span>
              </div>

              {total > 0 ? (
                <div className="bar" role="img" aria-label={`${spoken} of ${total} chapters spoken`}>
                  <div className="bar__fill" style={{ width: `${Math.round((spoken / total) * 100)}%` }} />
                </div>
              ) : null}

              <p className="subtle" style={{ fontSize: "0.85rem" }} data-testid={`project-next-${project.id}`}>
                {nextStepSentence(project)}
              </p>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Chapter</th>
                      <th>Status</th>
                      <th className="num">Chars</th>
                      <th>Audio</th>
                      <th>Opens with</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(project.chapters || []).map((chapter) => (
                      <tr key={chapter.index}>
                        <td className="mono">{chapterLabel(chapter, total)}</td>
                        <td>
                          <span className={`badge badge--${chapter.status}`}>{statusLabel(chapter.status)}</span>
                        </td>
                        <td className="num">{chapter.characters}</td>
                        <td className="mono" style={{ fontSize: "0.85rem" }}>
                          {chapter.audio_seconds != null ? formatSeconds(chapter.audio_seconds) : "—"}
                          {chapter.audio_bytes != null ? ` · ${formatBytes(chapter.audio_bytes)} B` : ""}
                        </td>
                        <td title={chapterDetail(chapter)} style={{ fontSize: "0.85rem" }}>
                          {chapter.status === "failed" && chapter.error
                            ? chapter.error.slice(0, 60)
                            : chapter.preview}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="subtle" style={{ fontSize: "0.82rem" }}>
                  queued {formatDateTime(project.created_at)} · voice {project.voice || "engine default"}
                </span>
                <div className="row" style={{ gap: "var(--space-2)" }}>
                  {ready ? (
                    <a
                      className="btn btn--sm"
                      href={`/api/projects/${project.id}/audio`}
                      download={`project-${project.id}.wav`}
                      data-testid={`project-audio-${project.id}`}
                    >
                      Download the audiobook
                    </a>
                  ) : null}
                  {project.status === "queued" || project.status === "running" ? (
                    <button
                      type="button"
                      className="btn btn--danger btn--sm"
                      onClick={() => cancel(project.id)}
                      data-testid={`project-cancel-${project.id}`}
                    >
                      Cancel the unspoken chapters
                    </button>
                  ) : null}
                </div>
              </div>
            </section>
          );
        })
      )}
    </div>
  );
}