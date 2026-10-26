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
def session():
    # Named explicitly because conftest.py plants throwaway credentials in the
    # environment so the unit tests can import boto3 clients. Environment
    # credentials outrank AWS_PROFILE, so without this the first call fails with
    # InvalidClientTokenId instead of reaching AWS as the intended identity.
    return boto3.Session(profile_name=os.environ["AWS_PROFILE"], region_name="ap-southeast-2")


@pytest.fixture(scope="module")
def sns(session):
    return session.client("sns")


@pytest.fixture(scope="module")
def sqs(session):
    return session.client("sqs")


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
