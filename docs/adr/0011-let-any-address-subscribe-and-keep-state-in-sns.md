# 11. Let any address subscribe, and keep subscription state in SNS

**Status:** accepted · Phase 8 · 2026-10-26

## Context

Phase 8 emails a person when a newly tagged file contains a species at or above a
count they chose. The design document fixed the shape: one SNS topic, email
subscriptions, a filter policy per subscriber, and `POST /subscribe` behind the
Cognito authoriser. It left open who may be subscribed, where the subscription is
stored, and what happens when sending fails.

## Decision

**Any signed-in user may subscribe any email address.** This was the owner's
choice over taking the address from the caller's Cognito claim. The cost is that a
signed-in user can make SNS send a confirmation mail to a stranger, and can add or
remove species on someone else's address. What limits it: the address must look
like an address and be at most 254 characters; the species must be in
`services/api/species.txt`; at most 20 species per address and 200 subscriptions
on the topic; the route is throttled to 1 request a second (burst 5). A recipient
can always unsubscribe with the link SNS puts in every email.

**The response is shortened.** `{ok, status, tag, min}` replaces the design's
`{ok, tags}`, because returning the address's whole subscription would let any
user read what a stranger follows. `min` is the value the caller sent, not the
stored one: the stored threshold can be lower, and echoing it would let anyone read
it with one add and restore the state with one remove.

**SNS holds all the state.** One subscription per address; its filter policy,
applied to the message body, is the subscription. Several species are ORed with a
top-level `$or`. A species is matched with `numeric >= min`.

**Publishing is best effort.** `process` publishes after the record reaches
`DONE`. If the publish fails it logs and carries on. Raising would make SQS
redeliver, `claim()` would see `DONE` and skip the file, and the notification
would be lost anyway while a correctly tagged file was reported as failed.

| Considered | Why not |
|---|---|
| The caller's own address only | Safest, but the owner chose to allow any address |
| A DynamoDB table of wishes, synced to SNS | SNS does not report when an address confirms, so the table and SNS would drift and need a reconciler |
| `process` fans out itself through SES | Abandons the SNS design and adds a verified domain |
| Filtering on message attributes | SNS allows 10 attributes per message and a file can carry more species |
| Raising on a failed publish | Loses the notification and fails a good file, see above |

## Consequences

**A pending address cannot be changed, and is not mailed again.** SNS does not allow
editing a subscription until its address confirms, so adding or removing a species
for such an address is a `409 pending_confirmation` and nothing more. A resend was
in the first design and was dropped after review: the route's throttle is shared by
all callers, so it could not stop one user sending a stranger about 86,000
confirmation mails a day. The price is that a lost confirmation mail has no
self-service resend; it waits for the pending subscription to expire, or the owner
removes it (runbook). A signed-in user can also fill the 200 slots with pending
addresses in minutes, and new addresses then get `limit_reached` until those expire.
The limits bound the damage; they do not prevent it.

**The email body is the JSON message.** The filter reads the body, so the body has
to be the JSON. A friendlier text would need `MessageStructure=json`, whose
interaction with body-scope filters has not been checked.

**A species with no common name cannot be subscribed to.** `ml/labels.txt` gives
most classes a common name. A genus-only class such as `rattus` falls back to its
scientific name in the tagger, and is not in `species.txt`.

**An SNS outage can lose a notification.** There is no retry. The log line carries
the correlation id and the runbook has a query for it.

**`species.txt` is a copy.** The API artefact is zipped from `services/api/`
alone, so it cannot read `ml/labels.txt`. A test fails if the two drift.

**What was measured against the real service (2026-10-26).** A body-scope policy
of `dingo >= 3` OR `koala >= 1` delivered `{"dingo": 3}` and `{"koala": 1}` and
dropped `{"dingo": 2}` and `{"emu": 9}`. A plain one-clause policy was accepted,
and a confirmed subscription's policy could be replaced with
`SetSubscriptionAttributes`. Not yet checked, because it needs a real address to
send a confirmation to: whether a pending subscription can be unsubscribed. What
`Subscribe` does when called again for a pending address no longer matters,
because nothing calls it again.
