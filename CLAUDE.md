# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WildLens: a serverless wildlife-tagging platform on AWS (ap-southeast-2), all infrastructure in Terraform. Uploaded camera-trap images/video are tagged by a two-stage ML pipeline (MegaDetector → SpeciesNet) and queried through a Cognito-protected REST API. Design spec: `docs/design/2026-09-22-architecture.md`; per-decision rationale: `docs/adr/`; operations: `docs/runbook.md`.

## Commands

Python is pinned to 3.11 (`.python-version`); the venv is `.venv`, dev tooling in `requirements-dev.txt`.

```bash
make check        # lint + test + test-web: everything CI is meant to run
make lint         # ruff check .
make fmt          # ruff format . && ruff check --fix .
make test         # pytest -m "not integration" (models mocked, no AWS)
make test-web     # node --test web/src/*.test.mjs (no browser, no npm install needed)
make build-web    # vite build; also the only check that web imports resolve

pytest services/api/tests/test_search.py::test_name   # single Python test
node --test web/src/hash.test.mjs                     # single web test file
python ml/eval/run_eval.py                            # score tagger against ml/eval/answer_key.yaml, writes ml/eval/reports/<version>.md
```

`integration`-marked tests hit real AWS and are skipped unless `AWS_PROFILE` is set.

Deploy/ops (always `export AWS_PROFILE=wildlens` first; Terraform output is the source of truth for every bucket/queue/URL name — never hard-code them, the random suffix changes on destroy/recreate):

```bash
terraform -chdir=infra/terraform/envs/dev plan|apply|output
./scripts/build-and-push.sh [tag]   # build the tagging container, push to ECR (needs Docker; registry URL read from Terraform)
./scripts/deploy-web.sh             # vite build, S3 sync, CloudFront invalidation of index.html
./scripts/get-token.sh / decode-token.sh   # Cognito token for calling the API
```

Enable the pre-commit hook once per clone: `git config core.hooksPath .githooks`.

## Architecture

- **Ingest path:** S3 raw bucket → SQS (+DLQ, maxReceiveCount 3) → container Lambda `process` (`services/process/`, 4 GB/900 s, reserved concurrency 2) → DynamoDB + thumbnails bucket + SNS. The queue and concurrency cap are the cost ceiling (ADR 1). Nothing consumes the DLQ by design; see the runbook.
- **Record states:** `PENDING` (written by the upload API) → `PROCESSING` → `DONE` | `FAILED`. Permanent failures write `FAILED` and are not retried; transient ones raise so SQS retries.
- **API:** API Gateway **REST** (not HTTP API, ADR 3) with a Cognito authoriser on every route; Python 3.11 zip Lambdas in `services/api/` (`upload`, `search`, `status`). They share `common.py` and ship as **one artefact**, so response shape and CORS headers stay identical. Search scans DynamoDB and filters in memory (ADR 4); query-by-file is bounded by the 29 s API Gateway limit (ADR 5).
- **Flat packaging:** Lambda code is copied flat, not installed as packages. `ml/tagger.py` is `import tagger` everywhere, and `services/process/Dockerfile` must be built from the **repo root** because it needs `ml/`. Ruff's `known-first-party` in `pyproject.toml` lists these flat modules (`app`, `common`, `search`, `tagger`, …) — add new ones there or import sorting breaks. Tests make `ml/` importable via `ml/tests/conftest.py` (sys.path), mirroring runtime layout.
- **Model loading:** weights are fetched from S3 at cold start, **not baked into the image** (ADR 2 — baking was built, measured, and reversed). The module-level cache holds the *loaded* detector, not a path to weights.
- **Web client (`web/`):** framework-free Vite app, Cognito Hosted UI with PKCE; tokens in `sessionStorage` with CSP as the defence (ADR 9). Browser uploads PUT directly to S3 via presigned URL, so the S3 bucket needs its own CORS rule and the page CSP must allow connecting to S3. Everything else is same-origin through one CloudFront distribution that fronts both the site and the API.
- **Terraform:** `infra/terraform/envs/dev` composes modules in `infra/terraform/modules/` (storage, database, queue, registry, compute, api, api_functions, auth, cdn). Dev only, no VPC (avoids NAT cost). Provider pins `allowed_account_ids`, so a wrong `AWS_PROFILE` refuses to run. State is in S3 with lockfile. One IAM role per function (ADR 7). `terraform destroy` is expected between sessions to save credit; the api_functions module builds into `.build/` (removed by `make clean`).

## Conventions that bite

- **English only.** The pre-commit hook (`.githooks/check_language.py`) rejects Vietnamese characters in `.md/.py/.tf/.js/.json/.sh/.txt/.yaml/.html` files — this includes code comments, docs and commit-bound text. It also blocks model weights, `*.tfstate`, files over 5 MB, and AWS credentials.
- **Makefile recipes use literal TABs.**
- Ruff config: line length 100, rules `E W F I B C4 UP S T20` (so no stray `print()`, bandit checks on); tests may `assert` and use throwaway credentials.
- ADRs are written when a decision is made and reversed decisions are kept, not rewritten; add a new numbered ADR (and a row in `docs/adr/README.md`) for significant decisions rather than editing old ones.
- Docker builds on Apple Silicon need `--platform=linux/amd64` or the image fails on Lambda with an unexplained exec-format error.
- **CI** (`.github/workflows/`): `ci.yml` is credential-free (lint, tests, web build, `terraform fmt/validate`, checkov). `plan.yml` runs a read-only `terraform plan` on PRs that touch `infra/**`, via an OIDC role from `modules/ci`; CI never applies (ADR 10). Pin every action to a commit SHA. Suppress a checkov finding only with `#checkov:skip=<ID>:<reason>` on the resource, never by disabling a check group. The tests need CPU `torch` and `pillow`, which `requirements-dev.txt` does not list; `ci.yml` installs them separately.
- Code comments in this repo explain *why* and record measured numbers/failures; match that style.
