"""POST /subscribe - follow a species, at a minimum count, by email.

There is no table behind this. One SNS subscription per address, and its filter
policy is the state (notify_policy.py). That avoids a second store that would have
to be reconciled with SNS, which does not tell us when an address confirms.

The consequence is the pending case. SNS will not change the attributes of a
subscription that has not been confirmed, so adding or removing a species for such
an address is a 409 and nothing else. The first add carries its policy in the
Subscribe call itself, which is the only moment it can be set.

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


def _pending(cid: str):
    """Refuse the change. Sends nothing.

    A resend of the confirmation mail was in the first design and was removed after
    review: each one is a mail to an address the caller may not own, the route's
    throttle is shared by every caller rather than applied per caller, so it cannot
    bound how many one person sends to one stranger. Without it a pending address is
    mailed once, by the Subscribe that created it. Re-subscribing with different
    casing could also have created a second, unfiltered subscription.
    """
    log_event("subscription pending, change refused", cid)
    raise ClientError(
        409,
        "pending_confirmation: confirm through the email SNS already sent, then try again",
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
        _pending(cid)

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
    # The caller's own value, not merged[tag]. The stored threshold may be lower, and
    # echoing it would let anyone read it off the response: add min=1000000, see what
    # comes back, then remove to restore the state. See docs/adr/0011.
    return {"ok": True, "status": "confirmed", "tag": tag, "min": minimum}


def _remove(email: str, tag: str, cid: str) -> dict:
    subscription, _ = _find(email)
    if subscription is None:
        return {"ok": True, "status": "none", "tag": tag}

    arn = subscription["SubscriptionArn"]
    if arn == PENDING:
        _pending(cid)

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
