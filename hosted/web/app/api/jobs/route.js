/**
 * The job queue: list this workspace's jobs, or queue a new one.
 *
 * Both are session-authenticated on the plane, so a job queued here is
 * attributed to the workspace rather than to a key — which is exactly what the
 * plane's own notes say the dashboard does.
 */
import { proxyJson, readJsonBody } from "../../../lib/proxy.js";

export async function GET(request) {
  const url = new URL(request.url);
  const status = url.searchParams.get("status");
  const limit = url.searchParams.get("limit");
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (limit) query.set("limit", limit);
  const suffix = query.toString() ? `?${query}` : "";
  return proxyJson(`/v1/jobs${suffix}`);
}

export async function POST(request) {
  const body = await readJsonBody(request);
  // Validate here too so an empty composer is a plain 400 rather than a round
  // trip the plane answers with the same sentence.
  const input = String(body.input ?? "").trim();
  if (!input) {
    return Response.json({ detail: "'input' must be a non-empty string." }, { status: 400 });
  }
  return proxyJson("/v1/jobs", {
    method: "POST",
    body: {
      input,
      voice: String(body.voice ?? ""),
      response_format: String(body.response_format ?? "wav"),
    },
  });
}