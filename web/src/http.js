/**
 * How a response body becomes an error message.
 *
 * Extracted from api.js so it can be imported by a test instead of copied into
 * one. The first version of its test re-declared these rules locally, which tests
 * a duplicate rather than the code - and a test that can drift from what it claims
 * to cover is worse than none, because it still goes green.
 *
 * Same reason pkce.js exists: the parts that must be exactly right are the parts
 * worth isolating.
 */

/** Parse a body, tolerating anything that is not JSON. */
export function parseBody(text) {
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    // Not every error body is JSON. A 413 or a 504 can come from API Gateway
    // rather than a handler, and a proxy can return HTML. Letting JSON.parse throw
    // replaces the real status with "Unexpected token <" and hides the cause.
    return { message: text.slice(0, 200) };
  }
}

/**
 * The message to show for a failed response.
 *
 * Two field names, because two different things produce errors here: this
 * project's handlers use `error`, and API Gateway itself uses `message` - which is
 * what a 413 or an authoriser rejection carries.
 */
export function errorMessage(body, statusText) {
  return body.error ?? body.message ?? statusText;
}
