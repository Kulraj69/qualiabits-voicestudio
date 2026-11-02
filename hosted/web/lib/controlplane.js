/**
 * The one place the web app knows the control plane's address.
 *
 * Everything else in the app goes through this module, so the address lives in
 * one environment variable (`QUALI_CP_BASE_URL`) and there is no second code
 * path that reaches the plane. The app is a client of the plane exactly as a
 * customer's server would be: it signs up, logs in, and holds a token — it does
 * not open the plane's database and imports nothing from `hosted/control_plane`.
 */

const DEFAULT_BASE = "http://127.0.0.1:8080";

/** The control-plane base URL, trailing slashes removed. */
export function cpBaseUrl(env = process.env) {
  const raw = (env && env.QUALI_CP_BASE_URL) || DEFAULT_BASE;
  return String(raw).trim().replace(/\/+$/, "");
}

/** Join a path onto the base URL. `path` must start with a slash. */
export function cpUrl(path, env = process.env) {
  const base = cpBaseUrl(env);
  if (!path.startsWith("/")) path = `/${path}`;
  return base + path;
}

/**
 * One request to the plane.
 *
 * `token` becomes `Authorization: Bearer *** when given. This is the only
 * place a credential is attached, so a route cannot forget to and accidentally
 * call the plane unauthenticated — the caller either passes a token or gets the
 * plane's own 401 back.
 *
 * A `FormData` or `Blob` body is passed through untouched rather than
 * stringified: the transcript upload is multipart, and letting `fetch` set that
 * body's own `content-type` is the only way the boundary matches the bytes.
 */
export async function cpFetch(path, { method = "GET", token, body, headers = {}, signal } = {}) {
  const init = { method, cache: "no-store", headers: { ...headers }, signal };
  if (token) init.headers.Authorization = `Bearer ${token}`;
  if (body !== undefined && body !== null) {
    const opaque = isOpaqueBody(body);
    init.body = opaque ? body : (typeof body === "string" ? body : JSON.stringify(body));
    if (!opaque && !init.headers["content-type"] && !init.headers["Content-Type"]) {
      init.headers["Content-Type"] = "application/json";
    }
  }
  return fetch(cpUrl(path), init);
}

/** A body that carries its own content type: multipart form data, or raw bytes. */
export function isOpaqueBody(body) {
  if (typeof FormData !== "undefined" && body instanceof FormData) return true;
  if (typeof Blob !== "undefined" && body instanceof Blob) return true;
  return false;
}

/** Read a plane response as `{ status, ok, data }` without throwing on bad JSON. */
export async function cpJson(path, options) {
  const response = await cpFetch(path, options);
  let data = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text.slice(0, 500) };
    }
  }
  return { status: response.status, ok: response.ok, data, response };
}

/** A short human sentence from a plane error body, for a page to show verbatim. */
export function planeDetail(payload, fallback = "The control plane refused the request.") {
  if (!payload) return fallback;
  const detail = payload.detail ?? payload.error;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    if (typeof detail.detail === "string" && detail.detail.trim()) return detail.detail.trim();
    return JSON.stringify(detail);
  }
  return fallback;
}