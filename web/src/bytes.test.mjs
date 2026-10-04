/**
 * Run with: node --test web/src/bytes.test.mjs
 *
 * The sample-size guard, which lives in hash.js alongside sha256 because both turn
 * a File into bytes and neither needs the API layer. It was in upload.js until a
 * test tried to import it and pulled in auth.js, which reads import.meta.env - a
 * sign the function was in the wrong module.
 *
 * Worth pinning because without this guard the browser sends a request API Gateway
 * refuses with a bare 413, before any of this project's code can explain why.
 */

import assert from "node:assert/strict";
import test from "node:test";

globalThis.btoa ??= (binary) => Buffer.from(binary, "binary").toString("base64");

const { MAX_SAMPLE_BYTES, toBase64 } = await import("./hash.js");

function fakeFile(size) {
  return {
    size,
    name: "sample.jpg",
    arrayBuffer: async () => new Uint8Array(size).buffer,
  };
}

test("a sample over the limit is refused before any encoding", async () => {
  await assert.rejects(() => toBase64(fakeFile(MAX_SAMPLE_BYTES + 1)), /must be under 4 MB/);
});

test("the refusal points at uploading instead", async () => {
  // The limits differ by a factor of six and the reason is not obvious, so the
  // message says what to do rather than only what went wrong.
  await assert.rejects(() => toBase64(fakeFile(MAX_SAMPLE_BYTES + 1)), /25 MB/);
});

test("a sample at the limit is accepted", async () => {
  const encoded = await toBase64(fakeFile(1024));
  assert.equal(typeof encoded, "string");
});

test("encoding handles a file larger than the argument limit", async () => {
  // String.fromCharCode(...bytes) on a multi-megabyte array throws RangeError,
  // which is why the encoder works in chunks. 2 MB is past the threshold.
  const encoded = await toBase64(fakeFile(2 * 1024 * 1024));
  assert.equal(encoded.length, Math.ceil((2 * 1024 * 1024) / 3) * 4);
});
