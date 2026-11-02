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
import { planeUploadCeiling, tooLargeSentence } from "../../../lib/limits.js";
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

  // Refuse an upload the plane would refuse, before sending it there.
  //
  // The plane bounds this on the declared `content-length` and closes the
  // connection to say so. Forwarding 25 MiB at it first turns that sentence into
  // an EPIPE here and a bare 500 for the customer, which is exactly what the
  // real-engine run caught. The number is the plane's own, read from the plane;
  // when it cannot be read, the request is forwarded and the plane does the
  // refusing, as before.
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
  // A part that parsed as smaller than its own declared length can still be over
  // the ceiling (a multipart body has overhead), so the file is bounded too.
  if (ceiling && file.size > ceiling) {
    return refused(tooLargeSentence(ceiling), 413);
  }

  const outgoing = new FormData();
  outgoing.append("file", file, file.name || "recording");
  for (const name of OPTION_FIELDS) {
    const value = incoming.get(name);
    if (typeof value === "string" && value.trim()) outgoing.append(name, value.trim());
  }

  let response;
  try {
    response = await cpFetch("/v1/audio/transcriptions", {
      method: "POST",
      token: sessionToken,
      body: outgoing,
    });
  } catch {
    // The write itself failed: the plane closed the connection rather than answer.
    // Saying so is better than a 500 that says nothing, and it is not a claim
    // about why the plane closed it.
    return refused(
      "The control plane closed the connection while this upload was being sent, "
      + "so nothing was transcribed. Try a smaller recording.",
      502,
    );
  }

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