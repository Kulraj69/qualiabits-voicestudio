/**
 * Presentation-only helpers. Kept pure so the way a number is shown can be
 * tested without rendering a page.
 *
 * Nothing here computes a truth the plane owns. `formatSeconds` shows the
 * seconds the plane measured from a WAV header; when the plane did not measure
 * one it shows the dash it was given, never a zero standing in for "unknown".
 */

/** A duration the plane measured. `null`/`undefined` becomes an em dash. */
export function formatSeconds(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n !== 0 && Math.abs(n) < 1) return `${n.toFixed(3)} s`;
  return `${n.toFixed(3).replace(/\.?0+$/, "")} s`;
}

/** A byte count, thousands-separated; em dash when unknown. */
export function formatBytes(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US");
}

/** A whole count; em dash when unknown. */
export function formatCount(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US");
}

/** The public prefix of a key id, so a list never shows usable material. */
export function keyPrefix(keyId) {
  if (typeof keyId !== "string" || !keyId) return "—";
  return `qbv_${keyId}`;
}

/** A short, readable label for a job status. */
export function statusLabel(status) {
  const map = {
    queued: "Queued",
    running: "Running",
    succeeded: "Done",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return map[status] || (status ? String(status) : "Unknown");
}

/** Whether a status is terminal (no further transition will come). */
export function isTerminal(status) {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/** An absolute time from an ISO string the plane wrote; em dash when absent. */
export function formatDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}