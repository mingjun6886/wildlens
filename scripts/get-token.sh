#!/usr/bin/env bash
# Print a Cognito ID token for the integration-test user.
#
#   TOKEN=$(./scripts/get-token.sh)
#   curl -H "Authorization: $TOKEN" "$API/files/$FILE_ID"
#
# The ID token, not the access token: API Gateway's COGNITO_USER_POOLS authoriser
# validates the ID token, and sending the access token returns 401 with no
# explanation of why.
#
# The password lives in SSM Parameter Store as a SecureString rather than in a
# file here. Phase 10's CI reads the same parameter through its OIDC role, so
# there is one copy of the credential and no path by which it reaches the repo.

set -euo pipefail

ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../infra/terraform/envs/dev" && pwd)"
PARAM="${TOKEN_PARAM:-/wildlens/dev/test-user-password}"
USERNAME="${TEST_USERNAME:-boiviemlava123@gmail.com}"

POOL_ID="$(terraform -chdir="$ENV_DIR" output -raw cognito_user_pool_id)"
CLIENT_ID="$(terraform -chdir="$ENV_DIR" output -raw cognito_client_id)"

PASSWORD="$(aws ssm get-parameter --name "$PARAM" --with-decryption \
  --query Parameter.Value --output text)"

# --auth-parameters takes key=value pairs; a password containing a comma would
# split into two parameters, so the generator that created it uses an alphabet
# without one.
aws cognito-idp admin-initiate-auth \
  --user-pool-id "$POOL_ID" \
  --client-id "$CLIENT_ID" \
  --auth-flow ADMIN_USER_PASSWORD_AUTH \
  --auth-parameters "USERNAME=$USERNAME,PASSWORD=$PASSWORD" \
  --query AuthenticationResult.IdToken \
  --output text
