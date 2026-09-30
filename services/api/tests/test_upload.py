"""Tests for POST /upload.

A note on mocking, since Phase 5 removed monkeypatch from the tagger tests and
called it a design smell. The distinction is what is being replaced.

  Replacing a collaborator the code owns   design smell - pass it in instead
  Replacing a client at a service boundary normal - there is nothing to pass in

`upload.table` is a DynamoDB resource. A Lambda handler's signature is fixed by
the platform, so there is no argument to inject it through, and the boundary is
exactly where a fake belongs. The tagger's detector was neither of those: it was a
collaborator the function fetched for itself.
"""

import json

import pytest

import common
import upload


class FakeTable:
    """Stands in for the DynamoDB table, recording what was written."""

    def __init__(self, existing: dict | None = None):
        self.existing = existing
        self.updates: list[dict] = []

    def get_item(self, Key):  # noqa: N803 - boto3's parameter name
        return {"Item": self.existing} if self.existing else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(upload, "table", fake)
    return fake


def request_event(body: dict, email: str = "someone@example.com") -> dict:
    return {
        "requestContext": {"requestId": "test-1", "authorizer": {"claims": {"email": email}}},
        "body": json.dumps(body),
    }


def valid_body(**overrides) -> dict:
    body = {"sha256": "a" * 64, "ext": "jpg", "size": 1024}
    body.update(overrides)
    return body


# --- validation ------------------------------------------------------------


def test_a_valid_request_is_accepted():
    assert upload.validated_request(valid_body()) == ("a" * 64, "jpg", 1024)


def test_the_digest_must_be_64_hex_characters():
    with pytest.raises(common.ClientError) as raised:
        upload.validated_request(valid_body(sha256="abc"))
    assert raised.value.status == 400


def test_a_digest_with_non_hex_characters_is_rejected():
    """Right length, wrong alphabet. Length alone would let "z" * 64 through, and it
    would become an S3 key that no digest can ever match."""
    with pytest.raises(common.ClientError):
        upload.validated_request(valid_body(sha256="z" * 64))


def test_an_uppercase_digest_is_accepted_and_normalised():
    """Different tools print hex in different cases; the same bytes must not get two
    identifiers, or deduplication silently stops working."""
    digest, _, _ = upload.validated_request(valid_body(sha256="A" * 64))
    assert digest == "a" * 64


def test_a_leading_dot_on_the_extension_is_tolerated():
    _, extension, _ = upload.validated_request(valid_body(ext=".JPG"))
    assert extension == "jpg"


def test_video_is_rejected_until_phase_12():
    """Accepting it would create a PENDING record that can only ever fail, and the
    caller would learn that twenty seconds later instead of immediately."""
    with pytest.raises(common.ClientError) as raised:
        upload.validated_request(valid_body(ext="mp4"))
    assert "ext must be one of" in raised.value.message


def test_a_zero_byte_file_is_rejected():
    with pytest.raises(common.ClientError):
        upload.validated_request(valid_body(size=0))


def test_a_file_over_the_limit_is_rejected():
    with pytest.raises(common.ClientError) as raised:
        upload.validated_request(valid_body(size=upload.MAX_BYTES + 1))
    assert "exceeds" in raised.value.message


def test_a_non_numeric_size_is_a_400_not_a_crash():
    with pytest.raises(common.ClientError):
        upload.validated_request(valid_body(size="big"))


# --- deduplication ---------------------------------------------------------


def test_a_finished_file_is_reported_as_a_duplicate(table):
    """The whole point of hashing first: a file already tagged costs one read
    instead of an upload and twenty seconds of inference."""
    table.existing = {"fileId": "a" * 64, "status": "DONE"}

    body = json.loads(upload.handler(request_event(valid_body()), None)["body"])

    assert body["duplicate"] is True
    assert "uploadUrl" not in body
    assert table.updates == [], "a duplicate must not touch the record"


