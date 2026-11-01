/**
 * Serialising the dashboard's cookies, kept pure so it can be unit-tested
 * without a server or a browser in the way.
 *
 * The dashboard holds exactly three cookies, and which one is httpOnly is a
 * security decision, not a detail:
 *
 *   qb_session   the control-plane session token. httpOnly: browser JavaScript
 *                never sees it, so an XSS on a dashboard page cannot lift the
 *                credential. This is the whole reason the app proxies rather
 *                than letting the page call the plane directly.
 *   qb_dash_key  the dashboard's own API key secret, minted by the server. Also
 *                httpOnly, for the same reason.
 *   qb_ws        the workspace's public fields (id, email, display name). Not a
 *                secret; readable by JavaScript purely so the header can render
 *                the signed-in name without another round-trip.
 */

export const SESSION_COOKIE = "qb_session";
export const DASH_KEY_COOKIE = "qb_dash_key";
export const WORKSPACE_COOKIE = "qb_ws";

/**
 * Serialise one `Set-Cookie` value.
 *
 * `httpOnly` defaults to true because a cookie that carries a credential must
 * opt *out* of it, not forget to opt in.
 */
export function serializeCookie(name, value, options = {}) {
  const {
    path = "/",
    httpOnly = true,
    sameSite = "Lax",
    secure = false,
    maxAge,
    expires,
  } = options;
  const parts = [`${name}=${value ?? ""}`];
  parts.push(`Path=${path}`);
  if (maxAge !== undefined && maxAge !== null) parts.push(`Max-Age=${Math.floor(maxAge)}`);
  if (expires) parts.push(`Expires=${expires.toUTCString()}`);
  if (sameSite) parts.push(`SameSite=${sameSite}`);
  if (httpOnly) parts.push("HttpOnly");
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

/** A cookie header that clears `name`. Used on sign-out and key rotation. */
export function clearCookie(name, options = {}) {
  return serializeCookie(name, "", { ...options, maxAge: 0 });
}

/** Read the workspace fields the dashboard is allowed to keep in a cookie. */
export function decodeWorkspace(raw) {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (parsed.id === undefined || typeof parsed.id !== "number") return null;
    return {
      id: parsed.id,
      email: typeof parsed.email === "string" ? parsed.email : "",
      display_name: typeof parsed.display_name === "string" ? parsed.display_name : "",
    };
  } catch {
    return null;
  }
}

/** The value stored in the workspace cookie, from the plane's signup shape. */
export function encodeWorkspace(workspace) {
  return JSON.stringify({
    id: workspace.id,
    email: workspace.email || "",
    display_name: workspace.display_name || "",
  });
}