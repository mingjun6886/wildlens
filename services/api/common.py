"""Shared helpers for the three API functions.

They live in one directory, and therefore in one deployment artefact, because
they are the same kind of thing: small, dependency-free handlers that read a
Cognito claim, touch DynamoDB or S3, and return JSON. Sharing the module is what
keeps the response shape and the CORS headers identical across them — the third
copy of a header dictionary is the one that goes stale.

The cost is that changing one function redeploys all three. At a few kilobytes
each that is not a cost, and it means the three are never out of step.
"""

from __future__ import annotations

import decimal
import json
import logging
import os
import uuid

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Both settings are pinned rather than left to boto3's defaults, and both were
# added after reading a URL this code had actually produced.
#
# signature_version: the default here signed with SigV2, recognisable by an
# AWSAccessKeyId and Signature pair in the query string instead of
# X-Amz-Algorithm. It works, because this region predates the SigV4-only cutoff,
# but AWS is retiring it — and the two schemes place object metadata differently
# in a presigned PUT: SigV2 carries it in the query string, SigV4 as a signed
# header the client must then send. Pinning SigV4 removes a difference that would
# otherwise decide whether the browser's PUT succeeds.
#
# s3v4 rather than v4: the s3-specific variant, which also keeps path-style and
# virtual-host addressing consistent.
s3 = boto3.client(
    "s3",
    region_name=os.environ["AWS_REGION"],
    config=Config(signature_version="s3v4"),
)

TABLE_NAME = os.environ["TABLE_NAME"]
RAW_BUCKET = os.environ["RAW_BUCKET"]
THUMB_BUCKET = os.environ["THUMB_BUCKET"]

# How long a signed URL stays valid. Long enough to browse a page of results
# without links dying mid-session, short enough that one copied out of a browser
# is not a lasting grant.
URL_TTL_SECONDS = 3600

# The browser origin allowed to call this API. Phase 7 sets this to the
# CloudFront domain; "*" would also work and is deliberately not used, because it
# would let any page on the internet call the API with a token it had obtained.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:3000")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


class ClientError(Exception):
    """A request the caller can fix. Carries the status code to return."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def log_event(message: str, correlation_id: str, **fields) -> None:
    """One line of JSON, with the same correlation id the tagging Lambda uses.

    The point of the shared field name is that a single Logs Insights query can
    follow one file from the upload request through the queue to the record.
    """
    logger.info(
        json.dumps({"msg": message, "correlationId": correlation_id, **fields}, default=str)
    )


def correlation_id(event: dict) -> str:
    """Prefer API Gateway's request id so a log line ties back to an access log."""
    return event.get("requestContext", {}).get("requestId") or str(uuid.uuid4())


def caller_email(event: dict) -> str:
    """The email from the ID token Cognito signed.

    Not anything the client sent in a body or header. This is the whole reason
    uploadedBy is written here rather than derived from S3 object metadata: a
    verified claim is a fact, and a field a client filled in is a statement.

    cognito:username is a UUID in this pool, not the address, so `email` is the
    claim to read.
    """
    try:
        return event["requestContext"]["authorizer"]["claims"]["email"]
    except (KeyError, TypeError) as error:
        # Reaching here means the authoriser did not run, which is a
        # misconfiguration rather than a bad request. Failing loudly beats
        # attributing an upload to "unknown".
        raise ClientError(401, "no verified email claim on this request") from error


def path_param(event: dict, name: str) -> str:
    value = (event.get("pathParameters") or {}).get(name)
    if not value:
        raise ClientError(400, f"missing path parameter: {name}")
    return value


def json_body(event: dict) -> dict:
    """Parse and validate the body as a JSON object.

    REST API does no request validation here on purpose — see the ADR — so this
    is where a malformed body becomes a 400 rather than a 502 from an unhandled
    exception.
    """
    raw = event.get("body") or ""
    if not raw.strip():
        raise ClientError(400, "request body is required")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ClientError(400, f"body is not valid JSON: {error}") from error
    if not isinstance(parsed, (dict, list)):
        raise ClientError(400, "body must be a JSON object or array")
    return parsed


def plain(value):
    """Convert DynamoDB's Decimal numbers to int or float for JSON.

    boto3 returns every number as Decimal, which json.dumps refuses. Counts are
    whole numbers, so they become int; anything fractional stays a float rather
    than being silently truncated.
    """
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: plain(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [plain(inner) for inner in value]
    return value


def signed_url(bucket: str, key: str) -> str:
    """A time-limited GET for one object.

    Every bucket in this project blocks public access, so this is the only way a
    browser reaches an image. The alternative - making the thumbnail bucket
    public - would put every uploaded photograph on the open internet.
    """
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=URL_TTL_SECONDS,
    )


def urls_for(record: dict) -> dict:
    """Signed thumbnail and original URLs for a finished record, if it has them."""
    out = {}
    if record.get("thumbKey"):
        out["thumbUrl"] = signed_url(THUMB_BUCKET, record["thumbKey"])
    if record.get("s3Key"):
        out["fullUrl"] = signed_url(RAW_BUCKET, record["s3Key"])
    return out


def response(status: int, body: dict | list) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS_HEADERS},
        "body": json.dumps(body, default=str),
    }


def handle_errors(function):
    """Turn a ClientError into its status code and anything else into a 500.

    Without this every handler repeats the same try/except, and the one that
    forgets it returns a 502 with an HTML body when a caller sends a bad field.
    """

    def wrapper(event, context):
        cid = correlation_id(event)
        try:
            return function(event, context)
        except ClientError as error:
            log_event("client error", cid, status=error.status, reason=error.message)
            return response(error.status, {"error": error.message})
        except Exception:
            # The message is deliberately vague: an internal failure must not
            # describe the internals. The detail goes to the log with the same
            # correlation id the caller can quote.
            logger.exception("unhandled error, correlationId=%s", cid)
            return response(500, {"error": "internal error", "correlationId": cid})

    return wrapper
