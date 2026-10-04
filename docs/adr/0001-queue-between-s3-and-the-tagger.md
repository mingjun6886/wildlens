# 1. Put a queue between S3 and the tagging function

**Status:** accepted · Phase 4 · 2026-09-29

## Context

An object landing in the raw bucket has to reach the tagging function. S3 can
invoke a Lambda directly, which is the shorter path and the one the original
coursework used.

The function it invokes is not an ordinary handler. It holds 470 MB of model
weights, runs for 6 to 20 seconds, and is configured at 3008 MB — so each
concurrent execution costs roughly 50 times what a typical API handler does.

## Decision

S3 publishes an `ObjectCreated` notification to an SQS queue. The function
consumes the queue through an event source mapping with
`maximum_concurrency = 2`, and messages that fail three times move to a
dead-letter queue with an alarm on its depth.

## Consequences

**A cost ceiling exists, and it is a number in a file.** Dropping 500 images into
the bucket produces 500 messages and two workers, not 500 concurrent executions.
The bounded worst case — two workers at 3 GB for 900 s, continuously, for eight
hours — is about $4. The same burst invoking Lambda directly is about $80.

**Failure is observable.** A direct invocation retries twice on an internal
schedule and then discards the event. There is no record of what was lost. A
message that fails three times here is still in the dead-letter queue a fortnight
later, and the alarm says so within five minutes.

**Delivery is at-least-once, so the worker must be idempotent.** This is the cost
of the decision, not an incidental detail. The same object can arrive twice, and
without a guard the second delivery runs inference again and, from Phase 8, sends
a second email. The guard is a conditional write — `attribute_not_exists(fileId)
OR status <> DONE` — and the handler catches `ConditionalCheckFailedException`,
logs at INFO, and **exits successfully**. Raising would return the message to the
queue and loop.

**The visibility timeout is now a load-bearing number.** While a consumer holds a
message SQS hides it; if the worker is still running when that window closes, SQS
hands the message to another worker and a second copy of a healthy job begins.
The rule is at least six times the function timeout, so 5400 s against a 900 s
function. Sized against the **maximum**, not the measured 6–20 s average, because
a video or a frame with many animals can approach the ceiling.

**One more hop to trace.** A correlation id now has to survive S3's notification
format rather than being generated in one place.

## Alternatives considered

**S3 invoking Lambda directly.** Rejected for the three reasons above. Worth
noting that it also has no place to put a poison message: the only options are
retry and discard.

**`reserved_concurrent_executions` on the function instead of
`maximum_concurrency` on the mapping.** AWS refuses a reservation unless at least
100 unreserved executions remain account-wide, and this account is capped at 10,
so the function-level setting is rejected outright. The mapping-level setting is
better regardless: it bounds only the queue consumer, leaving headroom for the
direct invocations that `/search/byfile` makes.

**EventBridge instead of SQS.** More routing flexibility, no queue depth to alarm
on, and no dead-letter queue without extra configuration. The flexibility is not
needed; the queue depth is.
