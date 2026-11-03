/**
 * The assembled audiobook, streamed through the dashboard.
 *
 * The bytes come from the plane, joined there from the chapters it stored, and are
 * passed through untouched — this route neither resamples nor re-headers them. When
 * the plane refuses (a chapter is missing, or the chapters disagree on their
 * format) the refusal is passed through with its own status and sentence, so the
 * page shows the plane's reason rather than a rewrite of it.
 */
import { proxyBytes } from "../../../../../lib/proxy.js";

export async function GET(_request, { params }) {
  const { id } = await params;
  return proxyBytes(`/v1/projects/${encodeURIComponent(id)}/audio`);
}