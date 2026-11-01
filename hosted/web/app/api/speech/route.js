/**
 * Speak, through the metered route, on the dashboard's behalf.
 *
 * The plane's design decision is that speaking always needs an API key, so that
 * every byte of audio is attributable to a credential. The dashboard honours
 * that: the first time a signed-in customer speaks, this server mints a key
 * labelled "dashboard", keeps the secret in an httpOnly cookie, and uses it. The
 * customer never sees the secret in page JavaScript, and the key appears in
 * their key list like any other.
 *
 * If that key is revoked on the Keys page, the plane answers 401 with its own
 * sentence ("That API key has been revoked.") and this route passes it straight
 * back — so revocation is visible on the very next request, which is what the
 * milestone asks for.
 */
import { cpFetch, cpJson } from "../../../lib/controlplane.js";
import { DASH_KEY_COOKIE, serializeCookie } from "../../../lib/cookies.js";
import { readJsonBody } from "../../../lib/proxy.js";
import { credentialCookieOptions, readDashKey, readSessionToken } from "../../../lib/session.js";

const KEY_LABEL = "dashboard";
const FORWARDED = [
  "content-type",
  "x-qualiabits-audio-seconds",
  "x-qualiabits-engine-seconds",
  "x-qualiabits-workspace",
  "x-omnivoice-backend",
];

async function mintDashboardKey(sessionToken) {
  const minted = await cpJson("/v1/api-keys", {
    method: "POST",
    token: sessionToken,
    body: { label: KEY_LABEL },
  });
  if (minted.status !== 201) return null;
  return minted.data?.api_key_record?.api_key || null;
}

export async function POST(request) {
  const sessionToken = await readSessionToken();
  if (!sessionToken) {
    return Response.json({ detail: "Sign in first." }, { status: 401 });
  }

  const body = await readJsonBody(request);
  const payload = {
    input: String(body.input ?? ""),
    voice: String(body.voice ?? ""),
    response_format: String(body.response_format ?? "wav"),
  };
  if (body.language) payload.language = String(body.language);

  let key = await readDashKey();
  let mintedKey = null;
  if (!key) {
    mintedKey = await mintDashboardKey(sessionToken);
    if (!mintedKey) {
      return Response.json(
        { detail: "The dashboard could not mint a key to speak with." },
        { status: 502 },
      );
    }
    key = mintedKey;
  }

  const response = await cpFetch("/v1/audio/speech", { method: "POST", token: key, body: payload });
  const headers = new Headers({ "content-type": response.headers.get("content-type") || "application/json" });
  for (const name of FORWARDED) {
    const value = response.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  if (mintedKey) {
    headers.set(
      "set-cookie",
      serializeCookie(DASH_KEY_COOKIE, mintedKey, credentialCookieOptions()),
    );
  }

  if (!response.ok) {
    // The plane's own refusal, passed through as it wrote it. A revoked key is
    // reported as revoked so the page can offer to rotate.
    const text = await response.text();
    headers.set("content-type", "application/json");
    headers.set("x-qualiabits-key-state", response.status === 401 ? "rejected" : "ok");
    return new Response(text || JSON.stringify({ detail: "The engine did not answer." }), {
      status: response.status,
      headers,
    });
  }

  const bytes = await response.arrayBuffer();
  return new Response(bytes, { status: response.status, headers });
}