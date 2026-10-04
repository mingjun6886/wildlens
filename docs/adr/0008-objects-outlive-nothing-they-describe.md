# 8. Records and the objects they describe expire together

**Status:** accepted · Phase 6 · 2026-10-01

## Context

Three lifetimes were set independently, at different times, for sensible reasons:

| Thing | Lifetime | Set in | Reason given |
|---|---|---|---|
| `raw` and `thumb` objects | 30 days | Phase 1 | stop test data accumulating |
| `PENDING` record | 1 hour TTL | Phase 4 | reap abandoned uploads |
| `DONE` record | **none** — TTL removed | Phase 4 | a finished record is real data |

Phase 6 added `GET /files/{fileId}`, which returns `thumbUrl` and `fullUrl` for a
`DONE` record. That is when the combination became a defect: after 30 days the
record still reports `DONE` with tags and still hands back signed URLs — to
objects S3 has deleted.

Nothing reports it. It surfaces when somebody opens an old link.

## Decision

Objects in `raw` and `thumb` no longer expire. The retention variables remain,
default 0, with a note that they must not be set below the lifetime of a `DONE`
record.

`FAILED` records get an explicit seven-day TTL rather than inheriting the one hour
`claim()` happened to set — a separate correction with the same cause.

## Consequences

**The invariant holds: a `DONE` record's URLs point at objects that exist.**

**Storage grows without bound, and the bound that was removed was protecting
nothing.** Uploads are capped at 25 MB, identical files are deduplicated by digest
before any bytes are sent, and a demo corpus of fifty photographs is about 150 MB
— under half a cent a month. The $20 budget alarm remains the backstop.

**Test churn is handled by `terraform destroy` between sessions,** which is why
the buckets carry `force_destroy = true`. That was already the working practice;
the lifecycle rule was solving a problem that practice had already solved.

**Deleting a record still orphans its objects.** Nothing removes a thumbnail when
a record goes away. Not reachable today — there is no delete endpoint, and
`PENDING` and `FAILED` records expire before a thumbnail exists — but a delete
feature would have to clean up after itself. One orphan from Phase 3 was found
during this work, six days old, dating from before the thumbnail naming convention
changed.

## The general lesson

**A safety guard is also an assumption.** The 30-day expiry was correct when it
was written: at that point no record outlived its data, because there were no
records. It became a defect when Phase 4 removed the TTL from `DONE`, and nothing
connected the two changes.

Guards do not announce when their assumption expires, and they are harder to doubt
than ordinary code because they look like the thing protecting you.

The two corrections share a shape worth naming: both were about **a lifetime
inherited rather than chosen**. `FAILED` kept an hour because nobody set it;
objects kept 30 days because nobody revisited it. An unchosen default is not a
decision, and it cannot be defended when it turns out to be wrong.
