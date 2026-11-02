/**
 * Transcribe, through the metered route, on the dashboard's behalf.
 *
 * The banner's recording never goes straight to the engine. It goes to this
 * route, which attaches the session token from its httpOnly cookie and hands it
 * to the plane's `POST /v1/audio/transcriptions`; the plane measures it, records
 * it against the workspace and passes it on. The browser therefore never holds a
 * credential that could transcribe on somebody else's account, and every second
 * transcribed is attributable.
 *
 * The options are forwarded by name and only if the caller supplied them; the
 * plane keeps only the engine's own option names, so an extra field added to this
 * form later cannot become an engine parameter by accident.
 */
import { cpFetch } from "../../../lib/controlplane.js";
import { readSessionToken } from "../../../lib/session.js";

const FORWARDED = [
  "content-type",
  "x-qualiabits-audio-seconds",
  "x-qualiabits-engine-seconds",
  "x-qualiabits-transcript-characters",
  "x-qualiabits-upload-bytes",
  "x-qualiabits-workspace",
  "x-omnivoice-backend",
];

/** Fields the form may carry. The plane's allow-list is the authority. */
const OPTION_FIELDS = ["language", "response_format", "prompt", "model"];

function refused(detail, status = 400) {
  return Response.json({ detail }, { status });
}

export async function POST(request) {
  const sessionToken = await readSessionToken();
  if (!sessionToken) return refused("Sign in first.", 401);

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

  const outgoing = new FormData();
  outgoing.append("file", file, file.name || "recording");
  for (const name of OPTION_FIELDS) {
    const value = incoming.get(name);
    if (typeof value === "string" && value.trim()) outgoing.append(name, value.trim());
  }

  const response = await cpFetch("/v1/audio/transcriptions", {
    method: "POST",
    token: sessionToken,
    body: outgoing,
  });

  const headers = new Headers();
  for (const name of FORWARDED) {
    const value = response.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  if (!headers.get("content-type")) headers.set("content-type", "application/json");

  // The body is passed through byte for byte, whatever format was asked for:
  // the plane's refusal sentence, the engine's JSON, or a subtitle file.
  const bytes = await response.arrayBuffer();
  return new Response(bytes, { status: response.status, headers });
}