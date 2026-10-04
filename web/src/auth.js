/**
 * Sign-in through the Cognito Hosted UI, using authorization code flow with PKCE.
 *
 * This file holds no password and no client secret. Cognito hosts the sign-in
 * page, so the application never sees a credential - which is the main reason to
 * use Cognito rather than a JWT library.
 *
 * Why PKCE is not optional here: the app client has no secret, because anything
 * shipped to a browser is readable. Without a secret, the only thing stopping
 * somebody who intercepts the redirect from exchanging the code for tokens is
 * proof that they started the flow. That proof is the code verifier.
 *
 *   1. generate a random verifier, keep it in sessionStorage
 *   2. send its SHA-256 as code_challenge, method S256
 *   3. Cognito returns a code to the redirect URI
 *   4. exchange code + verifier for tokens; without the verifier this fails
 *
 * Tokens live in sessionStorage: they survive the page reload the OAuth redirect
 * forces, and are gone when the tab closes. A script able to run on this origin
 * can read them, and no choice available to a browser-only client prevents that -
 * HttpOnly cookies would, but they need a backend endpoint to set them. The real
 * defence is a strict CSP and no third-party scripts. The reasoning, including
 * why an HttpOnly cookie was not chosen, is in
 * docs/adr/0009-keep-tokens-in-sessionstorage.md.
 */

import { challengeFor, fromBase64url, randomVerifier } from "./pkce.js";

const CONFIG = {
  hostedUi: import.meta.env.VITE_COGNITO_HOSTED_UI,
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID,
  redirectUri: import.meta.env.VITE_REDIRECT_URI ?? window.location.origin,
  // openid for an ID token at all; email because the upload endpoint reads that
  // claim to attribute a record, and dropping it makes attribution unavailable.
  scope: "openid email profile",
};

const KEY_TOKEN = "wildlens.idToken";
const KEY_VERIFIER = "wildlens.pkceVerifier";

/** Send the browser to the Hosted UI. Does not return. */
export async function signIn() {
  const verifier = randomVerifier();

  // Stored before the redirect, read after it. Losing it makes the code
  // unexchangeable, and the error says only invalid_grant.
  sessionStorage.setItem(KEY_VERIFIER, verifier);

  const query = new URLSearchParams({
    response_type: "code",
    client_id: CONFIG.clientId,
    redirect_uri: CONFIG.redirectUri,
    scope: CONFIG.scope,
    code_challenge: await challengeFor(verifier),
    code_challenge_method: "S256",
  });

  window.location.assign(`${CONFIG.hostedUi}/oauth2/authorize?${query}`);
}

/**
 * If this page load is the redirect back from Cognito, exchange the code.
 * Returns true if a token was obtained, false if there was no code to exchange.
 */
export async function completeSignIn() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");

  if (params.get("error")) {
    throw new Error(`${params.get("error")}: ${params.get("error_description") ?? ""}`);
  }
  if (!code) {
    return false;
  }

  const verifier = sessionStorage.getItem(KEY_VERIFIER);
  if (!verifier) {
    // Usually a reload of the redirect URL after the verifier was consumed, or a
    // flow started in another tab. Saying so is better than reporting
    // invalid_grant from Cognito.
    throw new Error("no PKCE verifier for this sign-in. Start again from the sign-in button.");
  }

  const response = await fetch(`${CONFIG.hostedUi}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CONFIG.clientId,
      redirect_uri: CONFIG.redirectUri,
      code,
      code_verifier: verifier,
    }),
  });

  if (!response.ok) {
    throw new Error(`token exchange failed: ${response.status} ${await response.text()}`);
  }

  const tokens = await response.json();

  // The ID token, not the access token. API Gateway's Cognito authoriser
  // validates the ID token and returns 401 with no explanation for the other one,
  // which is the single most likely cause of an unexplained 401 in this system.
  sessionStorage.setItem(KEY_TOKEN, tokens.id_token);
  sessionStorage.removeItem(KEY_VERIFIER);

  // Strip the code from the address bar so a reload does not retry an exchange
  // that can only fail - a code is single-use.
  window.history.replaceState({}, "", window.location.pathname);

  return true;
}

export function idToken() {
  return sessionStorage.getItem(KEY_TOKEN);
}

/** Claims of the stored token, or null. Not verified - API Gateway does that. */
export function claims() {
  const token = idToken();
  if (!token) return null;

  const [, payload] = token.split(".");
  return JSON.parse(fromBase64url(payload));
}

/** Is the stored token present and not expired? Treats the last minute as expired. */
export function isSignedIn() {
  try {
    const expiry = claims()?.exp ?? 0;
    return expiry - 60 > Date.now() / 1000;
  } catch {
    return false;
  }
}

export function signOut() {
  sessionStorage.removeItem(KEY_TOKEN);
  sessionStorage.removeItem(KEY_VERIFIER);
  const query = new URLSearchParams({
    client_id: CONFIG.clientId,
    logout_uri: CONFIG.redirectUri,
  });
  window.location.assign(`${CONFIG.hostedUi}/logout?${query}`);
}
