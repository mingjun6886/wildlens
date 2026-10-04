/**
 * Run with: node --test web/src/hash.test.mjs
 *
 * The first test is the one that matters: it hashes the same bytes that
 * `shasum -a 256` is given and compares against a known digest. A hashing bug
 * here produces an upload that can never succeed, so an assertion against an
 * independently computed value is worth more than a self-consistent one.
 */

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";

globalThis.crypto ??= webcrypto;

const { extensionOf, sha256 } = await import("./hash.js");

/** A File-like object, since node:test has no DOM. */
function fakeFile(name, bytes) {
  return {
    name,
    arrayBuffer: async () => new Uint8Array(bytes).buffer,
  };
}

test("matches the digest node:crypto produces for the same bytes", async () => {
  const bytes = Buffer.from("wildlens");
  const digest = await sha256(fakeFile("x.jpg", [...bytes]));

  // Compared against a second implementation rather than a literal copied from a
  // terminal, so the test keeps working if the sample bytes change.
  const { createHash } = await import("node:crypto");

  assert.equal(digest, createHash("sha256").update(bytes).digest("hex"));
  assert.equal(digest.length, 64);
});

test("zero-pads a byte below 0x10", async () => {
  // A single 0x0a byte. Without padStart the digest would be 63 characters
  // whenever any byte of the hash is below 0x10, which is almost always.
  const digest = await sha256(fakeFile("x.jpg", [0x0a]));
  const { createHash } = await import("node:crypto");

  assert.equal(digest, createHash("sha256").update(Buffer.from([0x0a])).digest("hex"));
  assert.equal(digest.length, 64);
});

test("produces lowercase hex only", async () => {
  const digest = await sha256(fakeFile("x.jpg", [255, 254, 253]));
  assert.match(digest, /^[0-9a-f]{64}$/);
});

test("hashes bytes, not text", async () => {
  // 0xFF is not valid UTF-8. Reading this file as text would replace it with
  // U+FFFD and produce a different digest, silently.
  const digest = await sha256(fakeFile("x.jpg", [0xff, 0x00, 0x80]));
  const { createHash } = await import("node:crypto");

  assert.equal(digest, createHash("sha256").update(Buffer.from([0xff, 0x00, 0x80])).digest("hex"));
});

test("an empty file still hashes", async () => {
  // The upload endpoint rejects size 0, but hashing must not be where that fails.
  const digest = await sha256(fakeFile("x.jpg", []));
  assert.equal(digest, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
});

test("extensionOf lowercases and drops the dot", () => {
  assert.equal(extensionOf({ name: "Sus_scrofa_1.JPG" }), "jpg");
  assert.equal(extensionOf({ name: "a.b.webp" }), "webp");
});

test("extensionOf refuses a name with no extension", () => {
  assert.throws(() => extensionOf({ name: "noextension" }), /no file extension/);
});
