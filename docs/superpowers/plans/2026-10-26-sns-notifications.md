# Phase 8 SNS Tag Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email a subscriber when a newly tagged file contains a species at or above the count they asked for.

**Architecture:** `process` publishes one JSON message per file that reaches `DONE` to an SNS topic. A new `subscribe` Lambda in the API artefact keeps one email subscription per address whose filter policy (scope `MessageBody`) is the whole subscription state. No table, no SES.

**Tech Stack:** Python 3.11, boto3 SNS, pytest, Terraform (AWS provider), API Gateway REST.

**Spec:** `docs/superpowers/specs/2026-10-26-sns-notifications-design.md`

## Global Constraints

- Branch is `notify/phase-8`. Never commit to `main`.
- English only in every committed file (pre-commit hook rejects Vietnamese characters), including comments and commit messages.
- Ruff: line length 100, rules `E W F I B C4 UP S T20`. No `print()`. New flat modules go into `known-first-party` in `pyproject.toml`.
- Comments explain *why* and record measured numbers, matching the existing files.
- One IAM role per function; the `subscribe` role gets exactly: `sns:Subscribe`, `sns:Unsubscribe`, `sns:ListSubscriptionsByTopic`, `sns:GetSubscriptionAttributes`, `sns:SetSubscriptionAttributes` on the topic ARN. `process` gets `sns:Publish` on the topic ARN only.
- Limits: at most 20 species per address, at most 200 subscriptions on the topic.
- Checkov findings are suppressed only with `#checkov:skip=<ID>:<reason>` on the resource.
- Always `export AWS_PROFILE=wildlens` before any Terraform or AWS command. Never apply without showing the plan first.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A pending address asked to add a species: expect `409 pending_confirmation`, never a silent no-op or a 500 from SNS (Task 4 test `test_pending_add_is_409`).
2. Email case: `Dingo@Example.com` and `dingo@example.com` are the same address, so the second request must find the first subscription instead of creating a duplicate (Task 4 `test_email_match_ignores_case`).
3. `min` given as `true`, `0`, `-1`, `2.5` or `"3"`: expect `400`, because `True` is an `int` in Python and would otherwise pass (Task 4 `test_min_must_be_a_positive_integer`).
4. A body that is a JSON array or lacks fields: expect `400`, not a `KeyError` (Task 4 `test_body_must_be_an_object_with_fields`).
5. A publish that raises, or a missing `TAGS_TOPIC_ARN`: the file stays `DONE` and the failure is logged (Task 5 `test_publish_failure_is_swallowed`, `test_missing_topic_arn_is_logged_not_raised`).

---

## File Structure

| File | Responsibility |
|---|---|
| `services/api/tests/test_sns_behaviour.py` (create) | Integration experiments that settle the SNS questions in spec section 5 |
| `services/api/notify_policy.py` (create) | Pure: build, parse, merge and trim a filter policy |
| `services/api/species.txt` (create) | The species names that may be subscribed to, one per line |
| `services/api/subscribe.py` (create) | The `POST /subscribe` handler |
| `services/process/notify.py` (create) | `publish_tags`: build the message and publish it, swallowing and logging failure |
| `services/process/app.py` (modify) | Call `publish_tags` after `record_success` |
| `infra/terraform/modules/notify/` (create) | The topic |
| `infra/terraform/modules/compute/`, `api_functions/`, `api/`, `envs/dev/` (modify) | Wiring, IAM, route, throttle |
| `docs/adr/0011-*.md`, `docs/adr/README.md`, `docs/runbook.md`, `CLAUDE.md`, the spec (modify/create) | Documentation |

Why `species.txt` is a copy and not `ml/labels.txt`: the API artefact is zipped from `services/api/` alone, so `ml/` is not in it. A test keeps the copy equal to the common names in `ml/labels.txt`.

---

### Task 1: Settle the SNS questions against real AWS

**Files:**
- Create: `services/api/tests/test_sns_behaviour.py`
- Modify: `docs/superpowers/specs/2026-10-26-sns-notifications-design.md` (section 5, record the results)

**Interfaces:**
- Produces: confirmed answers to spec section 5, questions 2 to 4. Question 1 stops mattering because `build_policy` writes a single species as a plain policy and only uses `$or` for two or more.

These tests create a throwaway topic, an SQS queue subscribed with a body filter (SQS needs no confirmation click), and optionally an email subscription. They are marked `integration` and skip unless `AWS_PROFILE` is set. The email tests also need `WILDLENS_TEST_EMAIL`, an address you own: SNS sends it a confirmation mail, which you ignore.

- [ ] **Step 1: Write the tests**

