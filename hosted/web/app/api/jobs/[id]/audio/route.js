/**
 * A finished job's stored audio, streamed through the plane.
 *
 * Bytes only: nothing here parses, re-encodes or measures the WAV. The plane's
 * own `X-Qualiabits-Audio-Seconds` and digest headers are forwarded so the page
 * can show what the plane recorded rather than a number the page made up.
 */
import { proxyBytes } from "../../../../../lib/proxy.js";

export async function GET(_request, { params }) {
  const { id } = await params;
  return proxyBytes(`/v1/jobs/${encodeURIComponent(id)}/audio`);
}