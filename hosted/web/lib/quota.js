/**
 * The rules for reading and writing a workspace's limits, in one place.
 *
 * Both sides of the dashboard need the same distinction, and getting it wrong is
 * silent: the plane treats an *absent* field as "leave this limit alone" and an
 * explicit `null` as "clear it". A proxy that spreads the request body, or a form
 * that always sends both fields, therefore clears a limit the customer never
 * touched. These two functions are the only places that decision is made, so the
 * Next route and the form cannot disagree about it, and both are unit-tested
 * without a browser or a plane.
 */

/** The two fields a caller may change. Anything else is dropped, not forwarded. */
export const LIMIT_FIELDS = ["character_ceiling", "requests_per_minute"];

/**
 * Rebuild a partial limits change from a request body.
 *
 * Only fields the caller actually sent appear in the result. `null` stays `null`
 * (clear the limit); anything else is coerced to a number so the plane's own
 * validation answers with a schema refusal rather than a string comparison here.
 */
export function limitsPatch(body) {
  const source = body && typeof body === "object" ? body : {};
  const patch = {};
  for (const field of LIMIT_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(source, field)) continue;
    const value = source[field];
    patch[field] = value === null || value === "" ? null : Number(value);
  }
  return patch;
}

/**
 * The body a limit form submits, and the sentence to show instead when it cannot.
 *
 * An empty box means "no limit" — that is the whole reason this is a function and
 * not two `Number()` calls in a component: `Number("")` is 0, and a ceiling of 0
 * is not no ceiling, it is a ceiling of zero.
 */
export function limitsFormBody({ ceiling, rate }) {
  const body = {};
  for (const [field, raw] of [
    ["character_ceiling", ceiling],
    ["requests_per_minute", rate],
  ]) {
    const text = String(raw ?? "").trim();
    if (text === "") {
      body[field] = null;
      continue;
    }
    if (!/^\d+$/.test(text)) {
      return {
        body: null,
        error: "Both limits are whole numbers: characters per month, and requests per minute.",
      };
    }
    body[field] = Number(text);
  }
  return { body, error: "" };
}

/** What to show for a limit that is set or unset, in the page's own words. */
export function describeLimit(value) {
  return value === null || value === undefined || Number(value) <= 0
    ? "none"
    : String(Number(value));
}