import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clearCookie,
  decodeWorkspace,
  encodeWorkspace,
  serializeCookie,
} from "../lib/cookies.js";

test("a credential cookie is httpOnly unless told otherwise", () => {
  const optioned = serializeCookie("qb_session", "tok");
  assert.match(optioned, /HttpOnly/);
  assert.match(optioned, /SameSite=Lax/);
  assert.match(optioned, /Path=\//);
});

test("the workspace cookie can be made readable, and carries no secret", () => {
  const readable = serializeCookie("qb_ws", "{}", { httpOnly: false });
  assert.doesNotMatch(readable, /HttpOnly/);
  assert.match(readable, /SameSite=Lax/);
});

test("Secure is opt-in, because the local dashboard is plain http", () => {
  assert.doesNotMatch(serializeCookie("a", "b"), /Secure/);
  assert.match(serializeCookie("a", "b", { secure: true }), /Secure/);
});

test("clearing a cookie sets Max-Age=0", () => {
  assert.match(clearCookie("qb_session"), /qb_session=;/);
  assert.match(clearCookie("qb_session"), /Max-Age=0/);
});

test("workspace fields round-trip through the cookie value", () => {
  const encoded = encodeWorkspace({ id: 7, email: "a@b.co", display_name: "Ada" });
  assert.deepEqual(decodeWorkspace(encoded), { id: 7, email: "a@b.co", display_name: "Ada" });
});

test("a malformed or id-less workspace cookie decodes to null, never a fake id", () => {
  assert.equal(decodeWorkspace("not json"), null);
  assert.equal(decodeWorkspace(""), null);
  assert.equal(decodeWorkspace(undefined), null);
  assert.equal(decodeWorkspace(JSON.stringify({ email: "x@y.z" })), null);
  assert.equal(decodeWorkspace(JSON.stringify({ id: "7" })), null);
});