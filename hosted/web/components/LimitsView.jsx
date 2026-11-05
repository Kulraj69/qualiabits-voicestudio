"use client";

import { useState } from "react";

import { planeDetail } from "../lib/controlplane.js";
import { formatCount, formatDateTime } from "../lib/format.js";
import { limitsFormBody } from "../lib/quota.js";

/**
 * The spend guard: this workspace's character ceiling and request rate.
 *
 * Two decisions are visible in the markup rather than buried in prose. A limit
 * that is not set says "none" and not "0", because a zero ceiling and an unset
 * ceiling are different products. And the number shown as spent is the plane's
 * own settled figure, with the reservations still standing shown apart from it —
 * a customer looking at this while a job is running should be able to see why
 * "remaining" is smaller than the ceiling minus what has finished.
 *
 * The form sends only the fields it is asked to change: the plane treats an
 * absent field as "leave alone" and an explicit null as "clear", so pressing
 * Save on one input cannot wipe the other.
 */
export default function LimitsView({ initialLimits }) {
  const [state, setState] = useState(initialLimits || null);
  const [ceiling, setCeiling] = useState(
    initialLimits?.character_ceiling ? String(initialLimits.character_ceiling) : "",
  );
  const [rate, setRate] = useState(
    initialLimits?.requests_per_minute ? String(initialLimits.requests_per_minute) : "",
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(body) {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const response = await fetch("/api/limits", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(planeDetail(data, "The plane refused the change."));
        return;
      }
      setState(data.limits);
      setNotice(`Changed: ${(data.changed || []).join(", ")}.`);
    } catch {
      setError("The dashboard could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  function save(event) {
    event.preventDefault();
    const { body, error: problem } = limitsFormBody({ ceiling, rate });
    if (problem) {
      setError(problem);
      return;
    }
    return send(body);
  }

  return (
    <section className="panel" aria-label="Limits" data-testid="limits">
      <div className="panel__head">
        <h2>Limits</h2>
        <p className="panel__note">
          A ceiling on what this workspace may ask the engine to speak in a UTC
          calendar month, and a rate on each API key. They are yours to set;
          nothing here is a bill.
        </p>
      </div>

      <div className="grid grid--stats">
        <div className="stat">
          <p className="stat__label">Character ceiling</p>
          <p className="stat__value" data-testid="limit-ceiling">
            {state?.character_ceiling == null ? "none" : formatCount(state.character_ceiling)}
          </p>
          <p className="stat__note">
            {state?.character_ceiling == null
              ? "no ceiling is set for this workspace"
              : `resets ${formatDateTime(state.period_resets_at)}`}
          </p>
        </div>
        <div className="stat">
          <p className="stat__label">Characters charged</p>
          <p className="stat__value" data-testid="limit-used">{formatCount(state?.characters_settled ?? 0)}</p>
          <p className="stat__note">from the meter, not a second count</p>
        </div>
        <div className="stat">
          <p className="stat__label">Remaining</p>
          <p className="stat__value" data-testid="limit-remaining">
            {state?.character_ceiling == null ? "—" : formatCount(state?.characters_remaining ?? 0)}
          </p>
          <p className="stat__note">
            {(state?.characters_reserved ?? 0) > 0
              ? `${formatCount(state.characters_reserved)} reserved by requests in flight`
              : "nothing reserved by a request in flight"}
          </p>
        </div>
        <div className="stat">
          <p className="stat__label">Rate per key</p>
          <p className="stat__value" data-testid="limit-rate">
            {state?.requests_per_minute == null
              ? "none"
              : `${formatCount(state.requests_per_minute)}/min`}
          </p>
          <p className="stat__note">
            fixed one-minute window; a refused request spends its slot and costs
            no characters
          </p>
        </div>
      </div>

      <form className="form-row" onSubmit={save} data-testid="limits-form">
        <label>
          <span>Character ceiling</span>
          <input
            inputMode="numeric"
            value={ceiling}
            placeholder="none"
            aria-label="Character ceiling"
            onChange={(event) => setCeiling(event.target.value)}
          />
        </label>
        <label>
          <span>Requests per minute, per key</span>
          <input
            inputMode="numeric"
            value={rate}
            placeholder="none"
            aria-label="Requests per minute"
            onChange={(event) => setRate(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save limits"}
        </button>
      </form>
      <p className="muted">
        An empty box means no limit. A limit bites the next request; characters
        already charged this month are not unspent by lowering it.
      </p>
      {notice ? <p className="notice">{notice}</p> : null}
      {error ? <p className="notice notice--error">{error}</p> : null}
    </section>
  );
}