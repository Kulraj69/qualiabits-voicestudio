/**
 * Forget the dashboard's key so the next Speak mints a fresh one.
 *
 * Only the cookie is cleared. The old key stays in the customer's key list —
 * the plane is the record, and this app does not reach into it to tidy up.
 */
import { DASH_KEY_COOKIE, clearCookie } from "../../../lib/cookies.js";
import { credentialCookieOptions } from "../../../lib/session.js";

export async function POST() {
  const headers = new Headers({ "content-type": "application/json" });
  headers.append("set-cookie", clearCookie(DASH_KEY_COOKIE, credentialCookieOptions()));
  return new Response(JSON.stringify({ rotated: true }), { status: 200, headers });
}