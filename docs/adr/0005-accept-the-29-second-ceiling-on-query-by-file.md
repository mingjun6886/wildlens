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

**The query file is capped at 7 MB.** API Gateway limits a request payload to
10 MB and base64 inflates by a third. Unrelated to the 25 MB upload limit, because
that path never sends bytes through the API — which is why it can be larger.

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
