/**
 * Sign in: email + password to the plane, session token into an httpOnly cookie.
 */
import { cpJson, planeDetail } from "../../../../lib/controlplane.js";
import {
  SESSION_COOKIE,
  WORKSPACE_COOKIE,
  encodeWorkspace,
  serializeCookie,
} from "../../../../lib/cookies.js";
import { credentialCookieOptions } from "../../../../lib/session.js";
import { readJsonBody } from "../../../../lib/proxy.js";

export async function POST(request) {
  const body = await readJsonBody(request);
  const email = String(body.email ?? "").trim();
  const password = String(body.password ?? "");

  const login = await cpJson("/v1/auth/token", { method: "POST", body: { email, password } });
  if (login.status !== 200) {
    return Response.json(
      { detail: planeDetail(login.data, "That email and password did not match.") },
      { status: login.status || 401 },
    );
  }
  const token = login.data?.token || "";
  const workspace = login.data?.workspace ?? {};
  if (!token) {
    return Response.json({ detail: "The control plane returned no session." }, { status: 502 });
  }

  const opts = credentialCookieOptions();
  const headers = new Headers({ "content-type": "application/json" });
  headers.append("set-cookie", serializeCookie(SESSION_COOKIE, token, opts));
  headers.append(
    "set-cookie",
    serializeCookie(WORKSPACE_COOKIE, encodeWorkspace(workspace), { ...opts, httpOnly: false }),
  );
  return new Response(JSON.stringify({ workspace, signed_in: true }), { status: 200, headers });
}