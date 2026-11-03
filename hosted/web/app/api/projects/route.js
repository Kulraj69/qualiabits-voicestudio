/**
 * The long-form queue: list this workspace's projects, or queue a manuscript.
 *
 * Both calls are session-authenticated on the plane, so a project queued here is
 * attributed to the workspace rather than to a key — the same rule the Jobs page
 * follows. The split happens on the plane, not here: this route forwards the
 * manuscript and lets the plane decide where the chapters end, so the page cannot
 * disagree with the plane about a chapter boundary.
 *
 * The one check made here is the empty composer, so pressing the button with
 * nothing written is a plain 400 instead of a round trip that answers with the
 * same sentence.
 */
import { proxyJson, readJsonBody } from "../../../lib/proxy.js";

export async function GET(request) {
  const url = new URL(request.url);
  const limit = url.searchParams.get("limit");
  const suffix = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  return proxyJson(`/v1/projects${suffix}`);
}

export async function POST(request) {
  const body = await readJsonBody(request);
  const input = String(body.input ?? "");
  if (!input.trim()) {
    return Response.json({ detail: "'input' must be a non-empty string." }, { status: 400 });
  }
  return proxyJson("/v1/projects", {
    method: "POST",
    body: {
      title: String(body.title ?? ""),
      input,
      voice: String(body.voice ?? ""),
      response_format: String(body.response_format ?? "wav"),
    },
  });
}