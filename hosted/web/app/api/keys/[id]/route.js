/**
 * Revoke one of this workspace's own keys.
 *
 * The plane answers 404 for a key that is not yours, exactly as it does for one
 * that does not exist, and this route passes that through rather than turning it
 * into a 403 the page could distinguish.
 */
import { proxyJson } from "../../../../lib/proxy.js";

export async function DELETE(_request, { params }) {
  const { id } = await params;
  return proxyJson(`/v1/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
}