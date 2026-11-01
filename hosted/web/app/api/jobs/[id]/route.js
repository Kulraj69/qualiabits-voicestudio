/**
 * One job's status, and cancelling a queued one.
 */
import { proxyJson } from "../../../../lib/proxy.js";

export async function GET(_request, { params }) {
  const { id } = await params;
  return proxyJson(`/v1/jobs/${encodeURIComponent(id)}`);
}

export async function DELETE(_request, { params }) {
  const { id } = await params;
  return proxyJson(`/v1/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
}