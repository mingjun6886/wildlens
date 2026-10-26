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
