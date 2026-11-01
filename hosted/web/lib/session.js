/**
 * Server-side reading and writing of the dashboard's cookies.
 *
 * This module imports `next/headers`, so it only runs on the server. Any route
 * or page that needs the session token reads it here; the browser never does,
 * because the token only ever lives in an httpOnly cookie.
 */
import { cookies } from "next/headers";

import {
  DASH_KEY_COOKIE,
  SESSION_COOKIE,
  WORKSPACE_COOKIE,
  decodeWorkspace,
} from "./cookies.js";

/** Cookie options shared by every credential cookie the app sets. */
export function credentialCookieOptions(env = process.env) {
  return {
    httpOnly: true,
    sameSite: "Lax",
    path: "/",
    // Local only: the dashboard is served over http on loopback, so `Secure`
    // would make the browser drop the cookie. In a real deployment this is
    // `NODE_ENV === "production"`.
    secure: env.NODE_ENV === "production",
  };
}

/** The session token, or "" when there is none. */
export async function readSessionToken() {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value || "";
}

/** The dashboard's own API key secret, or "" when it has not been minted yet. */
export async function readDashKey() {
  const jar = await cookies();
  return jar.get(DASH_KEY_COOKIE)?.value || "";
}

/** The public workspace fields, or null when signed out. */
export async function readWorkspace() {
  const jar = await cookies();
  return decodeWorkspace(jar.get(WORKSPACE_COOKIE)?.value || "");
}