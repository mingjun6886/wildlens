/**
 * Run with: node --test web/src/pkce.test.mjs
 *
 * The first test is the important one. It uses the worked example from RFC 7636
 * Appendix B, so it checks this implementation against the specification rather
 * than against itself. A PKCE bug otherwise surfaces as `invalid_grant` from
 * Cognito, which names nothing.
 */

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";

globalThis.crypto ??= webcrypto;
globalThis.btoa ??= (binary) => Buffer.from(binary, "binary").toString("base64");
globalThis.atob ??= (text) => Buffer.from(text, "base64").toString("binary");

const { base64url, challengeFor, fromBase64url, randomVerifier } = await import("./pkce.js");

// RFC 7636 Appendix B.
const RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
const RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

test("produces the challenge from the RFC 7636 worked example", async () => {
  assert.equal(await challengeFor(RFC_VERIFIER), RFC_CHALLENGE);
});

test("base64url uses the URL-safe alphabet and no padding", () => {
  // These bytes encode to "+/+/" in standard base64, which is the whole point.
  const encoded = base64url(new Uint8Array([0xfb, 0xff, 0xfe, 0xfb, 0xff, 0xfe]));

  assert.doesNotMatch(encoded, /[+/=]/);
  assert.match(encoded, /^[A-Za-z0-9_-]+$/);
});

test("base64url round-trips through fromBase64url", () => {
  const bytes = new Uint8Array([0, 1, 127, 128, 255, 254]);
  const decoded = fromBase64url(base64url(bytes));

  assert.deepEqual(Array.from(decoded, (c) => c.charCodeAt(0)), Array.from(bytes));
});

test("fromBase64url restores stripped padding", () => {
  // One, two and three leftover bytes exercise all three padding cases; atob
  // rejects an unpadded string, which is how a JWT payload fails to decode.
  for (const length of [1, 2, 3, 4, 5]) {
    const bytes = new Uint8Array(length).fill(0x41);
    assert.equal(fromBase64url(base64url(bytes)).length, length);
  }
});

test("a verifier is 43 characters, the RFC minimum", () => {
  // 32 random bytes encode to exactly 43 base64url characters. Fewer would be
  // rejected by the authorisation server.
  const verifier = randomVerifier();

  assert.equal(verifier.length, 43);
  assert.match(verifier, /^[A-Za-z0-9_-]{43}$/);
});

test("two verifiers differ", () => {
  assert.notEqual(randomVerifier(), randomVerifier());
});

test("the challenge is not the verifier", async () => {
  // Sending the verifier as the challenge would work with method "plain" and
  // defeat the whole mechanism.
  const verifier = randomVerifier();
  assert.notEqual(await challengeFor(verifier), verifier);
});
