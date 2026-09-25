#!/usr/bin/env bash
# Build the tagging container and push it to ECR.
#
#   ./scripts/build-and-push.sh [tag]
#
# Defaults to the tag "latest". CI will call this same script in Phase 10, which
# is why the registry URL is read from Terraform rather than hard-coded: there is
# one source of truth for where the image lives, and it is the state file.

set -euo pipefail

TAG="${1:-latest}"
REGION="ap-southeast-2"
ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../infra/terraform/envs/dev" && pwd)"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\033[36m==>\033[0m %s\n' "$1"; }
die() { printf '\033[31mError:\033[0m %s\n' "$1" >&2; exit 1; }

docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker Desktop and retry."

say "Reading the registry URL from Terraform state"
REGISTRY="$(terraform -chdir="$ENV_DIR" output -raw ecr_repository_url)"
[ -n "$REGISTRY" ] || die "ecr_repository_url is empty. Has terraform apply run?"
echo "    $REGISTRY:$TAG"

say "Authenticating Docker against ECR"
# The password is a short-lived token, not a stored credential. It is piped
# straight into docker login so it never reaches the shell history or a file.
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${REGISTRY%%/*}"

say "Building and pushing (this takes 20-40 minutes on a first run)"
# --platform linux/amd64: the build host is arm64, Lambda is x86_64. Omitting
#   this produces an image that runs locally and dies on Lambda with an
#   exec-format error that names no cause.
#
# --provenance=false: buildx otherwise pushes an OCI image index carrying build
#   attestations. Lambda cannot resolve an index and rejects the image with
#   "The image manifest is not supported", which also does not name the cause.
#
# The build context is the repository root, because the image needs ml/ as well
# as services/. .dockerignore keeps .terraform and .venv out of what gets sent.
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --file "$REPO_ROOT/services/process/Dockerfile" \
  --tag "$REGISTRY:$TAG" \
  --push \
  "$REPO_ROOT"

say "Verifying the image landed"
aws ecr describe-images \
  --repository-name "${REGISTRY##*/}" \
  --region "$REGION" \
  --query "sort_by(imageDetails,&imagePushedAt)[-1].{tag:imageTags[0],sizeMB:imageSizeInBytes,pushed:imagePushedAt}" \
  --output table

say "Done. Terraform deploys the function from $REGISTRY:$TAG"
