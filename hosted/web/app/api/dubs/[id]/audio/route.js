/**
 * The dubbed WAV, streamed through the dashboard.
 *
 * The bytes come from the plane, where the row points at the object the speech
 * engine really produced, and are passed through untouched. A refusal (no such
 * dub, or another workspace's) is passed through with its own status and sentence.
 */
import { proxyBytes } from "../../../../../lib/proxy.js";

export async function GET(_request, { params }) {
  const { id } = await params;
  return proxyBytes(`/v1/dubs/${encodeURIComponent(id)}/audio`);
}