```python
"""What SNS actually does, checked once against the real service.

Four behaviours of SNS were remembered rather than verified when the Phase 8 design
was written (docs/superpowers/specs/2026-10-26-sns-notifications-design.md,
section 5). The subscribe function is built on them, so they are pinned here. A
failure means the design needs revising, not that the test is wrong.

Everything is created under a throwaway topic and deleted afterwards.
"""

import json
import os
import time
import uuid

import boto3
import pytest
from botocore.exceptions import ClientError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("AWS_PROFILE"), reason="needs real AWS"),
]

ONE_SPECIES = {"tags": {"dingo": [{"numeric": [">=", 3]}]}}
TWO_SPECIES = {
    "$or": [
        {"tags": {"dingo": [{"numeric": [">=", 3]}]}},
        {"tags": {"koala": [{"numeric": [">=", 1]}]}},
    ]
}


@pytest.fixture(scope="module")
def sns():
    return boto3.client("sns", region_name="ap-southeast-2")


@pytest.fixture(scope="module")
def sqs():
    return boto3.client("sqs", region_name="ap-southeast-2")


@pytest.fixture()
def topic(sns):
    arn = sns.create_topic(Name=f"wildlens-test-{uuid.uuid4().hex[:8]}")["TopicArn"]
    yield arn
    sns.delete_topic(TopicArn=arn)


@pytest.fixture()
def queue(sqs, topic):
    url = sqs.create_queue(QueueName=f"wildlens-test-{uuid.uuid4().hex[:8]}")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "sns.amazonaws.com"},
                "Action": "sqs:SendMessage",
                "Resource": arn,
                "Condition": {"ArnEquals": {"aws:SourceArn": topic}},
            }
        ],
    }
    sqs.set_queue_attributes(QueueUrl=url, Attributes={"Policy": json.dumps(policy)})
    yield url, arn
    sqs.delete_queue(QueueUrl=url)


def drain(sqs, url, wait=6):
    received = []
    deadline = time.time() + wait
    while time.time() < deadline:
        batch = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=2, MaxNumberOfMessages=10)
        received.extend(batch.get("Messages", []))
    return received


def test_numeric_filter_matches_a_number_nested_in_the_message_body(sns, sqs, topic, queue):
    """Question 3: does >= apply to tags.dingo inside a JSON body?"""
    url, arn = queue
    sns.subscribe(
        TopicArn=topic,
        Protocol="sqs",
        Endpoint=arn,
        Attributes={
            "FilterPolicy": json.dumps(TWO_SPECIES),
            "FilterPolicyScope": "MessageBody",
            "RawMessageDelivery": "true",
        },
    )
    time.sleep(2)
    sns.publish(TopicArn=topic, Message=json.dumps({"fileId": "a", "tags": {"dingo": 3}}))
    sns.publish(TopicArn=topic, Message=json.dumps({"fileId": "b", "tags": {"dingo": 2}}))
    sns.publish(TopicArn=topic, Message=json.dumps({"fileId": "c", "tags": {"koala": 1}}))
    sns.publish(TopicArn=topic, Message=json.dumps({"fileId": "d", "tags": {"emu": 9}}))
    got = {json.loads(m["Body"])["fileId"] for m in drain(sqs, url)}
    assert got == {"a", "c"}


def test_a_single_clause_policy_is_accepted_without_or(sns, topic, queue):
    """The shape build_policy writes for one species."""
    _url, arn = queue
    sub = sns.subscribe(
        TopicArn=topic,
        Protocol="sqs",
        Endpoint=arn,
        Attributes={"FilterPolicy": json.dumps(ONE_SPECIES), "FilterPolicyScope": "MessageBody"},
        ReturnSubscriptionArn=True,
    )["SubscriptionArn"]
    attrs = sns.get_subscription_attributes(SubscriptionArn=sub)["Attributes"]
    assert json.loads(attrs["FilterPolicy"]) == ONE_SPECIES


def test_filter_policy_can_be_replaced_on_a_confirmed_subscription(sns, topic, queue):
    """The merge path: SetSubscriptionAttributes with a longer policy."""
    _url, arn = queue
    sub = sns.subscribe(
        TopicArn=topic,
        Protocol="sqs",
        Endpoint=arn,
        Attributes={"FilterPolicy": json.dumps(ONE_SPECIES), "FilterPolicyScope": "MessageBody"},
        ReturnSubscriptionArn=True,
    )["SubscriptionArn"]
    sns.set_subscription_attributes(
        SubscriptionArn=sub, AttributeName="FilterPolicy", AttributeValue=json.dumps(TWO_SPECIES)
    )
    attrs = sns.get_subscription_attributes(SubscriptionArn=sub)["Attributes"]
    assert json.loads(attrs["FilterPolicy"]) == TWO_SPECIES


@pytest.mark.skipif(not os.environ.get("WILDLENS_TEST_EMAIL"), reason="needs WILDLENS_TEST_EMAIL")
def test_pending_email_subscription_is_listed_as_pending_and_resubscribes(sns, topic):
    """Questions 2 and 4: how a pending address looks, and what Subscribe does again."""
    email = os.environ["WILDLENS_TEST_EMAIL"]
    first = sns.subscribe(
        TopicArn=topic,
        Protocol="email",
        Endpoint=email,
        Attributes={"FilterPolicy": json.dumps(ONE_SPECIES), "FilterPolicyScope": "MessageBody"},
        ReturnSubscriptionArn=True,
    )["SubscriptionArn"]
    assert first == "pending confirmation"

    listed = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
    assert [s["SubscriptionArn"] for s in listed] == ["PendingConfirmation"]

    # Subscribing again with no attributes must neither raise nor create a second row.
    again = sns.subscribe(
        TopicArn=topic, Protocol="email", Endpoint=email, ReturnSubscriptionArn=True
    )["SubscriptionArn"]
    assert again == "pending confirmation"
    assert len(sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]) == 1

    # Changing attributes of a pending subscription is not possible.
    with pytest.raises(ClientError):
        sns.set_subscription_attributes(
            SubscriptionArn="PendingConfirmation",
            AttributeName="FilterPolicy",
            AttributeValue=json.dumps(TWO_SPECIES),
        )
```

- [ ] **Step 2: Run them against AWS**

Run: `export AWS_PROFILE=wildlens WILDLENS_TEST_EMAIL=<an address you own> && pytest services/api/tests/test_sns_behaviour.py -m integration -v`
Expected: all four PASS. The email test makes SNS send one confirmation mail to that address; ignore it.

If a test fails, **stop**: the assumption in spec 4.3 is wrong. Record what happened in spec section 5 and ask the owner before continuing, because the flow table in section 4.3 will change.

- [ ] **Step 3: Record the results in the spec**

Replace the "Open questions to settle by experiment" list in section 5 with the observed answers (one line each, with the date), keeping the heading, and note that a single-species policy is written without `$or`.

- [ ] **Step 4: Run lint and the unit suite**

Run: `make lint && make test`
Expected: PASS (integration tests are skipped in `make test`).

- [ ] **Step 5: Commit**

```bash
git add services/api/tests/test_sns_behaviour.py docs/superpowers/specs/2026-10-26-sns-notifications-design.md
git commit -m "test(notify): pin the SNS behaviours the subscribe design relies on

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Filter policy functions

**Files:**
- Create: `services/api/notify_policy.py`
- Create: `services/api/tests/test_notify_policy.py`
- Modify: `pyproject.toml` (`known-first-party`: add `notify_policy`, `subscribe`, `notify`)

**Interfaces:**
- Produces:
  - `build_policy(wanted: dict[str, int]) -> dict` where `wanted` is non-empty; one species gives a plain policy, two or more give `{"$or": [...]}`
  - `parse_policy(policy: dict) -> dict[str, int]`
  - `with_species(wanted: dict[str, int], tag: str, minimum: int) -> dict[str, int]`: a copy, with the lower threshold kept when the species is already present
  - `without_species(wanted: dict[str, int], tag: str) -> dict[str, int]`: a copy

- [ ] **Step 1: Write the failing tests**

```python
"""The filter policy is the whole subscription state, so these round-trips matter."""

import pytest

from notify_policy import build_policy, parse_policy, with_species, without_species


def test_one_species_is_a_plain_policy():
    assert build_policy({"dingo": 3}) == {"tags": {"dingo": [{"numeric": [">=", 3]}]}}


def test_two_species_are_ored():
    assert build_policy({"dingo": 3, "koala": 1}) == {
        "$or": [
            {"tags": {"dingo": [{"numeric": [">=", 3]}]}},
            {"tags": {"koala": [{"numeric": [">=", 1]}]}},
        ]
    }


@pytest.mark.parametrize("wanted", [{"dingo": 3}, {"dingo": 3, "koala": 1, "wild boar": 7}])
def test_parse_inverts_build(wanted):
    assert parse_policy(build_policy(wanted)) == wanted


def test_building_an_empty_policy_is_an_error():
    with pytest.raises(ValueError):
        build_policy({})


def test_with_species_adds_and_keeps_the_lower_threshold():
    assert with_species({"dingo": 3}, "koala", 2) == {"dingo": 3, "koala": 2}
    assert with_species({"dingo": 3}, "dingo", 5) == {"dingo": 3}
    assert with_species({"dingo": 3}, "dingo", 1) == {"dingo": 1}


def test_helpers_do_not_mutate_their_input():
    original = {"dingo": 3}
    with_species(original, "koala", 1)
    without_species(original, "dingo")
    assert original == {"dingo": 3}


def test_without_species_removes_only_that_one():
    assert without_species({"dingo": 3, "koala": 1}, "dingo") == {"koala": 1}
    assert without_species({"dingo": 3}, "emu") == {"dingo": 3}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest services/api/tests/test_notify_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notify_policy'`.

- [ ] **Step 3: Implement**