def test_a_file_being_processed_gets_no_new_url(table):
    """Issuing one would let the object be replaced under a running worker, and
    resetting the record to PENDING would discard the claim it holds."""
    table.existing = {"fileId": "a" * 64, "status": "PROCESSING"}

    body = json.loads(upload.handler(request_event(valid_body()), None)["body"])

    assert body["status"] == "PROCESSING"
    assert "uploadUrl" not in body
    assert table.updates == []


def test_a_previously_failed_file_may_be_retried(table):
    """A permanent failure is about those bytes, not about that digest forever. The
    caller may have fixed the file."""
    table.existing = {"fileId": "a" * 64, "status": "FAILED", "errorReason": "not an image"}

    body = json.loads(upload.handler(request_event(valid_body()), None)["body"])

    assert "uploadUrl" in body
    assert len(table.updates) == 1


def test_an_unseen_file_gets_a_url_and_a_reservation(table):
    body = json.loads(upload.handler(request_event(valid_body()), None)["body"])

    assert body["duplicate"] is False
    assert body["key"] == f"{'a' * 64}.jpg"
    assert "uploadUrl" in body
    assert len(table.updates) == 1


# --- attribution -----------------------------------------------------------


def test_the_reservation_records_the_verified_email(table):
    """The one field only this function knows. Reading it from a Cognito claim is
    what stopped records being attributed to "unknown"."""
    upload.handler(request_event(valid_body(), email="real@example.com"), None)

    values = table.updates[0]["ExpressionAttributeValues"]
    assert values[":email"] == "real@example.com"


def test_the_body_cannot_override_the_email(table):
    """Attribution comes from the token, so a field in the body is ignored."""
    event = request_event(
        {**valid_body(), "uploadedBy": "attacker@example.com"}, email="real@example.com"
    )
    upload.handler(event, None)

    assert table.updates[0]["ExpressionAttributeValues"][":email"] == "real@example.com"


def test_a_request_without_a_claim_is_rejected_before_anything_is_written(table):
    """No authoriser means no attribution, and an unattributed reservation is worse
    than a refused request."""
    result = upload.handler({"body": json.dumps(valid_body()), "requestContext": {}}, None)

    assert result["statusCode"] == 401
    assert table.updates == []


def test_the_reservation_refuses_to_overwrite_a_finished_record(table):
    """Belt and braces alongside the status check above: two concurrent requests
    could both read PENDING, and the condition is what settles it in DynamoDB."""
    upload.handler(request_event(valid_body()), None)

    condition = table.updates[0]["ConditionExpression"]
    assert "attribute_not_exists(fileId)" in condition
    assert "<> :done" in condition


def test_the_reservation_expires(table):
    """An abandoned upload must not hold its digest forever."""
    upload.handler(request_event(valid_body()), None)

    assert ":ttl" in table.updates[0]["ExpressionAttributeValues"]


# --- the presigned URL -----------------------------------------------------


def test_the_url_binds_the_content_length(table):
    """Signing the length is what makes the declared size binding. Without it a
    client claiming one megabyte can upload a hundred and the only limit is the
    bill."""
    body = json.loads(upload.handler(request_event(valid_body(size=4096)), None)["body"])

    assert body["requiredHeaders"]["Content-Length"] == "4096"
    assert "content-length" in body["uploadUrl"].lower()


def test_the_url_binds_the_content_type(table):
    body = json.loads(upload.handler(request_event(valid_body(ext="png")), None)["body"])

    assert body["requiredHeaders"]["Content-Type"] == "image/png"


def test_the_url_is_a_put_and_expires(table):
    body = json.loads(upload.handler(request_event(valid_body()), None)["body"])
    url = body["uploadUrl"]

    assert f"X-Amz-Expires={upload.UPLOAD_URL_TTL}" in url
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url


def test_the_key_is_named_after_the_claimed_digest(table):
    """Which is why the tagging function compares its own digest against the key:
    the name is a claim, and an unchecked claim lets one file be stored under
    another file's identifier."""
    body = json.loads(upload.handler(request_event(valid_body(ext="webp")), None)["body"])

    assert body["key"] == f"{'a' * 64}.webp"
