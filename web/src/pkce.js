/**
 * PKCE (RFC 7636), the part of sign-in that is pure computation.
 *
 * Separated from auth.js so it can be tested without a browser, and because
 * getting any of it slightly wrong fails with `invalid_grant` and nothing else -
 * no indication of whether the verifier, the challenge, or the encoding is at
 * fault. The tests check against the worked example in RFC 7636 Appendix B.
 */

/**
 * base64url: base64 with the URL-unsafe characters replaced and padding removed.
 *
 * Plain base64 would be rejected: `+` and `/` are not URL-safe and `=` is
 * forbidden here by the spec. One function so the alphabet is defined once.
 */
export function base64url(bytes) {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

/** Reverse of base64url, for reading a JWT payload. */
export function fromBase64url(text) {
  const padded = text.padEnd(text.length + ((4 - (text.length % 4)) % 4), "=");
  return atob(padded.replaceAll("-", "+").replaceAll("_", "/"));
}

/**
 * A code verifier: 43 characters of base64url over 32 random bytes.
 *
 * RFC 7636 allows 43 to 128 characters. 32 bytes is the recommended amount of
 * entropy and encodes to exactly 43, the minimum - shorter would be rejected.
 */
export function randomVerifier() {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

/** code_challenge = base64url(SHA-256(verifier)), the S256 method. */
export async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(digest);
}
