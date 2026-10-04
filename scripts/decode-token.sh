#!/usr/bin/env bash
# Print the claims of a Cognito JWT, and how long it has left.
#
#   ./scripts/decode-token.sh "$(./scripts/get-token.sh)"
#   ./scripts/decode-token.sh            # reads from stdin
#
# This exists because the obvious one-liner does not work. A JWT payload is
# base64url with the padding stripped, and `base64 -d` rejects or truncates it —
# which produced a confusing parse error in the first version of the runbook. The
# padding has to be restored before decoding.
#
# Verifying a signature is not this script's job. API Gateway does that, and the
# GCP function in Phase 11 does it independently. This answers "what is in the
# token I am holding", which is what every 401 investigation starts with.

set -euo pipefail

TOKEN="${1:-$(cat)}"

python3 - "$TOKEN" <<'PY'
import base64
import json
import sys
import time

token = sys.argv[1].strip()
parts = token.split(".")
if len(parts) != 3:
    sys.exit(f"not a JWT: expected three dot-separated parts, got {len(parts)}")

payload = parts[1]
payload += "=" * (-len(payload) % 4)   # restore the stripped base64url padding
claims = json.loads(base64.urlsafe_b64decode(payload))

interesting = ("token_use", "email", "email_verified", "iss", "aud", "cognito:username")
for name in interesting:
    if name in claims:
        print(f"{name:18} {claims[name]}")

for name in sorted(set(claims) - set(interesting) - {"exp", "iat", "auth_time"}):
    print(f"{name:18} {claims[name]}")

remaining = claims.get("exp", 0) - int(time.time())
state = f"{remaining}s left" if remaining > 0 else f"EXPIRED {-remaining}s ago"
print(f"{'expiry':18} {state}")

if claims.get("token_use") != "id":
    print()
    print("WARNING: API Gateway's Cognito authoriser validates the ID token.")
    print("This is the access token, and it returns 401 with no explanation.")
PY