```python
"""Build and read the SNS filter policy that stores a subscription.

There is no table behind /subscribe. The policy on the SNS subscription is the
state, so reading and writing it has to be lossless, which is why this is a pure
module with round-trip tests rather than a few lines inside the handler.

Two SNS rules shape it. Keys inside one policy are ANDed, so "dingo >= 3 OR
koala >= 1" needs a top-level `$or`. And the policy applies to the message body
(FilterPolicyScope=MessageBody) because a file can carry more species than the 10
message attributes SNS allows.
"""

from __future__ import annotations


def _clause(tag: str, minimum: int) -> dict:
    return {"tags": {tag: [{"numeric": [">=", minimum]}]}}


def build_policy(wanted: dict[str, int]) -> dict:
    """One clause is written plain; two or more are ORed."""
    if not wanted:
        raise ValueError("a policy needs at least one species")
    clauses = [_clause(tag, minimum) for tag, minimum in sorted(wanted.items())]
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


def parse_policy(policy: dict) -> dict[str, int]:
    """The inverse of build_policy."""
    clauses = policy.get("$or", [policy])
    wanted: dict[str, int] = {}
    for clause in clauses:
        for tag, rules in clause["tags"].items():
            wanted[tag] = int(rules[0]["numeric"][1])
    return wanted


def with_species(wanted: dict[str, int], tag: str, minimum: int) -> dict[str, int]:
    """A copy with the species added. If it is already there the lower threshold wins,
    because asking again for a species should never make someone miss a sighting."""
    merged = dict(wanted)
    merged[tag] = min(minimum, merged[tag]) if tag in merged else minimum
    return merged


def without_species(wanted: dict[str, int], tag: str) -> dict[str, int]:
    return {name: minimum for name, minimum in wanted.items() if name != tag}
```

Also in `pyproject.toml` add `"notify"`, `"notify_policy"` and `"subscribe"` to `known-first-party`, keeping the list sorted.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest services/api/tests/test_notify_policy.py -v && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api/notify_policy.py services/api/tests/test_notify_policy.py pyproject.toml
git commit -m "feat(notify): build and read the filter policy that stores a subscription

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The species list

**Files:**
- Create: `services/api/species.txt`
- Create: `services/api/tests/test_species.py`

**Interfaces:**
- Produces: `services/api/species.txt`, one lowercase name per line, sorted, no blanks. Task 4 reads it with `Path(__file__).with_name("species.txt")`.

- [ ] **Step 1: Write the failing test**

```python
"""species.txt is a copy, so a test keeps it from drifting.

The API artefact is zipped from services/api alone and cannot see ml/labels.txt.
The names must equal what the tagger writes into a record, which is the last
column of labels.txt, lowercased (ml/tagger.py, load_label_map).
"""

from pathlib import Path

HERE = Path(__file__).resolve()
SPECIES = HERE.parents[1] / "species.txt"
LABELS = HERE.parents[3] / "ml" / "labels.txt"


def common_names_in_labels() -> set[str]:
    names = set()
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(";")
        if len(parts) >= 7 and parts[6].strip():
            names.add(parts[6].strip().lower())
    return names


def test_species_txt_matches_labels_txt():
    listed = [line for line in SPECIES.read_text(encoding="utf-8").splitlines()]
    assert listed == sorted(set(listed)), "one name per line, sorted, no duplicates or blanks"
    assert set(listed) == common_names_in_labels()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest services/api/tests/test_species.py -v`
Expected: FAIL with `FileNotFoundError` for `species.txt`.

- [ ] **Step 3: Generate the file**

```bash
awk -F';' 'NF>=7 && $7!="" {print tolower($7)}' ml/labels.txt | sed 's/^ *//;s/ *$//' | sort -u > services/api/species.txt
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest services/api/tests/test_species.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api/species.txt services/api/tests/test_species.py
git commit -m "feat(notify): list the species that can be subscribed to

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The `subscribe` handler

**Files:**
- Create: `services/api/subscribe.py`
- Create: `services/api/tests/test_subscribe.py`
- Modify: `services/api/tests/conftest.py` (add `TAGS_TOPIC_ARN`)

**Interfaces:**
- Consumes: `notify_policy.build_policy/parse_policy/with_species/without_species`; `common.ClientError/handle_errors/json_body/correlation_id/log_event/response`; `species.txt`.
- Produces: `subscribe.handler(event, context)`; module attributes `sns` (boto3 client, replaced in tests), `TOPIC_ARN`, `MAX_SPECIES = 20`, `MAX_SUBSCRIPTIONS = 200`, `SPECIES: frozenset[str]`.

Behaviour (from spec 4.1, 4.3 to 4.5). Request `{email, tag, min, operation}`. `add` returns `{"ok": true, "status": "pending"|"confirmed", "tag", "min"}`. `remove` returns `{"ok": true, "status": "confirmed"|"none", "tag"}`: `none` when nothing was subscribed, and there is no `min`.

- [ ] **Step 1: Add the environment variable to the test conftest**

In `services/api/tests/conftest.py`, after the `PROCESS_FUNCTION` line add:

```python
os.environ.setdefault("TAGS_TOPIC_ARN", "arn:aws:sns:ap-southeast-2:123456789012:test-tags")
```

- [ ] **Step 2: Write the failing tests**

```python
"""Tests for POST /subscribe.

SNS is a service boundary, so it is replaced with a fake that behaves like the
parts of SNS this function touches: a list of subscriptions, attributes per
subscription, and the PendingConfirmation marker.
"""

import json

import pytest
from botocore.exceptions import ClientError

import notify_policy
import subscribe

PENDING = "PendingConfirmation"
TOPIC = subscribe.TOPIC_ARN


