/**
 * Dub, through the metered route, on the dashboard's behalf.
 *
 * Two plane rules meet on this route and both are honoured here. Speaking needs
 * an API key, and dubbing produces audio — so, exactly as `/api/speech` does, this
 * server mints a "dashboard" key the first time and keeps its secret in an
 * httpOnly cookie; a dub is therefore attributable to a key like any other
 * request that makes audio. And an upload is bounded before it is parsed: the
 * ceiling is read from the plane's own `/healthz`, and an upload over it is
 * refused here, so the plane's 413 sentence is never turned into an EPIPE by a
 * 25 MiB body streamed at a socket that already closed.
 *
 * The recording goes to the plane; the plane hears it, then says it back in the
 * voice the customer picked. This route forwards and passes the plane's answer —
 * its JSON, or its refusal in its own words — straight back.
 */
import { cpFetch, cpJson } from "../../../lib/controlplane.js";
import { DASH_KEY_COOKIE, serializeCookie } from "../../../lib/cookies.js";
import { planeUploadCeiling, tooLargeSentence } from "../../../lib/limits.js";
import { credentialCookieOptions, readDashKey, readSessionToken } from "../../../lib/session.js";

const KEY_LABEL = "dashboard";
/** Fields the engine accepts on its STT route; the plane's allow-list is the authority. */
const OPTION_FIELDS = ["language", "model", "prompt"];

function refused(detail, status = 400) {
  return Response.json({ detail }, { status });
}

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
  if (!sessionToken) return refused("Sign in first.", 401);

  const declared = request.headers.get("content-length");
  const ceiling = await planeUploadCeiling();
  if (ceiling && declared && /^\d+$/.test(declared.trim()) && Number(declared) > ceiling) {
    return refused(tooLargeSentence(ceiling), 413);
  }

  let incoming;
  try {
    incoming = await request.formData();
  } catch {
    return refused("Could not read the upload; send it as multipart form data.");
  }

  const file = incoming.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return refused("Choose a recording first. A WAV from the Speak page is a good one.");
  }
  if (ceiling && file.size > ceiling) return refused(tooLargeSentence(ceiling), 413);

  const voice = String(incoming.get("voice") ?? "").trim();
  if (!voice) return refused("Pick a voice. The library page lists the ones the plane offers.");

  let key = await readDashKey();
  let mintedKey = null;
  if (!key) {
    mintedKey = await mintDashboardKey(sessionToken);
    if (!mintedKey) {
      return Response.json(
        { detail: "The dashboard could not mint a key to dub with." },
        { status: 502 },
      );
    }
    key = mintedKey;
  }

  const outgoing = new FormData();
  outgoing.append("file", file, file.name || "recording");
  outgoing.append("voice", voice);
  for (const name of OPTION_FIELDS) {
    const value = incoming.get(name);
    if (typeof value === "string" && value.trim()) outgoing.append(name, value.trim());
  }

  let response;
  try {
    response = await cpFetch("/v1/dubs", { method: "POST", token: key, body: outgoing });
  } catch {
    return refused(
      "The control plane closed the connection while this recording was being sent, "
      + "so nothing was dubbed. Try a smaller recording.",
      502,
    );
  }

  const headers = new Headers({
    "content-type": response.headers.get("content-type") || "application/json",
  });
  if (mintedKey) {
    headers.set(
      "set-cookie",
      serializeCookie(DASH_KEY_COOKIE, mintedKey, credentialCookieOptions()),
    );
  }
  const text = await response.text();
  return new Response(text, { status: response.status, headers });
}