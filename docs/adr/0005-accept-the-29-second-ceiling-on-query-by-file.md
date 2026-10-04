# 5. Accept the 29-second ceiling on query-by-file

**Status:** accepted · Phase 6 · 2026-10-01

## Context

`/search/byfile` takes an image, identifies what is in it, and returns stored
files containing at least the same species. Identification means running the
two-stage model, which lives in exactly one function.

The search function therefore invokes the tagging function synchronously in query
mode — a path that writes nothing to S3 or DynamoDB, because not persisting the
sample is a requirement rather than an optimisation.

**API Gateway REST cuts an integration off at 29 seconds, and the limit cannot be
raised.** Set against the Phase 5 measurements:

| Tagging function state | Time | Fits in 29 s? |
|---|---|---|
| Warm | 6.4 s | yes, comfortably |
| Cold, p50 | 20.1 s | yes, with 9 s to spare |
| First invocation after a deploy | 42.3 s | **no** |

So the route works most of the time and fails in the one state a demo is
guaranteed to hit, because a demo follows a deploy.

## Decision

Accept it. Set the client's read timeout to 25 s so the failure is caught inside
the function, and return `503` with the cause and a retry delay:

    {"error": "the model is warming up, retry in about 30 seconds", "retryAfter": 30}

## Consequences

**The failure is legible.** Letting the gateway cut first produces a bare `504`
with no body and nothing naming the cause. Timing out at 25 s is what lets this
code choose the error the caller sees.

**The client needs retry logic for one route.** Documented in the response rather
than left to be discovered.

**The invoke client must not retry, and this is the subtle part.** botocore
retries a timed-out invocation by default — and the request it retries is a
twenty-second inference that is still running. That pays twice for one answer.
Query mode writes nothing, so the idempotency guard protecting the upload path
does not apply: there is no record to refuse a second time. `total_max_attempts`
is 1.

This was observed before it was reasoned about: one `aws lambda invoke` during
benchmarking produced two invocations 11 seconds apart, the first running 56.7 s
and the second returning in 6.5 s because the idempotency guard caught it. On the
upload path the guard saves the duplicate. Here there would be no guard.

**The query file is capped at 4 MB, and the first version of this number was
wrong.** It said 7 MB, reasoning from API Gateway's documented 10 MB request cap.
The binding limit is Lambda's: a proxy integration invokes the function
synchronously, and a synchronous invocation payload is capped at **6 MB** holding
the whole event — the base64 body plus the headers and `requestContext` API Gateway
wraps around it. The gateway refuses anything larger with `413 Request Too Long`,
naming nothing about Lambda.

Measured after the fact: 2.7 MB of base64 passed, 8.3 MB was refused. 4 MB of
original file encodes to about 5.3 MB and leaves room for the wrapper.

The browser checks before sending, because a request the gateway refuses never
reaches this project's code and the caller otherwise gets a bare 413. Unrelated to
the 25 MB upload limit, because that path sends bytes straight to S3 and never
through the API — which is precisely why it can be larger.

## A correction: the 503 path could not run

The timeout reasoning above was correct and the deployment contradicted it.

The `search` function was created with a 10-second timeout, from a map whose
docstring read "one DynamoDB call and some signing, so they finish in
milliseconds". That was written when the map held only `status`. It is true of two
of the three handlers, and reusing it for the one that waits on an ML inference
meant the function was killed at 10 s — fifteen seconds before the `read_timeout`
it was written to respect, and well before a cold tagging function could answer.

API Gateway returned `502`, and the `503` "model is warming up" response described
above **was unreachable in production**. It had been exercised only by a unit test.

An earlier manual test did pass, in 7.9 s, because the tagging function happened to
be warm. It was inside the limit by two seconds, by luck.

Timeouts are now per function, with the ordering stated where they are set:

    boto3 read_timeout   25 s   < the function timeout, so the handler catches it
    search timeout       28 s   < the gateway, so the handler answers first
    API Gateway          29 s   hard, cannot be raised

The general shape is one this project has now hit three times: **a value that was
correct when written became wrong when a new case joined the same map, and nothing
connected the two changes.** The others were the 30-day object expiry (ADR 8) and
the one-hour `FAILED` expiry.

## Alternatives considered, with prices

| Option | Removes the failure? | Cost |
|---|---|---|
| **Accept and report** | no | nothing |
| Provisioned concurrency = 1 | yes | **~$15/month** |
| Make the route asynchronous | yes | a second polling flow and a job record |

Provisioned concurrency is 15% of this project's entire budget for one route.
Rejected on that basis alone.

The asynchronous option is the one that would be right at scale, and it is
genuinely better engineering: it is how `/upload` already works, so the pattern
exists. It was rejected for scope — it needs a job table, a status endpoint for
jobs distinct from the one for files, and a client that polls twice for different
things. For a route whose purpose is "show me pictures like this one", a `503`
telling the caller to try again in thirty seconds is a smaller lie than an
architecture built to avoid saying it.

## What would change this

Phase 9's dashboard will show how often this route is hit cold. If
`/search/byfile` becomes something people use repeatedly rather than demonstrate
once, the asynchronous version is the answer — not provisioned concurrency, which
pays continuously to avoid a problem that occurs occasionally.
