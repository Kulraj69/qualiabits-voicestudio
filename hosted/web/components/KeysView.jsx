"use client";

import { useState } from "react";

import { planeDetail } from "../lib/controlplane.js";

import { formatDateTime, keyPrefix } from "../lib/format.js";

/**
 * The API keys page.
 *
 * A minted key is shown exactly once — the plane stores only a digest, so this
 * component holds the plaintext in memory for one reveal and never persists it.
 * The list afterwards shows only the key's public id.
 */
export default function KeysView({ initialKeys }) {
  const [keys, setKeys] = useState(initialKeys || []);
  const [label, setLabel] = useState("");
  const [revealed, setRevealed] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function reload() {
    const response = await fetch("/api/keys");
    const data = await response.json().catch(() => ({}));
    if (response.ok) setKeys(data.api_keys || []);
  }

  async function mint(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const response = await fetch("/api/keys", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ label }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(planeDetail(data, "The plane refused the request."));
        return;
      }
      setRevealed(data.api_key_record);
      setLabel("");
      setCopied(false);
      await reload();
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id) {
    setError("");
    const response = await fetch(`/api/keys/${id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(planeDetail(data, "The plane refused the revocation."));
      return;
    }
    await reload();
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(revealed.api_key);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="stack">
      <form className="card card--pad-sm row" onSubmit={mint}>
        <label className="field field--grow">
          <span className="field__label">Label</span>
          <input
            type="text"
            data-testid="key-label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="what will use this key? e.g. laptop, server"
          />
        </label>
        <button className="btn" type="submit" data-testid="key-create" disabled={busy}>
          {busy ? "Creating…" : "Create key"}
        </button>
      </form>

      {error ? (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      ) : null}

      {revealed ? (
        <section className="card" aria-label="New key — shown once">
          <div className="card__title">
            <h2>Your new key</h2>
            <span className="badge badge--running">shown once</span>
          </div>
          <p className="card__hint">
            This is the only time the key is returned. Only a digest is stored, so it
            cannot be shown again — copy it now.
          </p>
          <div className="secret" style={{ marginTop: "var(--space-4)" }}>
            <code className="secret__value" data-testid="revealed-key">
              {revealed.api_key}
            </code>
            <button type="button" className="btn btn--ghost btn--sm" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setRevealed(null)}
            >
              Dismiss
            </button>
          </div>
        </section>
      ) : null}

      <div className="table-wrap">
        <table>
          <caption className="sr-only">Your API keys</caption>
          <thead>
            <tr>
              <th>Key id</th>
              <th>Label</th>
              <th>Created</th>
              <th>Last used</th>
              <th>Status</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <p className="empty">No keys yet. Create one above.</p>
                </td>
              </tr>
            ) : (
              keys.map((key) => (
                <tr key={key.id}>
                  <td className="mono">{keyPrefix(key.key_id)}</td>
                  <td>{key.label || <span className="subtle">unlabelled</span>}</td>
                  <td className="mono">{formatDateTime(key.created_at)}</td>
                  <td className="mono">{formatDateTime(key.last_used_at)}</td>
                  <td>
                    {key.revoked_at ? (
                      <span className="badge badge--failed">revoked</span>
                    ) : (
                      <span className="badge badge--succeeded">active</span>
                    )}
                  </td>
                  <td>
                    {key.revoked_at ? (
                      <span className="subtle" style={{ fontSize: "0.85rem" }}>
                        {formatDateTime(key.revoked_at)}
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--danger btn--sm"
                        onClick={() => revoke(key.id)}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <p className="boundary">
        The list shows only each key's public id. The secret is never stored, so a
        revocation — and a listing — can do no more than that.
      </p>
    </div>
  );
}