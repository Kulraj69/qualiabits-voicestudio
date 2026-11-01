/**
 * Sign out: forget the cookies.
 *
 * The plane's session row is left to expire on its own; there is no route to
 * revoke a single session, and inventing one here would be a change to the
 * plane's surface that this slice did not make.
 */
import {
  DASH_KEY_COOKIE,
  SESSION_COOKIE,
  WORKSPACE_COOKIE,
  clearCookie,
} from "../../../../lib/cookies.js";
import { credentialCookieOptions } from "../../../../lib/session.js";

export async function POST() {
  const opts = credentialCookieOptions();
  const headers = new Headers({ "content-type": "application/json" });
  for (const name of [SESSION_COOKIE, DASH_KEY_COOKIE, WORKSPACE_COOKIE]) {
    headers.append("set-cookie", clearCookie(name, opts));
  }
  return new Response(JSON.stringify({ signed_in: false }), { status: 200, headers });
}