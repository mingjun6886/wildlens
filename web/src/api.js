/**
 * The only module that talks to the backend.
 *
 * One gateway, because exactly one place should know how to attach the token and
 * exactly one place should know what to do about a 401. Token expiry happens
 * mid-session - they last an hour - and if six modules each called fetch, six
 * would have to handle it, and the sixth would not.
 *
 * Requests go to a relative /api path in both development and production. In
 * development Vite proxies it, which happens server-side where CORS does not
 * apply. In production CloudFront serves the same path from the API Gateway
 * origin, so the browser never makes a cross-origin request and there is no CORS
 * configuration in either. See the Phase 7 notes.
 */

import { idToken, isSignedIn, signIn } from "./auth.js";
import { errorMessage, parseBody } from "./http.js";

const BASE = "/api";

export class ApiError extends Error {
  constructor(status, message, correlationId) {
    super(message);
    this.status = status;
    this.correlationId = correlationId;
  }
}

async function request(path, options = {}) {
  if (!isSignedIn()) {
    // Checked before sending rather than after a 401, so an expired session
    // produces a sign-in rather than a failed request the user has to repeat.
    await signIn();
    // signIn navigates away; this is unreachable but makes the contract explicit.
    throw new ApiError(401, "signing in");
  }

  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      // The raw token, not "Bearer <token>". The authoriser is configured for
      // the header value itself, and the Bearer prefix returns 401 with no
      // explanation of what was wrong.
      Authorization: idToken(),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (response.status === 401) {
    // The token was accepted a moment ago by isSignedIn, so this is a pool that
    // no longer matches, or a clock skew. Either way the session is unusable.
    await signIn();
    throw new ApiError(401, "session rejected");
  }

  const body = parseBody(await response.text());

  if (!response.ok) {
    // correlationId appears on a 500 and is what ties a user's report to a log
    // line, so it is carried through rather than discarded.
    throw new ApiError(
      response.status,
      errorMessage(body, response.statusText),
      body.correlationId,
    );
  }

  return body;
}

/** Reserve a record. Returns {duplicate, fileId, key?, uploadUrl?, requiredHeaders?}. */
export function reserveUpload({ sha256, ext, size }) {
  return request("/upload", {
    method: "POST",
    body: JSON.stringify({ sha256, ext, size }),
  });
}

export function fileStatus(fileId) {
  return request(`/files/${fileId}`);
}

export function searchByTags(counts) {
  return request("/search/tags", { method: "POST", body: JSON.stringify(counts) });
}

export function searchBySpecies(names) {
  return request("/search/species", { method: "POST", body: JSON.stringify(names) });
}

export function searchByFile({ base64, ext }) {
  return request("/search/byfile", {
    method: "POST",
    body: JSON.stringify({ file: base64, ext }),
  });
}

/**
 * Send the bytes to S3. Deliberately not using `request`: this goes to S3, not
 * to the API, and carries no Authorization header at all.
 *
 * The presigned URL *is* the authorisation. Adding an Authorization header makes
 * S3 try to use it instead of the signature and return 400.
 */
export async function putBytes(uploadUrl, file, requiredHeaders) {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    // Exactly the headers the upload endpoint named, and nothing else. The URL
    // signs both Content-Type and Content-Length; letting fetch infer the type
    // from file.type gives 403 SignatureDoesNotMatch whenever the two disagree,
    // and the browser sets Content-Length itself from the body.
    headers: { "Content-Type": requiredHeaders["Content-Type"] },
    body: file,
  });

  if (!response.ok) {
    throw new ApiError(
      response.status,
      response.status === 403
        ? "upload rejected: the file does not match what was declared (size or type)"
        : `upload failed: ${response.status}`,
    );
  }
}
