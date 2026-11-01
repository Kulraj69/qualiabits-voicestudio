/**
 * API keys: list (never key material) and mint (the key is returned once).
 */
import { proxyJson, readJsonBody } from "../../../lib/proxy.js";

export async function GET() {
  return proxyJson("/v1/api-keys");
}

export async function POST(request) {
  const body = await readJsonBody(request);
  return proxyJson("/v1/api-keys", {
    method: "POST",
    body: { label: String(body.label ?? "") },
  });
}