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
    arn = sns.seed("a@example.com", dict.fromkeys(species, 1))
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
