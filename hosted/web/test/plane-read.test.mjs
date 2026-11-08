import assert from "node:assert/strict";
import { test } from "node:test";

import { firstFailure, readPlane, refusedMessage, unreachableMessage } from "../lib/planeRead.js";

/** A reader that answers with one canned reply, or throws one canned error. */
function readerReturning(reply) {
  return async (path, options) => {
    if (reply instanceof Error) throw reply;
    return { ...reply, path, options };
  };
}

test("a 200 is the plane's body, and is not a failure", async () => {
  const read = await readPlane("/v1/voices", {
    token: "t",
    reader: readerReturning({ status: 200, data: { voices: [], count: 0 } }),
  });
  assert.equal(read.ok, true);
  assert.equal(read.failed, false);
  assert.equal(read.error, "");
  assert.deepEqual(read.data, { voices: [], count: 0 });
});

test("a 200 with an empty list is empty, not failed", async () => {
  const read = await readPlane("/v1/api-keys", {
    token: "t",
    reader: readerReturning({ status: 200, data: { api_keys: [] } }),
  });
  assert.equal(read.failed, false);
  assert.deepEqual(read.data.api_keys, []);
});

test("a 500 is a failure whose sentence names the status", async () => {
  const read = await readPlane("/v1/api-keys", {
    token: "t",
    reader: readerReturning({ status: 500, data: null }),
  });
  assert.equal(read.ok, false);
  assert.equal(read.failed, true);
  assert.equal(read.status, 500);
  assert.equal(read.data, null);
  assert.match(read.error, /answered 500/);
});

test("the plane's own detail is shown verbatim when it has one", async () => {
  const read = await readPlane("/v1/usage", {
    token: "t",
    reader: readerReturning({ status: 429, data: { detail: "Too many requests." } }),
  });
  assert.equal(read.failed, true);
  assert.equal(read.error, "Too many requests.");
});

test("a 401 is a failure of the read, so a page can redirect before it renders", async () => {
  const read = await readPlane("/v1/voices", {
    token: "stale",
    reader: readerReturning({ status: 401, data: { detail: "Sign in again." } }),
  });
  assert.equal(read.failed, true);
  assert.equal(read.status, 401);
});

test("a transport error is a failure, and names the address and the socket code", async () => {
  const error = new TypeError("fetch failed");
  error.cause = { code: "ECONNREFUSED" };
  const read = await readPlane("/v1/voices", {
    token: "t",
    env: { QUALI_CP_BASE_URL: "http://127.0.0.1:9" },
    reader: readerReturning(error),
  });
  assert.equal(read.status, 0);
  assert.equal(read.failed, true);
  assert.equal(read.data, null);
  assert.match(read.error, /http:\/\/127\.0\.0\.1:9/);
  assert.match(read.error, /ECONNREFUSED/);
});

test("a transport error without a code is still a failure, and invents no cause", async () => {
  const read = await readPlane("/v1/voices", {
    token: "t",
    reader: readerReturning(new Error("socket hang up")),
  });
  assert.equal(read.failed, true);
  assert.match(read.error, /is not answering/);
  assert.doesNotMatch(read.error, /ECONNREFUSED|hang up/);
});

test("the token reaches the reader, and an absent one sends no header", async () => {
  const seen = [];
  const spy = async (path, options) => {
    seen.push(options);
    return { status: 200, data: {} };
  };
  await readPlane("/v1/voices", { token: "abc", reader: spy });
  await readPlane("/healthz", { reader: spy });
  assert.deepEqual(seen, [{ token: "abc" }, undefined]);
});

test("firstFailure returns the first read that failed, and null when none did", () => {
  const ok = { failed: false };
  const bad = { failed: true, error: "The control plane answered 500." };
  assert.equal(firstFailure(ok, ok), null);
  assert.equal(firstFailure(ok, bad, { failed: true, error: "second" }), bad);
});

test("the two sentences are the ones a page shows, and neither claims a cause", () => {
  assert.match(refusedMessage(503, null), /answered 503/);
  assert.equal(refusedMessage(503, { detail: "The plane is starting." }), "The plane is starting.");
  const sentence = unreachableMessage(new Error("nope"));
  assert.match(sentence, /nothing to show/);
  assert.match(sentence, /Reload when the plane is back/);
});