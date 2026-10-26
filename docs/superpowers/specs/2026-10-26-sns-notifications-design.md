# Phase 8 — Tag notifications through SNS

**Date:** 2026-10-26
**Status:** Approved; implementation in progress
**Scope:** `services/process/`, `services/api/` (new `subscribe` function), `infra/terraform/modules/notify` (new), `infra/terraform/modules/api_functions`, `infra/terraform/modules/api`, `infra/terraform/modules/compute`, documentation

---

## 1. Goal

A person who subscribes an email address to a species and a minimum count receives an email when a newly tagged file reaches that count. Success means: subscribe, confirm through SNS's own email, upload an image containing the species at or above the threshold, receive one email, and receive nothing for files that do not match.

The design document (`docs/design/2026-09-22-architecture.md`) already fixes the shape: one SNS topic `wildlens-tags`, email subscriptions, a per-subscriber filter policy, and `POST /subscribe` behind the Cognito authoriser.

### Decisions already taken

| # | Decision | Reason |
|---|---|---|
| D1 | `POST /subscribe` accepts **any** email address, not only the caller's own | Chosen by the owner. The cost, and what limits it, is in section 4.4 and ADR 11 |
| D2 | A subscription is a species plus a **minimum count**, for example `dingo >= 3` | Mirrors `/search/tags`. The design document's `{email, tag, operation}` gains a `min` field |
| D3 | **SNS holds all subscription state.** No DynamoDB table, no SES | The filter policy is the state. A second store would need reconciling with SNS, which does not tell us when an address is confirmed |
| D4 | Filters apply to the **message body** (`FilterPolicyScope=MessageBody`), not message attributes | SNS allows at most 10 message attributes per message, and a file can carry more than 10 species |
| D5 | A failed publish is logged and **does not fail the file** | See section 3.3 |
| D6 | No web form. The API only | Owner's decision |

### Out of scope

A subscription form in `web/`; retries or a dead-letter queue for notifications; digest emails that combine several files; image links in the email; SES; an alarm on failed publishes (the runbook gets a log query instead); video (Phase 12).

---

## 2. Components

| Unit | Purpose | Depends on |
|---|---|---|
| `modules/notify` | The topic `wildlens-tags` and its output ARN | nothing |
| `services/process/app.py` | Publishes one message per file that reaches `DONE` | the topic ARN, `sns:Publish` on it |
| `services/api/subscribe.py` | Adds and removes species on an email's subscription | `common.py`, the topic ARN, `labels.txt` |
| `services/api/notify_policy.py` | Pure functions: build, parse and merge a filter policy | nothing, so it is tested without AWS |

`subscribe` ships in the same artefact as `upload`, `search` and `status`, so response shape and CORS headers stay identical (see `CLAUDE.md`, API section). `subscribe` and `notify_policy` are added to `known-first-party` in `pyproject.toml`.

---

## 3. Publishing (`process` → SNS)

### 3.1 Where

Immediately after `record_success(...)` in `process_object`. Idempotency comes from what is already there: a redelivered SQS message reaches `claim()`, which returns `skipped: already DONE` before this point, and so does a second upload of the same file. `query_mode` never reaches this code and therefore never publishes, as the design document requires.

### 3.2 Message

JSON body:

```json
{"fileId": "<sha256>", "tags": {"dingo": 3, "koala": 1}}
```

The email body is the JSON message itself, pretty-printed, because subscribers' filters read the body. A friendlier body needs `MessageStructure=json`, whose interaction with body-scope filters is unverified. The subject is `WildLens: new sighting`. There is no image link, because the thumbnail bucket is private and a presigned URL would expire inside an email that may be read days later.

### 3.3 Failure behaviour

If `sns.publish` raises, `process_object` catches the exception, writes a structured error log with the correlation id and the file id, and returns normally. The file stays `DONE`.

Raising would be worse: SQS would redeliver, `claim()` would see `DONE` and skip the file, so the notification would still be lost, and a correctly tagged file would be reported as a failure. The accepted cost is that an SNS outage can lose a notification. It is visible in the logs and there is no automatic retry. The runbook gets a Logs Insights query for it.

### 3.4 Configuration

`TAGS_TOPIC_ARN` is added to the `process` function's environment. Its role gains `sns:Publish` on that one topic and nothing else (ADR 7).

---

## 4. Subscribing (`POST /subscribe`)

### 4.1 Contract

Request: `{email, tag, min, operation}`

- `operation` is `"add"` or `"remove"`.
- `min` is an integer from 1 upward and is required for `add`.

Response for `add`: `{ok, status, tag, min}` where `status` is `"pending"` (the address has not confirmed) or `"confirmed"`. For `remove`: `{ok, status, tag}` with `status` `"confirmed"`, or `"none"` when the address was not subscribed; there is no `min`.

The design document returned `{ok, tags}`, the address's whole subscription. Because D1 lets any signed-in user name any address, that would let anyone probe which species a stranger follows. The response therefore echoes only the species just changed. ADR 11 records the deviation.

### 4.2 Filter policy

One SNS subscription per email address. Its filter policy is the state:

