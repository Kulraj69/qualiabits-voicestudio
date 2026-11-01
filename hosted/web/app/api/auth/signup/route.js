/**
 * Sign up, then sign in, in one step, so the first screen after signup is a
 * working dashboard rather than a second form.
 *
 * Both calls go to the control plane. The session token the plane returns is
 * written straight into an httpOnly cookie by this server; it is never sent to
 * the browser in a body a page script could read.
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
  const display_name = String(body.display_name ?? "").trim();

  const signup = await cpJson("/v1/workspaces", {
    method: "POST",
    body: { email, password, display_name },
  });
  if (signup.status !== 201) {
    return Response.json(
      { detail: planeDetail(signup.data, "Signup failed.") },
      { status: signup.status || 400 },
    );
  }

  const login = await cpJson("/v1/auth/token", {
    method: "POST",
    body: { email, password },
  });
  if (login.status !== 200) {
    return Response.json(
      { detail: planeDetail(login.data, "Signed up, but signing in failed.") },
      { status: login.status || 500 },
    );
  }

  const token = login.data?.token || "";
  const workspace = login.data?.workspace ?? signup.data?.workspace ?? {};
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