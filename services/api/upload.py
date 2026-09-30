"""POST /upload — reserve a record and hand back a URL to upload to.

The browser hashes the file before sending anything, so a file already in the
system is never uploaded twice. The bytes then go straight from the browser to S3
through a presigned URL, never through this function: a Lambda that proxied a
40 MB video would pay for the transfer twice and be bounded by API Gateway's 10 MB
payload limit.

    client                    upload                      S3
      │  sha256, ext, size       │                          │
      ├─────────────────────────►│  PENDING + uploadedBy     │
      │◄─────────────────────────┤  presigned PUT            │
      │                          │                          │
      ├──────────── the bytes ───────────────────────────────►│
                                                             │ ObjectCreated
                                                             ▼  queue, then tagging

The digest the client sends is a *claim*. The tagging function recomputes it from
the bytes that actually arrive and refuses to process an object whose content does
not match the key it was stored under — see the note on KEY_IS_A_CLAIM below,
which is a security control rather than tidiness.
"""

from __future__ import annotations

import time
from decimal import Decimal

import boto3

from common import (
    RAW_BUCKET,
    TABLE_NAME,
    ClientError,
    caller_email,
    correlation_id,
    handle_errors,
    json_body,
    log_event,
    response,
    s3,
)

table = boto3.resource("dynamodb").Table(TABLE_NAME)

# Image types the tagging pipeline can open. Video arrives in Phase 12; until then
# accepting an .mp4 would produce a PENDING record that can only ever fail, so the
# rejection belongs here where it can be explained.
ALLOWED_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
}

# Upper bound on one upload. A camera-trap photograph is a few megabytes; 25 is
# generous and still bounds what a single request can cost.
MAX_BYTES = 25 * 1024 * 1024

# How long the presigned URL is valid. Long enough for a slow connection to finish
# a 25 MB file, short enough that a URL copied out of a browser is not a standing
# invitation to write into the bucket.
UPLOAD_URL_TTL = 900

# An abandoned reservation expires rather than blocking the digest forever. The
# same hour the tagging function uses, so the two agree.
PENDING_TTL_SECONDS = 3600

# KEY_IS_A_CLAIM
#
# The object is stored at <claimed sha256>.<ext>, because the browser has to know
# the key before it uploads and the digest is the only name both sides can agree
# on without a round trip.
#
# That makes the key a claim, and it opens a real hole if nothing checks it. A
# client can compute the digest of file B, ask to upload, and send the bytes of
# file A instead. The record is keyed by the digest the tagging function computes,
# so B's *record* is safe — but the *object* at B's key now holds A's bytes, and
# B's fullUrl would serve them. Content substitution under someone else's
# identifier.
#
# The tagging function therefore compares its computed digest against the key and
# treats a mismatch as a permanent failure. This function's part of the contract is
# simply to name the key after the claim, so that the comparison is possible.


def validated_request(body: dict) -> tuple[str, str, int]:
    """Check the three fields and return them, or raise a 400 explaining which.

    Validation is here rather than in an API Gateway request model: REST APIs can
    validate a JSON schema, but the error they return is generic, and a caller
    cannot tell a bad digest from a bad extension. A 400 that names the field is
    worth more than one saved function invocation.
    """
    sha256 = str(body.get("sha256", "")).lower()
    if len(sha256) != 64 or not all(character in "0123456789abcdef" for character in sha256):
        raise ClientError(400, "sha256 must be 64 hexadecimal characters")

    extension = str(body.get("ext", "")).lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ClientError(400, f"ext must be one of: {allowed}")

    try:
        size = int(body["size"])
    except (KeyError, TypeError, ValueError) as error:
        raise ClientError(400, "size must be an integer number of bytes") from error

    if size <= 0:
        raise ClientError(400, "size must be greater than zero")
    if size > MAX_BYTES:
        raise ClientError(400, f"size exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")

    return sha256, extension, size


def upload_url(key: str, extension: str, size: int) -> str:
    """A presigned PUT that binds both the content type and the exact length.

    Signing ContentLength is what makes the declared size binding. A presigned PUT
    cannot otherwise be limited: without it, a client that claims one megabyte can
    upload a hundred, and the only defence left is the bill. The browser sets
    Content-Length itself from the body, so a truthful client needs to do nothing —
    and a client that lied about the size gets 403 rather than a free upload.

    The cost is that both headers must match exactly, which is why the size comes
    from the same pass that produced the digest rather than from a separate stat.
    """
    return s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": RAW_BUCKET,
            "Key": key,
            "ContentType": ALLOWED_EXTENSIONS[extension],
            "ContentLength": size,
        },
        ExpiresIn=UPLOAD_URL_TTL,
    )


def reserve(file_id: str, email: str, correlation: str) -> None:
    """Write the PENDING record, carrying the one field only this function knows.

    uploadedBy comes from the Cognito claim, which is the reason it is written
    here. The original design passed it to S3 as object metadata for the tagging
    function to read back, which made attribution depend on a header a client
    controlled and on which signature scheme was in use. A verified claim is a
    fact; an S3 header is a statement.

    The condition allows overwriting PENDING and FAILED but not DONE, so a retry
    after a failure works while a finished record cannot be reopened.
    """
    table.update_item(
        Key={"fileId": file_id},
        UpdateExpression="SET #s = :pending, uploadedBy = :email, #t = :ttl",
        ConditionExpression="attribute_not_exists(fileId) OR #s <> :done",
        ExpressionAttributeNames={"#s": "status", "#t": "ttl"},
        ExpressionAttributeValues={
            ":pending": "PENDING",
            ":done": "DONE",
            ":email": email,
            ":ttl": Decimal(int(time.time()) + PENDING_TTL_SECONDS),
        },
    )
    log_event("reserved", correlation, fileId=file_id, uploadedBy=email)


@handle_errors
def handler(event, _context):
    cid = correlation_id(event)
    email = caller_email(event)
    sha256, extension, size = validated_request(json_body(event))

    key = f"{sha256}.{extension}"
    existing = table.get_item(Key={"fileId": sha256}).get("Item")
    status = (existing or {}).get("status")

    if status == "DONE":
        # Already tagged. The client is told so instead of being given a URL, which
        # is what makes the hash-first design worth its round trip: a file the
        # system has seen costs one DynamoDB read rather than an upload and twenty
        # seconds of inference.
        log_event("duplicate upload declined", cid, fileId=sha256)
        return response(200, {"duplicate": True, "fileId": sha256})

    if status == "PROCESSING":
        # The bytes are already in the bucket and a worker holds them. Issuing a
        # URL now would let the object be replaced underneath that worker, and
        # resetting the record to PENDING would lose the claim it is working under.
        log_event("upload already in progress", cid, fileId=sha256)
        return response(200, {"duplicate": False, "fileId": sha256, "status": "PROCESSING"})

    reserve(sha256, email, cid)

    return response(
        200,
        {
            "duplicate": False,
            "fileId": sha256,
            "key": key,
            "uploadUrl": upload_url(key, extension, size),
            # The client must send both, or the signature will not match. Returning
            # them removes the guesswork rather than leaving it to documentation.
            "requiredHeaders": {
                "Content-Type": ALLOWED_EXTENSIONS[extension],
                "Content-Length": str(size),
            },
        },
    )
