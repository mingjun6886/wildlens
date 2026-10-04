# 3. REST API rather than HTTP API

**Status:** accepted · Phase 6 · 2026-09-30

## Context

API Gateway offers two products. REST API is the older, feature-rich one; HTTP
API is a deliberately minimal rewrite that AWS describes as "designed with
minimal features so that they can be offered at a lower price".

HTTP API costs about a third as much per request, deploys automatically instead of
needing an explicit deployment resource, has a built-in JWT authoriser, and
configures CORS in one declaration rather than an `OPTIONS` method per path. On
configuration effort alone it wins clearly.

## Decision

REST API.

## Consequences

**X-Ray tracing works.** This is the deciding reason: HTTP APIs do not support it.
Choosing HTTP API would start every trace at Lambda, losing the gateway segment —
no authoriser timing, no gateway latency — and one of this project's five success
criteria is that a single trace follows a request from the edge.

**Execution logs are available.** A failing Cognito authoriser returns `401` with
no explanation, and REST APIs can log what the authoriser actually decided. HTTP
APIs cannot. This is the single most likely thing to go wrong in Phase 6 and the
hardest to diagnose without it.

**CORS is manual, and that is the main cost.** Each path needs an `OPTIONS` method
with a mock integration. One `for_each` block covers all of them, so the cost is
paid once — but it is paid, and `OPTIONS` must use `authorization = "NONE"`,
because a preflight carries no `Authorization` header by definition. Requiring
authorisation there makes the preflight fail and the real request never happen.

**The deployment is a separate resource, and forgetting it is silent.** A REST API
has a configuration layer and a deployment that freezes it; changing the
configuration does nothing until a new deployment exists, and nothing reports the
gap. Terraform cannot see the relationship either, so
`aws_api_gateway_deployment` carries a hash over every resource, method,
integration and CORS response. An omitted entry is a route that never deploys
while everything else works. Verified: adding `/upload` showed
`aws_api_gateway_deployment.api must be replaced`, which is the evidence the hash
is doing its job.

**Features paid for and unused.** API keys, usage plans, per-client throttling,
caching, request validation, WAF, canary deployments, private endpoints. None is
needed here.

## Request validation, specifically

REST APIs can validate a JSON schema at the gateway, and this project does not use
it. Validation happens in Python instead, because the gateway's rejection is
generic: a caller cannot tell a malformed digest from an unsupported extension. A
`400` that names the field is worth more than one saved invocation, and these
handlers finish in milliseconds.

## Cost, and a correction

The initial recommendation here was HTTP API, on the grounds that it is about 70%
cheaper per request. That reasoning was wrong twice over.

The percentage is real and the amount is not: at a few thousand requests the
difference is a few cents. Using a percentage to argue a case where the absolute
figure is nil is the same error made in Phase 5 with "the model load takes nine
seconds" — a ratio standing in for a quantity nobody checked.

The X-Ray limitation was also not checked before the recommendation was made. It
took one reading of the comparison table to reverse the conclusion.
