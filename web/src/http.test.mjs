/**
 * Run with: node --test web/src/http.test.mjs
 *
 * The error path, because it is exercised least and matters most when something is
 * wrong. Each test pins a failure that would otherwise replace a real error with a
 * confusing one.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { errorMessage, parseBody } from "./http.js";

test("a handler error is reported from the error field", () => {
  const body = parseBody('{"error": "size must be greater than zero"}');
  assert.equal(errorMessage(body, "Bad Request"), "size must be greater than zero");
});

test("an API Gateway error is reported from the message field", () => {
  // What a 413 actually returns. Reading only `error` would fall through to the
  // status text and lose the specific reason.
  const body = parseBody('{"message": "Request Too Long"}');
  assert.equal(errorMessage(body, "Payload Too Large"), "Request Too Long");
});

test("an HTML error body does not throw", () => {
  // JSON.parse raises SyntaxError here, which would surface as "Unexpected
  // token <" and hide the status entirely.
  const body = parseBody("<html><body>504 Gateway Timeout</body></html>");
  assert.match(errorMessage(body, "Gateway Timeout"), /504/);
});

test("an empty body falls back to the status text", () => {
  assert.equal(errorMessage(parseBody(""), "Unauthorized"), "Unauthorized");
});

test("a long non-JSON body is truncated", () => {
  const body = parseBody("x".repeat(5000));
  assert.ok(errorMessage(body, "Error").length <= 200);
});

test("a successful body is returned as parsed", () => {
  assert.deepEqual(parseBody('{"results": [], "query": {"dingo": 1}}'), {
    results: [],
    query: { dingo: 1 },
  });
});

test("correlationId survives parsing", () => {
  // The one field that makes a 500 diagnosable rather than just reported.
  const body = parseBody('{"error": "internal error", "correlationId": "abc-123"}');
  assert.equal(body.correlationId, "abc-123");
});
