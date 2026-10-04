# 9. Keep tokens in sessionStorage, and defend with CSP instead

**Status:** accepted · Phase 7 · 2026-10-04

## Context

Sign-in uses the authorization code flow with PKCE against the Cognito Hosted UI.
The flow ends with the browser holding an ID token, which every API request
carries.

The flow forces a full page navigation — out to Cognito and back — so the token
cannot simply live in a variable: it has to survive at least that one reload.

There is no option that keeps a token out of JavaScript's reach in a
browser-only client. Code flow hands the token to the page, by design.

## Decision

`sessionStorage`, under `wildlens.idToken`. The PKCE verifier goes in the same
place and is removed as soon as it has been used.

The actual defence is a strict `Content-Security-Policy` in `index.html` and no
third-party scripts on the page.

## Consequences

| Option | Survives reload | Survives tab close | Readable by page scripts |
|---|---|---|---|
| A variable | ✗ | ✗ | yes, while it exists |
| **`sessionStorage`** | **✓** | **✗** | **yes** |
| `localStorage` | ✓ | ✓ | yes |
| `HttpOnly` cookie | ✓ | ✓ | **no** |

**A variable does not survive the OAuth redirect,** which rules it out before any
security argument.

**`sessionStorage` over `localStorage`** because the difference is free. Both are
equally readable by a script on this origin, so the choice buys nothing against
XSS — it only limits how long a token sits on a shared or unattended machine. A
token lasts an hour, so persisting it across tab closes would mostly mean leaving
it somewhere after the user had finished.

**An `HttpOnly` cookie is genuinely safer and was not chosen.** JavaScript cannot
read one, so an injected script could not exfiltrate the token — though it could
still *use* the session by making requests, which is the part people often skip
over when recommending cookies. It needs an endpoint that receives the
authorization code, performs the exchange server-side, and sets the cookie, plus
`SameSite` handling and a CSRF defence for state-changing requests. That is a
Lambda, a route, and a second authoriser mode, to close one of the two things XSS
would give an attacker.

**So the token storage is not the defence, and saying otherwise would be the real
mistake here.** If a script can run on this origin, the session is compromised
under every row of that table. What prevents that is the policy the page ships:

    default-src 'self'; connect-src 'self' https://*.amazoncognito.com;
    img-src 'self' https://*.amazonaws.com data:; object-src 'none'; base-uri 'none'

`connect-src` names the Cognito domain because the token exchange posts there
directly. The API needs no entry: it is reached through a relative `/api` path,
same-origin in both environments.

**One accepted weakness:** `style-src` allows `unsafe-inline`. The page sets no
inline styles, but removing the allowance entirely would break the `hidden`
attribute toggling. It permits style injection, not script execution.

## What would change this

An `HttpOnly` cookie becomes the right answer as soon as there is a reason to
write a token-exchange endpoint anyway — a refresh-token rotation flow, or a
second client. Until then it is a Lambda and an authoriser mode to partially
close a hole that CSP closes more completely.

Adding any third-party script to this page — an analytics tag, a font loader, a
widget — invalidates the whole argument, because the CSP would have to admit an
origin whose contents somebody else controls. That is the line to watch, and it
is a cheaper line to hold than a cookie flow.
