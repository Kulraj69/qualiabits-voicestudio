/**
 * One server-side read of the control plane, in which "the plane did not answer"
 * is a different value from "the plane answered, and the answer is empty".
 *
 * Every page that reads the plane while it renders used to fold both cases into
 * the empty one, because the read and the fallback sat on one line:
 * `initialKeys={status === 200 ? data?.api_keys ?? [] : []}`. A plane that answered
 * 500 therefore rendered "No keys yet. Create one above." and a customer whose plane
 * had hiccuped was told their keys were gone; the home page printed four zeros for a
 * meter it had not read. That is the defect class this module closes: a page may show
 * an empty state only when the plane really said the list is empty.
 *
 * The three outcomes are `ok` (200, `data` is the plane's body), `failed` (an error
 * status or a transport error, `error` is one plain sentence to show) and 401, which
 * is a failure of the read like any other and which every page handles first by
 * redirecting to the sign-in page.
 */

import { cpBaseUrl, cpJson, planeDetail } from "./controlplane.js";

/**
 * The sentence for a plane that could not be reached at all.
 *
 * It names the address that was tried, because "the plane is down" is only useful
 * to whoever has to start it, and it quotes the socket error's own code rather than
 * guessing a cause.
 */
export function unreachableMessage(error, env = process.env) {
  const code = (error && (error.cause?.code || error.code)) || "";
  const why = code ? ` (${code})` : "";
  return (
    `The control plane at ${cpBaseUrl(env)} is not answering${why}, so this page has ` +
    "nothing to show. Reload when the plane is back."
  );
}

/**
 * The sentence for a plane that answered with a status other than 200.
 *
 * The plane's own detail wins when it has one — it is the only side that knows why
 * it refused — and the fallback names the status instead of inventing a reason.
 */
export function refusedMessage(status, payload) {
  return planeDetail(
    payload,
    `The control plane answered ${status}, so this page has nothing to show. ` +
      "Reload when the plane is back.",
  );
}

/**
 * Read one plane route for a page's render.
 *
 * `reader` is `cpJson` everywhere in the app and is a parameter so the web unit suite
 * can hand it a plane that answers 500 or throws without starting a server. It is
 * called with the token only when there is one, so a route cannot accidentally send
 * an empty `Authorization` header.
 */
export async function readPlane(path, { token, reader = cpJson, env = process.env } = {}) {
  try {
    const { status, data } = await reader(path, token ? { token } : undefined);
    if (status === 200) {
      return { status, ok: true, failed: false, data, error: "" };
    }
    return {
      status,
      ok: false,
      failed: true,
      data: null,
      error: refusedMessage(status, data),
    };
  } catch (error) {
    return {
      status: 0,
      ok: false,
      failed: true,
      data: null,
      error: unreachableMessage(error, env),
    };
  }
}

/**
 * The first failure among several reads of one page, or null when they all answered.
 *
 * A page that needs three routes must say the plane did not answer rather than show
 * two empty panels and one real one; which route failed is the plane's business, not
 * the reader's, so the sentence is the first failure's own.
 */
export function firstFailure(...reads) {
  return reads.find((read) => read && read.failed) || null;
}