/**
 * One project: its chapters and their progress, and cancelling the unspoken ones.
 */
import { proxyJson } from "../../../../lib/proxy.js";

export async function GET(_request, { params }) {
  const { id } = await params;
  return proxyJson(`/v1/projects/${encodeURIComponent(id)}`);
}

export async function DELETE(_request, { params }) {
  const { id } = await params;
  return proxyJson(`/v1/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
}