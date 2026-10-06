#!/usr/bin/env bash
# Build the client and publish it.
#
#   ./scripts/deploy-web.sh
#
# Two steps that both matter: sync the files, then invalidate index.html. Vite
# fingerprints every asset filename, so new JavaScript and CSS arrive under names
# CloudFront has never seen and need no invalidation at all. index.html is the one
# file whose name never changes — skip the invalidation and the deployed site keeps
# loading the previous build's assets, which are still in the bucket, so nothing
# visibly breaks and the change simply does not appear.

set -euo pipefail

ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../infra/terraform/envs/dev" && pwd)"
WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../web" && pwd)"

say() { printf '\033[36m==>\033[0m %s\n' "$1"; }

BUCKET="$(terraform -chdir="$ENV_DIR" output -raw web_bucket_name)"
DISTRIBUTION="$(terraform -chdir="$ENV_DIR" output -raw cdn_distribution_id)"
SITE="$(terraform -chdir="$ENV_DIR" output -raw site_url)"

say "Building"
# The build is also the only check that every import resolves: the test suite
# covers the pure modules, not the wiring between them.
(cd "$WEB_DIR" && npm run build)

say "Syncing to s3://$BUCKET"
# Fingerprinted assets get a long cache; index.html gets none, so a viewer always
# re-reads it and picks up the new asset names. --delete removes the previous
# build's assets, which is safe only because index.html is uploaded after them.
aws s3 sync "$WEB_DIR/dist" "s3://$BUCKET" \
  --exclude index.html \
  --cache-control "public, max-age=31536000, immutable" \
  --delete

aws s3 cp "$WEB_DIR/dist/index.html" "s3://$BUCKET/index.html" \
  --cache-control "no-cache, must-revalidate" \
  --content-type "text/html; charset=utf-8"

say "Invalidating index.html"
# Only index.html. A wildcard invalidation would also clear the fingerprinted
# assets, which never need it, and the first 1,000 paths a month are free while
# the rest are billed per path.
ID="$(aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION" \
  --paths /index.html / \
  --query Invalidation.Id --output text)"
echo "    invalidation $ID"

say "Done"
echo "    $SITE"
