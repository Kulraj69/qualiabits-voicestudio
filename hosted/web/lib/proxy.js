/**
 * The shared proxy behind every `/api/*` route.
 *
 * The browser talks to the Next server; the Next server talks to the control
 * plane with the session token it holds in an httpOnly cookie. That indirection
 * is the point: the token and any API key secret never reach page JavaScript,
 * and there is exactly one place that attaches the credential.
 */
import { cpFetch } from "./controlplane.js";
import { readSessionToken } from "./session.js";

/** The list of plane response headers a dashboard caller is allowed to see. */
const FORWARDED = [
  "content-type",
  "x-qualiabits-job",
  "x-qualiabits-audio-seconds",
  "x-qualiabits-engine-seconds",
  "x-qualiabits-object-sha256",
  "x-qualiabits-workspace",
  "x-omnivoice-backend",
];

function forwardedHeaders(from) {
  const out = {};
  for (const name of FORWARDED) {
    const value = from.get(name);
    if (value !== null) out[name] = value;
  }
  if (!out["content-type"]) out["content-type"] = "application/json";
  return out;
}

function unauthorized(detail = "Sign in first.") {
  return new Response(JSON.stringify({ detail }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Forward a JSON call to the plane, attaching the session token.
 *
 * The plane's body and status are passed through untouched, so a page shows the
 * plane's own sentence rather than a rewrite of it.
 */
export async function proxyJson(path, { method = "GET", body } = {}) {
  const token = await readSessionToken();
  if (!token) return unauthorized();
  const response = await cpFetch(path, { method, token, body });
  const text = await response.text();
  return new Response(text, {
    status: response.status,
    headers: forwardedHeaders(response.headers),
  });
}

/**
 * Forward a call whose response is bytes (a WAV). Nothing is parsed or resized.
 */
export async function proxyBytes(path, { method = "GET" } = {}) {
  const token = await readSessionToken();
  if (!token) return unauthorized();
  const response = await cpFetch(path, { method, token });
  const buffer = await response.arrayBuffer();
  return new Response(buffer, {
    status: response.status,
    headers: forwardedHeaders(response.headers),
  });
}

/** Read and JSON-decode a request body, tolerating an empty or bad body. */
export async function readJsonBody(request) {
  try {
    const value = await request.json();
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}