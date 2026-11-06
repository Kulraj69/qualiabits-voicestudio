import { redirect } from "next/navigation";

import LimitsView from "../../components/LimitsView.jsx";
import { cpJson } from "../../lib/controlplane.js";
import { formatBytes, formatCount, formatDateTime, formatSeconds, keyPrefix } from "../../lib/format.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Usage — QualiaBits Voice" };

export default async function UsagePage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const { status, data } = await cpJson("/v1/usage", { token });
  if (status === 401) redirect("/login");
  const usage = status === 200 ? data?.usage ?? null : null;
  const readVia = data?.read_via;
  // Slice G: the same page shows the limits the meter is measured against, read
  // from the plane rather than computed here, so the two cannot disagree.
  const limitRead = await cpJson("/v1/limits", { token });
  const limits = limitRead.status === 200 ? limitRead.data?.limits ?? null : null;

  if (!usage) {
    return (
      <>
        <div className="page-head">
          <p className="page-head__eyebrow">The meter</p>
          <h1>Usage</h1>
        </div>
        <p className="notice notice--error">The plane did not return a meter.</p>
      </>
    );
  }

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">The meter</p>
        <h1>Usage</h1>
        <p className="page-head__lede">
          These are the control plane's own records for this workspace, read via{" "}
          <code className="mono">{readVia || "session"}</code>. The three counters mean
          different things on purpose; each column says which.
        </p>
      </div>

      <section className="grid grid--stats" aria-label="Workspace totals">
        <div className="stat">
          <p className="stat__label">Requests</p>
          <p className="stat__value">{formatCount(usage.requests)}</p>
          <p className="stat__note">including refusals</p>
        </div>
        <div className="stat">
          <p className="stat__label">Characters</p>
          <p className="stat__value">{formatCount(usage.characters)}</p>
          <p className="stat__note">{usage.characters_note}</p>
        </div>
        <div className="stat">
          <p className="stat__label">Characters transcribed</p>
          <p className="stat__value" data-testid="stat-transcript-characters">
            {formatCount(usage.transcript_characters)}
          </p>
          <p className="stat__note">{usage.transcript_characters_note}</p>
        </div>
        <div className="stat">
          <p className="stat__label">Audio returned or sent</p>
          <p className="stat__value">{formatSeconds(usage.audio_seconds)}</p>
          <p className="stat__note">{usage.audio_seconds_note}</p>
        </div>
        <div className="stat">
          <p className="stat__label">Audio bytes</p>
          <p className="stat__value">{formatBytes(usage.audio_bytes)}</p>
          <p className="stat__note">what the plane handed back, or what you sent in</p>
        </div>
        <div className="stat">
          <p className="stat__label">Failed requests</p>
          <p className="stat__value">{formatCount(usage.failed_requests)}</p>
          <p className="stat__note">status 400 and above</p>
        </div>
        <div className="stat">
          <p className="stat__label">Without audio seconds</p>
          <p className="stat__value">{formatCount(usage.requests_without_audio_seconds)}</p>
          <p className="stat__note">no WAV to measure — not counted as zero</p>
        </div>
      </section>

      <section className="stack" style={{ marginTop: "var(--space-6)" }}>
        <div className="card--pad-sm">
          <div className="card__title">
            <h2>By key</h2>
          </div>
          <p className="card__hint">{usage.by_key_note}</p>
          <div
            className="table-wrap"
            style={{ marginTop: "var(--space-4)" }}
            tabIndex={0}
            role="region"
            aria-label="Usage by key"
          >
            <table>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Label</th>
                  <th className="num">Requests</th>
                  <th className="num">Characters</th>
                  <th className="num">Transcribed</th>
                  <th className="num">Audio (s)</th>
                  <th className="num">Failed</th>
                  <th>Last used</th>
                </tr>
              </thead>
              <tbody>
                {(usage.by_key || []).length === 0 ? (
                  <tr>
                    <td colSpan={8}>
                      <p className="empty">
                        No metered requests yet. Speaking on the Speak page mints a
                        dashboard key and this table fills in.
                      </p>
                    </td>
                  </tr>
                ) : (
                  usage.by_key.map((row) => (
                    <tr key={row.api_key_id}>
                      <td className="mono">{row.key_id ? keyPrefix(row.key_id) : "—"}</td>
                      <td>{row.label || <span className="subtle">unlabelled</span>}</td>
                      <td className="num">{formatCount(row.requests)}</td>
                      <td className="num">{formatCount(row.characters)}</td>
                      <td className="num">{formatCount(row.transcript_characters)}</td>
                      <td className="num">{formatSeconds(row.audio_seconds)}</td>
                      <td className="num">{formatCount(row.failed_requests)}</td>
                      <td className="mono">{formatDateTime(row.last_request_at)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card--pad-sm">
          <div className="card__title">
            <h2>By status</h2>
          </div>
          <div
            className="table-wrap"
            style={{ marginTop: "var(--space-4)" }}
            tabIndex={0}
            role="region"
            aria-label="Usage by status"
          >
            <table>
              <thead>
                <tr>
                  <th>Status code</th>
                  <th className="num">Requests</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(usage.by_status || {}).map(([code, count]) => (
                  <tr key={code}>
                    <td className="mono">{code}</td>
                    <td className="num">{formatCount(count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="boundary" style={{ marginTop: "var(--space-4)" }}>
            First request {formatDateTime(usage.first_request_at)} · last{" "}
            {formatDateTime(usage.last_request_at)}.
          </p>
        </div>
      </section>

      <LimitsView initialLimits={limits} />
    </>
  );
}