class FakePaginator:
    def __init__(self, sns):
        self.sns = sns

    def paginate(self, TopicArn):  # noqa: N803 - boto3's parameter name
        subs = self.sns.subscriptions
        # Two pages, so a lookup that reads only the first one is caught.
        yield {"Subscriptions": subs[: len(subs) // 2]}
        yield {"Subscriptions": subs[len(subs) // 2 :]}


class FakeSns:
    def __init__(self):
        self.subscriptions: list[dict] = []
        self.attributes: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fail = False

    def seed(self, email, wanted, confirmed=True):
        arn = f"arn:sub:{len(self.subscriptions)}" if confirmed else PENDING
        self.subscriptions.append(
            {"SubscriptionArn": arn, "Endpoint": email, "Protocol": "email", "TopicArn": TOPIC}
        )
        if confirmed:
            self.attributes[arn] = {
                "FilterPolicy": json.dumps(notify_policy.build_policy(wanted))
            }
        return arn

    def seed_many(self, count):
        for index in range(count):
            self.seed(f"user{index}@example.com", {"dingo": 1})

    def get_paginator(self, name):
        assert name == "list_subscriptions_by_topic"
        return FakePaginator(self)

    def _maybe_fail(self):
        if self.fail:
            raise ClientError({"Error": {"Code": "InternalError", "Message": "boom"}}, "Sns")

    def subscribe(self, **kwargs):
        self._maybe_fail()
        self.calls.append(("subscribe", kwargs))
        existing = [s for s in self.subscriptions if s["Endpoint"] == kwargs["Endpoint"]]
        if existing and existing[0]["SubscriptionArn"] == PENDING:
            return {"SubscriptionArn": "pending confirmation"}
        self.subscriptions.append(
            {
                "SubscriptionArn": PENDING,
                "Endpoint": kwargs["Endpoint"],
                "Protocol": kwargs["Protocol"],
                "TopicArn": TOPIC,
            }
        )
        return {"SubscriptionArn": "pending confirmation"}

    def get_subscription_attributes(self, SubscriptionArn):  # noqa: N803
        self._maybe_fail()
        return {"Attributes": self.attributes[SubscriptionArn]}

    def set_subscription_attributes(self, SubscriptionArn, AttributeName, AttributeValue):  # noqa: N803
        self._maybe_fail()
        self.calls.append(("set", SubscriptionArn, AttributeName, AttributeValue))
        self.attributes[SubscriptionArn][AttributeName] = AttributeValue

    def unsubscribe(self, SubscriptionArn):  # noqa: N803
        self._maybe_fail()
        self.calls.append(("unsubscribe", SubscriptionArn))
        self.subscriptions = [s for s in self.subscriptions if s["SubscriptionArn"] != SubscriptionArn]


@pytest.fixture
def sns(monkeypatch):
    fake = FakeSns()
    monkeypatch.setattr(subscribe, "sns", fake)
    return fake


def call(body, raw=None):
    event = {
        "requestContext": {"requestId": "t-1", "authorizer": {"claims": {"email": "me@x.org"}}},
        "body": raw if raw is not None else json.dumps(body),
    }
    result = subscribe.handler(event, None)
    return result["statusCode"], json.loads(result["body"])


def add(email="a@example.com", tag="dingo", minimum=3):
    return call({"email": email, "tag": tag, "min": minimum, "operation": "add"})


def remove(email="a@example.com", tag="dingo"):
    return call({"email": email, "tag": tag, "operation": "remove"})


def policy_of(sns, arn):
    return notify_policy.parse_policy(json.loads(sns.attributes[arn]["FilterPolicy"]))


# --- add ---------------------------------------------------------------------


def test_first_add_subscribes_with_a_body_scoped_policy(sns):
    status, body = add()
    assert (status, body) == (200, {"ok": True, "status": "pending", "tag": "dingo", "min": 3})
    (name, kwargs) = sns.calls[0]
    assert name == "subscribe"
    assert kwargs["TopicArn"] == TOPIC
    assert kwargs["Protocol"] == "email"
    assert kwargs["Endpoint"] == "a@example.com"
    assert kwargs["Attributes"]["FilterPolicyScope"] == "MessageBody"
    assert json.loads(kwargs["Attributes"]["FilterPolicy"]) == notify_policy.build_policy(
        {"dingo": 3}
    )


def test_add_to_a_confirmed_address_merges_and_keeps_the_lower_threshold(sns):
    arn = sns.seed("a@example.com", {"dingo": 3})
    status, body = add(tag="red fox", minimum=1)
    assert (status, body["status"]) == (200, "confirmed")
    assert policy_of(sns, arn) == {"dingo": 3, "red fox": 1}
    add(tag="dingo", minimum=1)
    assert policy_of(sns, arn) == {"dingo": 1, "red fox": 1}


def test_pending_add_is_409(sns):
    sns.seed("a@example.com", {}, confirmed=False)
    status, body = add()
    assert status == 409
    assert body["error"].startswith("pending_confirmation")
    # The confirmation mail is sent again.
    assert [name for name, *_ in sns.calls] == ["subscribe"]
    assert "Attributes" not in sns.calls[0][1]


def test_email_match_ignores_case(sns):
    arn = sns.seed("Dingo@Example.com", {"red fox": 1})
    status, _ = add(email="dingo@example.com")
    assert status == 200
    assert policy_of(sns, arn) == {"dingo": 3, "red fox": 1}
    assert not [c for c in sns.calls if c[0] == "subscribe"]


def test_the_lookup_reads_every_page(sns):
    sns.seed_many(3)
    arn = sns.seed("a@example.com", {"red fox": 1})  # lands on the second page
    add()
    assert policy_of(sns, arn) == {"dingo": 3, "red fox": 1}


def test_twenty_species_is_the_ceiling(sns):
    species = sorted(subscribe.SPECIES)[:20]
    arn = sns.seed("a@example.com", {name: 1 for name in species})
    status, body = add(tag=sorted(subscribe.SPECIES)[20])
    assert (status, body["error"].startswith("limit_reached")) == (409, True)
    # Raising a threshold on a species already followed is still allowed.
    status, _ = add(tag=species[0], minimum=5)
    assert status == 200
    assert policy_of(sns, arn)[species[0]] == 1


def test_the_topic_ceiling_blocks_a_new_address_only(sns):
    sns.seed_many(subscribe.MAX_SUBSCRIPTIONS)
    status, body = add(email="new@example.com")
    assert (status, body["error"].startswith("limit_reached")) == (409, True)
    sns.seed("old@example.com", {"red fox": 1})
    assert add(email="old@example.com")[0] == 200


# --- remove ------------------------------------------------------------------


def test_remove_one_of_two_species_keeps_the_subscription(sns):
    arn = sns.seed("a@example.com", {"dingo": 3, "red fox": 1})
    status, body = remove()
    assert (status, body) == (200, {"ok": True, "status": "confirmed", "tag": "dingo"})
    assert policy_of(sns, arn) == {"red fox": 1}


def test_remove_the_last_species_unsubscribes(sns):
    arn = sns.seed("a@example.com", {"dingo": 3})
    assert remove()[0] == 200
    assert ("unsubscribe", arn) in sns.calls
    assert sns.subscriptions == []


def test_remove_from_an_unknown_address_is_ok_and_changes_nothing(sns):
    status, body = remove()
    assert (status, body) == (200, {"ok": True, "status": "none", "tag": "dingo"})
    assert sns.calls == []


def test_remove_of_a_species_not_followed_changes_nothing(sns):
    sns.seed("a@example.com", {"red fox": 1})
    status, body = remove()
    assert (status, body["status"]) == (200, "confirmed")
    assert [c for c in sns.calls if c[0] in ("set", "unsubscribe")] == []


def test_pending_remove_is_409(sns):
    sns.seed("a@example.com", {}, confirmed=False)
    assert remove()[0] == 409


# --- validation --------------------------------------------------------------


@pytest.mark.parametrize("minimum", [True, False, 0, -1, 2.5, "3", None])
def test_min_must_be_a_positive_integer(sns, minimum):
    assert add(minimum=minimum)[0] == 400
    assert sns.calls == []


def test_min_is_required_for_add_and_ignored_for_remove(sns):
    sns.seed("a@example.com", {"dingo": 3})
    assert call({"email": "a@example.com", "tag": "dingo", "operation": "add"})[0] == 400
    assert remove()[0] == 200


@pytest.mark.parametrize(
    "email", ["", "plain", "a@b", "a b@example.com", "x@" + "y" * 250 + ".com", 7, None]
)
def test_email_must_be_an_address(sns, email):
    assert add(email=email)[0] == 400


def test_unknown_species_is_400(sns):
    assert add(tag="dragon")[0] == 400


def test_operation_must_be_add_or_remove(sns):
    assert call({"email": "a@example.com", "tag": "dingo", "min": 1, "operation": "list"})[0] == 400


@pytest.mark.parametrize("raw", ["[]", '"text"', "{}", '{"email": "a@example.com"}', "not json", ""])
def test_body_must_be_an_object_with_fields(sns, raw):
    assert call(None, raw=raw)[0] == 400
    assert sns.calls == []


# --- failure and privacy -----------------------------------------------------


def test_sns_failure_is_a_502_with_a_generic_message(sns):
    sns.fail = True
    status, body = add()
    assert status == 502
    assert "boom" not in json.dumps(body)


def test_the_response_never_reveals_other_species(sns):
    sns.seed("a@example.com", {"red fox": 1, "cattle": 2})
    _, body = add()
    assert set(body) == {"ok", "status", "tag", "min"}
    assert "red fox" not in json.dumps(body)
```

Species names used above (`dingo`, `red fox`, `cattle`) are in `species.txt`; there are 44 names, so the 21st exists for the ceiling test.

- [ ] **Step 3: Run to verify they fail**

Run: `pytest services/api/tests/test_subscribe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subscribe'`.

- [ ] **Step 4: Implement**

```python
"""POST /subscribe - follow a species, at a minimum count, by email.

There is no table behind this. One SNS subscription per address, and its filter
policy is the state (notify_policy.py). That avoids a second store that would have
to be reconciled with SNS, which does not tell us when an address confirms.

The consequence is the pending case. SNS will not change the attributes of a
subscription that has not been confirmed, so adding or removing a species for such
an address is a 409 that sends the confirmation mail again. The first add carries
its policy in the Subscribe call itself, which is the only moment it can be set.

Any signed-in user may name any address (docs/adr/0011). The limits below and the
shortened response are what keep that from being a probe or a mail cannon: the
response echoes only the species just changed, so the rest of someone else's
subscription is never disclosed.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import boto3
from botocore.exceptions import ClientError as AwsError

import notify_policy
from common import (
    ClientError,
    correlation_id,
    handle_errors,
    json_body,
    log_event,
    logger,
    response,
)

sns = boto3.client("sns")
TOPIC_ARN = os.environ["TAGS_TOPIC_ARN"]

MAX_SPECIES = 20
MAX_SUBSCRIPTIONS = 200

# SNS reports an unconfirmed subscription's ARN as this literal string.
PENDING = "PendingConfirmation"

# Copied from ml/labels.txt because this artefact is zipped from services/api alone.
# test_species.py keeps the copy honest.
SPECIES = frozenset(
    Path(__file__).with_name("species.txt").read_text(encoding="utf-8").splitlines()
)

# Deliberately loose: the real check is SNS's confirmation mail. This only rejects
# what could not be an address, and bounds the length SNS will accept.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validated(event: dict) -> tuple[str, str, str, int | None]:
    body = json_body(event)
    if not isinstance(body, dict):
        raise ClientError(400, "body must be a JSON object")

    email = body.get("email")
    if not isinstance(email, str) or len(email) > 254 or not EMAIL.match(email):
        raise ClientError(400, "email must be a valid address of at most 254 characters")

    tag = body.get("tag")
    if tag not in SPECIES:
        raise ClientError(400, "tag is not a known species")

    operation = body.get("operation")
    if operation not in ("add", "remove"):
        raise ClientError(400, 'operation must be "add" or "remove"')

    minimum = body.get("min")
    if operation == "add":
        # bool is a subclass of int, so True would otherwise pass as a count of 1.
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ClientError(400, "min must be a whole number of at least 1")
    return email, tag, operation, minimum


def _find(email: str) -> tuple[dict | None, int]:
    """The subscription for this address, and how many subscriptions the topic has.

    Case-insensitive, because the same address typed two ways must not become two
    subscriptions. Reads every page: stopping at the first would miss an address
    that happens to sit on the second.
    """
    found = None
    total = 0
    for page in sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=TOPIC_ARN):
        for subscription in page["Subscriptions"]:
            total += 1
            if subscription["Endpoint"].lower() == email.lower():
                found = subscription
    return found, total


def _wanted(arn: str) -> dict[str, int]:
    attributes = sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"]
    return notify_policy.parse_policy(json.loads(attributes["FilterPolicy"]))


def _pending(email: str, cid: str):
    """Send the confirmation mail again and refuse the change.

    No attributes are passed: a pending subscription's cannot be read, and SNS
    rejects a Subscribe whose attributes differ from the stored ones.
    """
    sns.subscribe(TopicArn=TOPIC_ARN, Protocol="email", Endpoint=email)
    log_event("subscription pending, confirmation resent", cid)
    raise ClientError(
        409, "pending_confirmation: confirm through the email SNS sent, then try again"
    )


def _add(email: str, tag: str, minimum: int, cid: str) -> dict:
    subscription, total = _find(email)

    if subscription is None:
        if total >= MAX_SUBSCRIPTIONS:
            raise ClientError(409, "limit_reached: the topic has too many subscriptions")
        sns.subscribe(
            TopicArn=TOPIC_ARN,
            Protocol="email",
            Endpoint=email,
            Attributes={
                "FilterPolicy": json.dumps(notify_policy.build_policy({tag: minimum})),
                "FilterPolicyScope": "MessageBody",
            },
        )
        log_event("subscribed", cid, tag=tag, minimum=minimum)
        return {"ok": True, "status": "pending", "tag": tag, "min": minimum}

    arn = subscription["SubscriptionArn"]
    if arn == PENDING:
        _pending(email, cid)

    wanted = _wanted(arn)
    if tag not in wanted and len(wanted) >= MAX_SPECIES:
        raise ClientError(409, f"limit_reached: at most {MAX_SPECIES} species per address")
    merged = notify_policy.with_species(wanted, tag, minimum)
    sns.set_subscription_attributes(
        SubscriptionArn=arn,
        AttributeName="FilterPolicy",
        AttributeValue=json.dumps(notify_policy.build_policy(merged)),
    )
    log_event("species added", cid, tag=tag, minimum=minimum)
    return {"ok": True, "status": "confirmed", "tag": tag, "min": merged[tag]}


def _remove(email: str, tag: str, cid: str) -> dict:
    subscription, _ = _find(email)
    if subscription is None:
        return {"ok": True, "status": "none", "tag": tag}

    arn = subscription["SubscriptionArn"]
    if arn == PENDING:
        _pending(email, cid)

    wanted = _wanted(arn)
    if tag in wanted:
        remaining = notify_policy.without_species(wanted, tag)
        if remaining:
            sns.set_subscription_attributes(
                SubscriptionArn=arn,
                AttributeName="FilterPolicy",
                AttributeValue=json.dumps(notify_policy.build_policy(remaining)),
            )
        else:
            sns.unsubscribe(SubscriptionArn=arn)
        log_event("species removed", cid, tag=tag)
    return {"ok": True, "status": "confirmed", "tag": tag}


@handle_errors
def handler(event, _context):
    cid = correlation_id(event)
    email, tag, operation, minimum = _validated(event)
    try:
        if operation == "add":
            result = _add(email, tag, minimum, cid)
        else:
            result = _remove(email, tag, cid)
    except AwsError as error:
        # The detail goes to the log with the correlation id; the caller is told
        # nothing about SNS's internals.
        logger.exception("sns call failed, correlationId=%s", cid)
        raise ClientError(502, "the notification service is unavailable") from error
    return response(200, result)
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest services/api/tests -v && make lint`
Expected: PASS. If `test_twenty_species_is_the_ceiling` fails because `species.txt` has fewer than 21 names, count them (`wc -l services/api/species.txt`); there are 44 distinct common names today.

- [ ] **Step 6: Commit**

```bash
git add services/api/subscribe.py services/api/tests/test_subscribe.py services/api/tests/conftest.py
git commit -m "feat(notify): add POST /subscribe, with the filter policy as the only state

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Publish from `process`

**Files:**
- Create: `services/process/notify.py`
- Create: `services/process/tests/test_notify.py`
- Create: `services/process/tests/test_app_publish.py`
- Modify: `services/process/app.py` (client, import, one call after `record_success`)
- Modify: `services/process/tests/conftest.py` (AWS environment defaults)

**Interfaces:**
- Produces: `notify.publish_tags(client, topic_arn: str, file_id: str, tags: dict[str, int], correlation_id: str) -> bool`. Returns `True` when published, `False` when it could not (and logs). Never raises.
- The message is the JSON `{"fileId": ..., "tags": {...}}`, pretty-printed with `indent=2`. The email body is that JSON itself: SNS filters on the body, so the body must be the JSON. A friendlier plain-text email would need `MessageStructure="json"`, whose interaction with body-scope filters is not verified. The subject is `WildLens: new sighting`.

- [ ] **Step 1: Write the failing unit tests**

`services/process/tests/test_notify.py`:

```python
"""publish_tags must never be the reason a tagged file is reported as failed."""

import json
import logging

import notify

ARN = "arn:aws:sns:ap-southeast-2:123456789012:tags"


class FakeSns:
    def __init__(self, error=None):
        self.error = error
        self.published = []

    def publish(self, **kwargs):
        if self.error:
            raise self.error
        self.published.append(kwargs)
        return {"MessageId": "m-1"}


def test_publishes_the_file_id_and_tags_as_json():
    client = FakeSns()
    assert notify.publish_tags(client, ARN, "abc", {"dingo": 3, "koala": 1}, "cid-1") is True
    (call,) = client.published
    assert call["TopicArn"] == ARN
    assert call["Subject"] == "WildLens: new sighting"
    assert json.loads(call["Message"]) == {"fileId": "abc", "tags": {"dingo": 3, "koala": 1}}


def test_publish_failure_is_swallowed(caplog):
    client = FakeSns(error=RuntimeError("sns is down"))
    with caplog.at_level(logging.ERROR):
        assert notify.publish_tags(client, ARN, "abc", {"dingo": 3}, "cid-1") is False
    assert "cid-1" in caplog.text
    assert "abc" in caplog.text


def test_missing_topic_arn_is_logged_not_raised(caplog):
    client = FakeSns()
    with caplog.at_level(logging.ERROR):
        assert notify.publish_tags(client, "", "abc", {"dingo": 3}, "cid-1") is False
    assert client.published == []
    assert "TAGS_TOPIC_ARN" in caplog.text


def test_a_file_with_no_tags_publishes_nothing():
    client = FakeSns()
    assert notify.publish_tags(client, ARN, "abc", {}, "cid-1") is False
    assert client.published == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest services/process/tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notify'`.

- [ ] **Step 3: Implement `notify.py`**

```python
"""Tell subscribers that a file has been tagged.

Kept out of app.py so it can be tested without the models: app.py imports the
detector, which no unit test can load.

A failure here must not fail the file. The tags are already written. Raising would
make SQS redeliver the message, claim() would see DONE and skip it, and the
notification would be lost anyway, while a correctly tagged file was reported as a
failure. So the error is logged with the correlation id and the caller carries on;
the runbook has a Logs Insights query for finding them.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger()

SUBJECT = "WildLens: new sighting"


def publish_tags(
    client, topic_arn: str, file_id: str, tags: dict[str, int], correlation_id: str
) -> bool:
    """Publish one message for a file that reached DONE. Never raises."""
    if not tags:
        # Nothing was detected, so no subscription could match.
        return False
    if not topic_arn:
        logger.error(
            json.dumps(
                {
                    "msg": "notification not sent: TAGS_TOPIC_ARN is not set",
                    "correlationId": correlation_id,
                    "fileId": file_id,
                }
            )
        )
        return False
    try:
        # The body is the JSON itself, which is what the subscribers' filter
        # policies read (FilterPolicyScope=MessageBody). Email shows it as is.
        client.publish(
            TopicArn=topic_arn,
            Subject=SUBJECT,
            Message=json.dumps({"fileId": file_id, "tags": tags}, indent=2),
        )
    except Exception:
        logger.exception(
            json.dumps(
                {
                    "msg": "notification not sent: publish failed",
                    "correlationId": correlation_id,
                    "fileId": file_id,
                }
            )
        )
        return False
    return True
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest services/process/tests/test_notify.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing call-site tests**

`services/process/tests/conftest.py` gets the AWS defaults at the end:

```python
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("THUMB_BUCKET", "test-thumb")
```

(Move the `import os` to the top of the file with the other imports.)

`services/process/tests/test_app_publish.py`:

```python
"""The call site: one publish per file that reaches DONE, and never otherwise.

app.py imports the detector and the tagger, which pull in torch and MegaDetector.
They are replaced with stand-ins before app is imported, because this test is about
what app does around them, not about inference.
"""

import importlib
import sys
import types
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def app(monkeypatch, tmp_path):
    fake_loader = types.ModuleType("model_loader")
    fake_loader.artefact_bucket = lambda: "models"
    fake_loader.artefact_version = lambda: "v-test"
    fake_loader.get_models = lambda: None
    fake_tagger = types.ModuleType("tagger")
    fake_tagger.tag_image = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, "model_loader", fake_loader)
    monkeypatch.setitem(sys.modules, "tagger", fake_tagger)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "white").save(image)

    class FakeS3:
        def download_file(self, bucket, key, destination):
            Path(destination).write_bytes(image.read_bytes())

        def upload_file(self, *args, **kwargs):
            return None

    monkeypatch.setattr(module, "s3", FakeS3())
    monkeypatch.setattr(module, "claim", lambda file_id, cid: True)
    monkeypatch.setattr(module, "record_success", lambda file_id, cid, **fields: None)
    monkeypatch.setattr(module, "load_and_tag", lambda path, cid: ({"dingo": 3}, 1, 2))
    monkeypatch.setenv("TAGS_TOPIC_ARN", "arn:aws:sns:ap-southeast-2:1:tags")

    published = []
    monkeypatch.setattr(
        module, "publish_tags", lambda *args, **kwargs: published.append(args) or True
    )
    module.published = published
    yield module
    sys.modules.pop("app", None)


def test_a_file_that_reaches_done_publishes_once(app):
    result = app.process_object("raw", "wild.png", "cid-1", cold_start=False)
    assert len(app.published) == 1
    _client, topic, file_id, tags, cid = app.published[0]
    assert topic == "arn:aws:sns:ap-southeast-2:1:tags"
    assert file_id == result["fileId"]
    assert tags == {"dingo": 3}
    assert cid == "cid-1"


def test_a_file_already_done_publishes_nothing(app, monkeypatch):
    monkeypatch.setattr(app, "claim", lambda file_id, cid: False)
    assert app.process_object("raw", "wild.png", "cid-1", cold_start=False)["skipped"]
    assert app.published == []


def test_query_mode_publishes_nothing(app):
    import base64

    event = {"query_mode": True, "file_bytes": base64.b64encode(b"x").decode(), "ext": "png"}
    app.handler(event, None)
    assert app.published == []
```

- [ ] **Step 6: Run to verify they fail**

Run: `pytest services/process/tests/test_app_publish.py -v`
Expected: FAIL, `app` has no attribute `publish_tags`.

- [ ] **Step 7: Wire it into `app.py`**

After `table = boto3.resource(...)` add `sns = boto3.client("sns")`. Add `from notify import publish_tags` with the other first-party imports (sorted: after `from model_loader import ...`). Immediately after the `record_success(...)` call and before `return {"fileId": file_id, ...}` add:

```python
        # After the record is written, not before: the tags are the product and the
        # email is a side effect. publish_tags never raises, so a failure here
        # cannot send the message back to SQS, where claim() would skip it as DONE
        # and the notification would be lost regardless.
        publish_tags(sns, os.environ.get("TAGS_TOPIC_ARN", ""), file_id, tags, correlation_id)
```

- [ ] **Step 8: Run to verify they pass**

Run: `pytest services/process -v && make lint && make test`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add services/process pyproject.toml
git commit -m "feat(notify): publish to SNS when a file reaches DONE

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Terraform

**Files:**
- Create: `infra/terraform/modules/notify/{main,variables,outputs}.tf`
- Modify: `infra/terraform/modules/compute/{main,variables}.tf`
- Modify: `infra/terraform/modules/api_functions/{main,variables}.tf`
- Modify: `infra/terraform/modules/api/{main,variables}.tf`
- Modify: `infra/terraform/envs/dev/{main,outputs}.tf`

**Interfaces:**
- Produces: output `tags_topic_arn`; the `subscribe` function; route `POST /subscribe`.

- [ ] **Step 1: The topic module**

`modules/notify/variables.tf`:

```hcl
variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}
```

`modules/notify/main.tf`:

```hcl
# One topic for every tag notification. Subscribers are told apart by the filter
# policy on each subscription, not by separate topics, so adding a species costs
# nothing and the publisher never needs to know who is listening.
resource "aws_sns_topic" "tags" {
  name = "${var.name_prefix}-tags"

  # The AWS-managed key. Encryption at rest costs nothing and satisfies CKV_AWS_26;
  # a customer-managed key adds $1 a month and nothing for a message that holds a
  # file id and species names.
  kms_master_key_id = "alias/aws/sns"
}
```

`modules/notify/outputs.tf`:

```hcl
output "topic_arn" {
  description = "Where process publishes and where subscribe manages subscriptions."
  value       = aws_sns_topic.tags.arn
}
```

- [ ] **Step 2: Let `process` publish**

In `modules/compute/variables.tf` add:

```hcl
variable "tags_topic_arn" {
  description = "Topic process publishes to when a file reaches DONE. Publish only."
  type        = string
}
```

In `modules/compute/main.tf`, add to the `aws_iam_role_policy.process` statement list (read the file first and place it beside the other statements):

```hcl
      {
        # Publish only. This function announces a tagged file and has no business
        # managing who is subscribed, so Subscribe and the attribute calls belong
        # to the subscribe function alone.
        Sid      = "AnnounceTaggedFiles"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [var.tags_topic_arn]
      },
```

and `TAGS_TOPIC_ARN = var.tags_topic_arn` to the function's `environment.variables`.

- [ ] **Step 3: The `subscribe` function**

In `modules/api_functions/variables.tf` add:

```hcl
variable "tags_topic_arn" {
  description = "The topic the subscribe function manages subscriptions on."
  type        = string
}
```

In `modules/api_functions/main.tf` add to `local.functions`:

```hcl
    subscribe = {
      handler     = "subscribe.handler"
      description = "POST /subscribe - follow a species at a minimum count by email."
      timeout     = 10
      statements = [
        {
          # Exactly the five calls the handler makes, on the one topic. No Publish:
          # this function manages who is listening and never speaks to them.
          Sid    = "ManageSubscriptions"
          Effect = "Allow"
          Action = [
            "sns:Subscribe",
            "sns:Unsubscribe",
            "sns:ListSubscriptionsByTopic",
            "sns:GetSubscriptionAttributes",
            "sns:SetSubscriptionAttributes",
          ]
          Resource = [var.tags_topic_arn]
        },
      ]
    }
```

and in the shared environment block, beside `PROCESS_FUNCTION`:

```hcl
      # Read by subscribe.py at import. Set for all functions for the same reason as
      # PROCESS_FUNCTION: one zip means one environment, and an ARN is not a secret.
      TAGS_TOPIC_ARN = var.tags_topic_arn
```

Update the comment above `local.functions` that says "three" if present, and `outputs.tf` description "status, upload, search" to include `subscribe`.

- [ ] **Step 4: The route and its throttle**

In `modules/api/variables.tf` add:

```hcl
variable "route_throttles" {
  description = <<-EOT
    Per-route rate limits, keyed like `routes`. Routes absent here use the account
    default (10,000 requests a second), which is far above what any route here
    needs. /subscribe is limited because it can make SNS send mail to an address
    the caller does not own.
  EOT
  type = map(object({
    rate  = number
    burst = number
  }))
  default = {}
}
```

In `modules/api/main.tf`, after `aws_api_gateway_stage.stage`:

```hcl
resource "aws_api_gateway_method_settings" "throttle" {
  #checkov:skip=CKV_AWS_225:Caching is off for every route (see the stage); these settings exist only to set a rate limit
  for_each = var.route_throttles

  rest_api_id = aws_api_gateway_rest_api.api.id
  stage_name  = aws_api_gateway_stage.stage.stage_name

  # "subscribe/POST": the resource path with slashes kept, then the method.
  method_path = "${var.routes[each.key].resource_key}/${var.routes[each.key].http_method}"

  settings {
    throttling_rate_limit  = each.value.rate
    throttling_burst_limit = each.value.burst
  }
}
```

- [ ] **Step 5: Wire the dev environment**

In `envs/dev/main.tf`: add

```hcl
module "notify" {
  source = "../../modules/notify"

  name_prefix = local.name_prefix
}
```

add `tags_topic_arn = module.notify.topic_arn` to the `compute` and `api_functions` modules, `subscribe = []` to the `api` module's `resources`, and these to `routes` and the `api` module:

```hcl
    subscribe = {
      http_method   = "POST"
      resource_key  = "subscribe"
      invoke_arn    = module.api_functions.invoke_arns["subscribe"]
      function_name = module.api_functions.function_names["subscribe"]
    }
```

```hcl
  # 1 request a second with a burst of 5. /subscribe can make SNS email an address
  # the caller does not own, so it is limited far below the other routes.
  route_throttles = {
    subscribe = { rate = 1, burst = 5 }
  }
```

In `envs/dev/outputs.tf` add:

```hcl
output "tags_topic_arn" {
  value = module.notify.topic_arn
}
```

- [ ] **Step 6: Validate**

Run:

```bash
terraform fmt -recursive infra/terraform
terraform -chdir=infra/terraform/envs/dev init -input=false
terraform -chdir=infra/terraform/envs/dev validate
```

Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Show the plan to the owner**

Run: `export AWS_PROFILE=wildlens && terraform -chdir=infra/terraform/envs/dev plan -input=false -no-color`
Expected: additions for the topic, the `subscribe` function, its role, policy and log group, the resource and method, the OPTIONS method, the integration, the Lambda permission and the method settings; in-place changes for the `process` function (environment), its role policy, the other API functions (shared environment and the new zip hash) and the API deployment. **No destroys.** Show the output to the owner and wait for approval before any apply.

- [ ] **Step 8: Checkov**

Run: `checkov -d infra/terraform --framework terraform --quiet --compact` if checkov is installed locally; otherwise rely on CI. Expected: no new failures. Fix with a `#checkov:skip=<ID>:<reason>` on the resource only for a finding that is genuinely accepted.

- [ ] **Step 9: Commit**

```bash
git add infra
git commit -m "feat(notify): add the tags topic, the subscribe function and its route

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Create: `docs/adr/0011-let-any-address-subscribe-and-keep-state-in-sns.md`
- Modify: `docs/adr/README.md`, `docs/runbook.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-10-26-sns-notifications-design.md`

- [ ] **Step 1: The ADR**

Follow `docs/adr/0010-*.md` exactly: the heading `# 11. ...`, then `## Context`, `## Decision`, `## Consequences`, and the rejected-alternatives table. Content to record, with the measured results from Task 1:

- **Any address may be subscribed** (the owner's decision). The cost: a signed-in user can make SNS send a confirmation mail to a stranger and can add or remove species on another person's address. Mitigations: address validation and length bound, species restricted to `species.txt`, 20 species per address, 200 subscriptions per topic, 1 request a second on the route; a recipient can always unsubscribe with the link in every SNS email. Rejected: the caller's own address from the Cognito claim (proposed first and declined).
- **The response is shortened** to `{ok, status, tag, min}` instead of the design's `{ok, tags}`, because the whole `tags` map would let any user read a stranger's subscriptions.
- **State lives in SNS only** (approach A). Rejected: a DynamoDB table of wishes, which SNS cannot reconcile because it does not report confirmation; and fan-out from `process` through SES, which abandons the SNS design and adds domain verification.
- **Publishing is best-effort.** Raising would be redelivered, then skipped by `claim()`, so the notification would be lost anyway while a tagged file was reported failed.
- **The email body is the JSON message**, because the filter reads the body. A friendlier body needs `MessageStructure=json`, whose interaction with body-scope filters was not verified.
- **Known limits:** a species with no common name in `labels.txt` (the genus-only `rattus` fallback) cannot be subscribed to; an SNS outage can lose a notification; a pending address must confirm before species can change.
- The results of the four experiments from Task 1.

- [ ] **Step 2: ADR index**

Add the row `| [11](0011-let-any-address-subscribe-and-keep-state-in-sns.md) | Let any address subscribe, and keep subscription state in SNS | 8 |` to `docs/adr/README.md`.

- [ ] **Step 3: Runbook**

Add a section `## Notifications are not arriving` before `## Bootstrapping CI`, containing: the Logs Insights query for failed publishes against `/aws/lambda/wildlens-dev-process` (`fields @timestamp, @message | filter @message like /notification not sent/ | sort @timestamp desc`); how to see a stuck subscription (`aws sns list-subscriptions-by-topic --topic-arn "$(terraform -chdir=infra/terraform/envs/dev output -raw tags_topic_arn)"` and look for `PendingConfirmation`); how to remove a subscription by hand (`aws sns unsubscribe --subscription-arn ...`); and the manual end-to-end checklist from spec section 6.

- [ ] **Step 4: `CLAUDE.md`**

Replace "SNS notification is not built yet (Phase 8)." in the Ingest path bullet with: "After a file reaches `DONE`, `process` publishes `{fileId, tags}` to the SNS topic `wildlens-tags` (best effort: a failed publish is logged and never fails the file). Subscriptions are managed by the `subscribe` function; the SNS filter policy on one subscription per address is the only state (ADR 11)." Add `subscribe` to the list of API functions in the API bullet.

- [ ] **Step 5: Correct the spec**

In the spec: section 3.2, replace "The published email is plain text: ..." with "The email body is the JSON message itself, pretty-printed, because subscribers' filters read the body; a friendlier body needs `MessageStructure=json`, which is unverified." Section 4.1: add that `remove` returns `{ok, status, tag}` with `status` of `confirmed` or `none` and no `min`. Section 4.4: change "`tag` must be in `ml/labels.txt`" to "`tag` must be in `services/api/species.txt`, a copy of the common names in `ml/labels.txt`, kept equal by a test, because the API artefact cannot see `ml/`". Set the status line to "Approved; implementation in progress".

- [ ] **Step 6: English-only check and commit**

Run: `make lint && git add docs CLAUDE.md && git commit -m "docs: record ADR 11, add the notifications runbook section, update CLAUDE.md"` (pre-commit enforces English-only).

---

### Task 8: Deploy and verify end to end

This task changes the real estate. Do each step only with the owner's go-ahead.

- [ ] **Step 1: Push the branch and open the PR**

```bash
git push -u origin notify/phase-8
gh pr create --title "feat(notify): tag notifications through SNS (Phase 8)" --body "<summary, link to the spec and ADR 11>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Expected: `ci` and `plan` both pass. `plan` should list the same additions as Task 6 step 7.

- [ ] **Step 2: Apply (owner approves the plan first)**

Run: `export AWS_PROFILE=wildlens && terraform -chdir=infra/terraform/envs/dev apply`
Expected: the plan from Task 6 step 7, no destroys.

- [ ] **Step 3: Update the process image**

The new code is in the container, and Terraform sees no change while the tag stays `latest`. Run `./scripts/build-and-push.sh` (Docker required, 20 to 40 minutes on a first run), then update the function to the new image with `aws lambda update-function-code --function-name "$(terraform -chdir=infra/terraform/envs/dev output -raw process_function_name)" --image-uri "$(terraform -chdir=infra/terraform/envs/dev output -raw ecr_repository_url):latest"`. Confirm with `aws lambda get-function-configuration` that `LastUpdateStatus` is `Successful`.

- [ ] **Step 4: Manual end-to-end checklist**

1. `./scripts/get-token.sh`, then `POST /subscribe` with `{"email":"<yours>","tag":"dingo","min":1,"operation":"add"}`. Expect `status: pending` and a confirmation email.
2. Add a second species before confirming. Expect `409 pending_confirmation` and a second confirmation email.
3. Click the link in the email. Add the second species again. Expect `confirmed`.
4. Upload an image with a dingo through the normal upload path. Expect one email with the JSON body.
5. Upload an image with a species you did not follow. Expect no email.
6. Upload the same dingo image again. Expect no second email.
7. `remove` both species. Expect the subscription to disappear from `list-subscriptions-by-topic`.

- [ ] **Step 5: Record the outcome**

If any step behaved differently from this plan, write what happened in ADR 11 and the runbook before merging. Then ask the owner whether to merge.

---

## Self-review

**Spec coverage:** goal and D1 to D6 (Tasks 4, 5, 6); section 2 components (File Structure); section 3 publishing, including failure behaviour and configuration (Task 5, Task 6 step 2); sections 4.1 to 4.5 (Tasks 2 to 4); 4.6 infrastructure and the throttle (Task 6); section 5 (Task 1); section 6 testing (Tasks 1 to 5, Task 8 manual); section 7 documentation (Task 7). Two deviations are made explicit and the spec is corrected in Task 7 step 5: the email body is JSON, and the species list is a copy in `services/api`.

**Placeholder scan:** none left in code steps. The ADR and runbook content in Task 7 are specified as content to write rather than pasted text, because they follow the existing ADR format.

**Type consistency:** `build_policy`, `parse_policy`, `with_species`, `without_species` (Task 2) match their use in Task 4. `publish_tags(client, topic_arn, file_id, tags, correlation_id)` (Task 5) matches the call in `app.py` and the tuple unpacked in `test_app_publish.py`. `TOPIC_ARN`, `MAX_SPECIES`, `MAX_SUBSCRIPTIONS`, `SPECIES`, `sns` (Task 4) are all referenced by the tests that use them.
