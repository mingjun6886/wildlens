"""Tests for the shared API helpers.

Each of these pins a behaviour whose failure would be quiet rather than loud: a
wrong number type that only breaks when a count exceeds a threshold, a missing
CORS header that only breaks in a browser, an upload attributed to the wrong
person. None of them would be caught by a smoke test that checks for HTTP 200.
"""

import decimal
import json

import pytest

import common

# --- numbers ---------------------------------------------------------------


def test_decimal_counts_become_plain_ints():
    """boto3 returns every number as Decimal, which json.dumps refuses outright."""
    assert common.plain({"wild boar": decimal.Decimal("3")}) == {"wild boar": 3}


def test_a_whole_decimal_is_an_int_not_a_float():
    """A count of 3 must serialise as 3, not 3.0 - the client compares integers."""
    result = common.plain(decimal.Decimal("3"))
    assert result == 3
    assert isinstance(result, int)


def test_a_fractional_decimal_keeps_its_fraction():
    """Truncating silently would be worse than carrying a float."""
    assert common.plain(decimal.Decimal("0.5")) == 0.5


def test_nested_structures_are_converted_throughout():
    """tags sits inside the record, so a shallow conversion would miss it."""
    record = {"tags": {"dingo": decimal.Decimal("2")}, "history": [decimal.Decimal("1")]}
    assert common.plain(record) == {"tags": {"dingo": 2}, "history": [1]}


# --- the verified claim ----------------------------------------------------


def test_caller_email_reads_the_verified_claim():
    event = {"requestContext": {"authorizer": {"claims": {"email": "someone@example.com"}}}}
    assert common.caller_email(event) == "someone@example.com"


def test_caller_email_refuses_when_the_authoriser_did_not_run():
    """A missing claim is a misconfiguration, not a request to attribute to nobody.

    Returning a placeholder here is how a record ends up owned by "unknown", which
    is exactly the defect that moved uploadedBy out of S3 metadata.
    """
    with pytest.raises(common.ClientError) as raised:
        common.caller_email({"requestContext": {}})
    assert raised.value.status == 401


def test_caller_email_ignores_anything_the_client_sent():
    """The body cannot influence attribution, whatever it claims."""
    event = {
        "requestContext": {"authorizer": {"claims": {"email": "real@example.com"}}},
        "body": json.dumps({"uploadedBy": "someone-else@example.com"}),
    }
    assert common.caller_email(event) == "real@example.com"


# --- request parsing -------------------------------------------------------


def test_an_empty_body_is_a_400_not_a_crash():
    with pytest.raises(common.ClientError) as raised:
        common.json_body({"body": ""})
    assert raised.value.status == 400


def test_malformed_json_is_a_400_and_says_so():
    with pytest.raises(common.ClientError) as raised:
        common.json_body({"body": "{not json"})
    assert raised.value.status == 400
    assert "not valid JSON" in raised.value.message


def test_a_bare_string_body_is_rejected():
    """json.loads accepts a quoted string; the handlers expect a structure."""
    with pytest.raises(common.ClientError):
        common.json_body({"body": '"just a string"'})


# --- responses -------------------------------------------------------------


def test_every_response_carries_the_cors_origin():
    """The preflight is only half of CORS; without this header the browser blocks
    the real response while the OPTIONS reply still looks correct."""
    result = common.response(200, {"ok": True})
    assert result["headers"]["Access-Control-Allow-Origin"] == "http://localhost:3000"


def test_the_origin_is_never_a_wildcard():
    """"*" would let any page on the internet call the API with a stolen token."""
    assert common.CORS_HEADERS["Access-Control-Allow-Origin"] != "*"


def test_response_body_is_a_json_string():
    """API Gateway proxy integration requires a string, not an object."""
    result = common.response(200, {"count": 1})
    assert isinstance(result["body"], str)
    assert json.loads(result["body"]) == {"count": 1}


# --- error handling --------------------------------------------------------


def test_handle_errors_maps_a_client_error_to_its_status():
    @common.handle_errors
    def handler(_event, _context):
        raise common.ClientError(404, "nope")

    assert handler({}, None)["statusCode"] == 404


def test_handle_errors_hides_internal_detail_but_keeps_the_trail():
    """A 500 must not describe the internals, yet must remain diagnosable: the
    correlation id in the body is what ties a user's complaint to a log line."""

    @common.handle_errors
    def handler(_event, _context):
        raise RuntimeError("connection string was postgres://user:hunter2@host")

    result = handler({"requestContext": {"requestId": "abc-123"}}, None)
    body = json.loads(result["body"])

    assert result["statusCode"] == 500
    assert "hunter2" not in result["body"]
    assert body["correlationId"] == "abc-123"


def test_an_error_response_still_carries_cors_headers():
    """Without this a browser cannot read the error at all, and a 400 presents as a
    network failure with no message."""

    @common.handle_errors
    def handler(_event, _context):
        raise common.ClientError(400, "bad field")

    assert "Access-Control-Allow-Origin" in handler({}, None)["headers"]


# --- signing ---------------------------------------------------------------


def test_signed_urls_use_sigv4():
    """SigV2 was the boto3 default here and is being retired. It also places
    metadata differently in a presigned PUT, which decides whether the browser's
    upload succeeds - so the scheme is pinned rather than inherited."""
    url = common.signed_url("test-thumb", "abc.jpg")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "AWSAccessKeyId=" not in url


def test_signed_urls_expire():
    url = common.signed_url("test-thumb", "abc.jpg")
    assert f"X-Amz-Expires={common.URL_TTL_SECONDS}" in url


def test_urls_are_omitted_when_there_are_no_keys():
    """A PROCESSING record has no thumbnail yet; signing one would hand the client
    a link to an object that does not exist."""
    assert common.urls_for({"status": "PROCESSING"}) == {}
