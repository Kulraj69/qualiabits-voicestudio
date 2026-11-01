import assert from "node:assert/strict";
import { test } from "node:test";

import { cpBaseUrl, cpUrl, planeDetail } from "../lib/controlplane.js";

test("the base URL defaults to the local control plane on loopback", () => {
  assert.equal(cpBaseUrl({}), "http://127.0.0.1:8080");
});

test("the base URL is trimmed of trailing slashes so joins never double up", () => {
  assert.equal(cpBaseUrl({ QUALI_CP_BASE_URL: "http://plane:9000///" }), "http://plane:9000");
  assert.equal(cpBaseUrl({ QUALI_CP_BASE_URL: "  http://plane:9000  " }), "http://plane:9000");
});

test("paths join onto the base, with or without a leading slash", () => {
  const env = { QUALI_CP_BASE_URL: "http://plane:9000" };
  assert.equal(cpUrl("/v1/voices", env), "http://plane:9000/v1/voices");
  assert.equal(cpUrl("v1/voices", env), "http://plane:9000/v1/voices");
});

test("an error detail is read from the plane's own body, verbatim", () => {
  assert.equal(
    planeDetail({ detail: "That API key has been revoked." }),
    "That API key has been revoked.",
  );
  assert.equal(
    planeDetail({ error: "upstream_timeout" }, "fallback"),
    "upstream_timeout",
  );
});

test("a nested detail object yields its inner sentence", () => {
  assert.equal(
    planeDetail({ detail: { job_status: "queued", detail: "This job has no audio yet." } }),
    "This job has no audio yet.",
  );
});

test("an empty body yields the fallback rather than an empty string", () => {
  assert.equal(planeDetail({}, "nothing here"), "nothing here");
  assert.equal(planeDetail(null, "nothing here"), "nothing here");
  assert.equal(planeDetail({ detail: "   " }, "nothing here"), "nothing here");
});