import assert from "node:assert/strict";
import { test } from "node:test";

import { describeLimit, limitsFormBody, limitsPatch } from "../lib/quota.js";

test("a partial change carries only the field it changes", () => {
  assert.deepEqual(limitsPatch({ character_ceiling: 500 }), { character_ceiling: 500 });
  assert.deepEqual(limitsPatch({ requests_per_minute: 3 }), { requests_per_minute: 3 });
  // The bug this exists to prevent: a body that never mentioned the rate must not
  // clear it, and a spread or a template with both keys would.
  assert.equal("requests_per_minute" in limitsPatch({ character_ceiling: 500 }), false);
});

test("an explicit null is a clear, and it survives the trip", () => {
  assert.deepEqual(
    limitsPatch({ character_ceiling: null, requests_per_minute: 10 }),
    { character_ceiling: null, requests_per_minute: 10 },
  );
});

test("nothing else in the body is forwarded", () => {
  assert.deepEqual(
    limitsPatch({ character_ceiling: 5, workspace_id: 999, admin: true }),
    { character_ceiling: 5 },
  );
});

test("a form's empty box means no limit, and never a limit of zero", () => {
  assert.deepEqual(limitsFormBody({ ceiling: "", rate: "" }), { body: { character_ceiling: null, requests_per_minute: null }, error: "" });
  assert.deepEqual(limitsFormBody({ ceiling: "1200", rate: "" }), { body: { character_ceiling: 1200, requests_per_minute: null }, error: "" });
  // Number("") is 0; a ceiling of zero refuses every request, which is the
  // opposite of what an empty box means.
  assert.notEqual(limitsFormBody({ ceiling: "", rate: "" }).body.character_ceiling, 0);
});

test("a non-numeric box is refused here rather than at the plane", () => {
  const spaced = limitsFormBody({ ceiling: "1 200", rate: "" });
  assert.match(spaced.error, /whole numbers/);
  assert.equal(spaced.body, null);
  const negative = limitsFormBody({ ceiling: "-5", rate: "" });
  assert.match(negative.error, /whole numbers/);
});

test("a limit reads as none, never as zero", () => {
  assert.equal(describeLimit(null), "none");
  assert.equal(describeLimit(undefined), "none");
  assert.equal(describeLimit(0), "none");
  assert.equal(describeLimit(2500), "2500");
});
