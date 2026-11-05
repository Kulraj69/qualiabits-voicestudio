/**
 * Limits: read this workspace's ceiling and rate, and set them.
 *
 * Both verbs go through the same proxy the rest of the dashboard uses, so the
 * session token stays in its httpOnly cookie and the browser never holds it. The
 * body is rebuilt field by field rather than forwarded: the plane distinguishes
 * "absent" from "null" (leave alone versus clear), and a spread would turn a
 * missing field into an explicit null and silently clear a limit the customer
 * meant to keep.
 */
import { proxyJson, readJsonBody } from "../../../lib/proxy.js";
import { limitsPatch } from "../../../lib/quota.js";

export async function GET() {
  return proxyJson("/v1/limits");
}

export async function PUT(request) {
  const body = await readJsonBody(request);
  return proxyJson("/v1/limits", { method: "PUT", body: limitsPatch(body) });
}