```json
{"$or": [
  {"tags": {"dingo": [{"numeric": [">=", 3]}]}},
  {"tags": {"koala": [{"numeric": [">=", 1]}]}}
]}
```

`notify_policy.py` builds this from a `{species: min}` map and parses it back. Top-level `$or` is needed because keys in one SNS filter policy are ANDed. If a species is added twice the lower threshold wins.

### 4.3 Flow

The function finds the address's subscription by paging through `list_subscriptions_by_topic` and matching the endpoint.

| Situation | Behaviour |
|---|---|
| No subscription, `add` | `Subscribe` with the first policy and `FilterPolicyScope=MessageBody`. Status `pending`. SNS sends its confirmation email |
| Confirmed, `add` | Read the attributes, merge, `SetSubscriptionAttributes`. Status `confirmed` |
| Confirmed, `remove` | Remove the species. If it was the last one, `Unsubscribe` |
| No subscription, `remove` | `ok` with no change. Removing something that is not there is not an error |
| Pending, `add` or `remove` | `409 pending_confirmation`, the message says to confirm through the email, and the confirmation email is sent again. SNS does not allow changing attributes of an unconfirmed subscription |

### 4.4 Validation and limits

These are the mitigations for D1. Anyone signed in can cause SNS to send a confirmation email to a stranger, and can add or remove species on another person's address. A recipient can always unsubscribe with the link SNS puts in every email.

- Email: a valid address, at most 254 characters.
- `tag` must be in `services/api/species.txt`, a copy of the common names in `ml/labels.txt` kept equal by a test, because the API artefact cannot see `ml/`. No one can invent a species.
- At most 20 species per address, and at most 200 subscriptions on the topic. The second limit also bounds the cost of paging through the subscription list.
- A throttle on the `/subscribe` route in API Gateway lower than on the other routes.

### 4.5 Errors

`400` for invalid input. `409 pending_confirmation` as above. `409 limit_reached` for either limit. `502` when SNS fails: the detail goes to the structured log with the correlation id, and the caller sees a generic message. `401` and `403` come from the authoriser.

### 4.6 Infrastructure

`subscribe` is added to `api_functions` with its own role (ADR 7): `Subscribe`, `Unsubscribe`, `ListSubscriptionsByTopic`, `GetSubscriptionAttributes` and `SetSubscriptionAttributes` on the topic ARN only. The route is added to the `api` module behind the Cognito authoriser. `TAGS_TOPIC_ARN` is in its environment.

---

## 5. Behaviours of SNS checked against the real service

These were remembered rather than verified when the design was written. `services/api/tests/test_sns_behaviour.py` pins each of them; the results below are from 2026-10-26 in `ap-southeast-2`.

1. **Single-element `$or`.** Not tested. A one-species policy is written without `$or` (`notify_policy.build_policy`), so the question no longer matters. A plain single-clause policy was accepted and read back unchanged.
2. **Re-subscribing a pending address.** *Not yet verified.* The test needs an address to send a confirmation mail to (`WILDLENS_TEST_EMAIL`), and none was supplied. Until it runs, the pending flow in section 4.3 rests on the documented behaviour of SNS.
3. **`numeric` against a number inside a JSON body.** Confirmed. With `FilterPolicyScope=MessageBody`, a policy of `dingo >= 3` OR `koala >= 1` delivered `{"dingo": 3}` and `{"koala": 1}`, and dropped `{"dingo": 2}` and `{"emu": 9}`.
4. **Unsubscribing a pending subscription.** *Not yet verified*, for the same reason as 2.

Replacing the filter policy of a confirmed subscription with `SetSubscriptionAttributes` was confirmed. If an answer differs from the assumption, section 4.3 changes and this document is revised.

---

## 6. Testing

**Unit** (no AWS, `sns` mocked the way S3 and DynamoDB already are):

- Publishing: the body has the right shape; a raising `publish` is logged and the file stays `DONE`; a file skipped as already `DONE` publishes nothing; `query_mode` publishes nothing.
- Subscribing: policy build and parse round-trip; merge keeps the lower threshold; removing the last species unsubscribes; `409` while pending; both limits; every validation case; the response never contains other species.

**Integration** (`integration` marker, skipped unless `AWS_PROFILE` is set): the four questions in section 5.

**Manual** (a link in an email cannot be clicked from a test): a short checklist in the runbook: subscribe, confirm, upload a matching image, receive one email, upload a non-matching image, receive none, remove the species.

**Infrastructure:** `terraform fmt`, `validate` and checkov in CI, and the read-only `plan` on the pull request.

---

## 7. Documentation

- **ADR 11** records D1 (any address, the mitigations, the probe-and-tamper exposure), the shortened response, D3 and D5, and the results of section 5. Row added to `docs/adr/README.md`.
- **Runbook:** a "Notifications" section: finding failed publishes in the logs, a subscription stuck at pending, removing a subscription by hand, and the manual checklist.
- **`CLAUDE.md`:** replace "SNS notification is not built yet (Phase 8)" with the new path.
- **`README.md`:** the Notify row already reads correctly; no change.
