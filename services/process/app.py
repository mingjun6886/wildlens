"""Lambda entry point for the tagging pipeline.

Driven by SQS, which S3 publishes into when an object lands in the raw bucket.
Also accepts two direct invocations: a {bucket, key} payload for testing, and
the query mode the search API uses from Phase 6.

The record written to DynamoDB moves through four states. PENDING is written by
the upload function that arrives in Phase 6; until then a record first appears
here as PROCESSING.

    PENDING ──► PROCESSING ──► DONE
                     │
                     └───────► FAILED
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

from media_utils import make_thumbnail
from model_loader import artefact_bucket, artefact_version, get_models
from tagger import tag_image

# True until the first invocation finishes. A warm invocation reuses the same
# execution environment, and therefore the same module globals, so this flag
# distinguishes the two without asking AWS anything.
_COLD_START = True

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

TMP = Path("/tmp")  # noqa: S108 - the only writable path in a Lambda container

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# A PENDING record that never completes is abandoned work. It expires rather
# than accumulating; the attribute is removed on the transition to DONE.
PENDING_TTL_SECONDS = 3600


class PermanentFailure(Exception):
    """A failure that retrying cannot fix.

    Raising this marks the record FAILED and lets the message be deleted.
    Retrying a corrupt file three times pays three times for the same answer,
    and the third attempt is no more likely to succeed than the first.
    """


def log_event(message: str, correlation_id: str, **fields) -> None:
    """Emit one line of JSON so CloudWatch Logs Insights can query the fields.

    Every line carries the same correlation id, which is what makes it possible
    to follow one file across every function it touches.
    """
    logger.info(
        json.dumps({"msg": message, "correlationId": correlation_id, **fields}, default=str)
    )


def sha256_of(path: Path) -> str:
    """Hash file contents in chunks, so a large video does not have to fit in memory.

    The identity of a file is its content, not its name. The upload function in
    Phase 6 names objects after this same digest, but this function recomputes
    it rather than parsing the key: a name supplied by a client is a claim, and
    the hash is the fact.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def claim(file_id: str, correlation_id: str) -> bool:
    """Mark the record PROCESSING. Returns False if it is already DONE.

    The condition is the idempotency guard. SQS guarantees at-least-once
    delivery, so the same object can arrive twice; without this the second
    delivery would run inference again and, from Phase 8, send a second email.
    """
    try:
        table.update_item(
            Key={"fileId": file_id},
            UpdateExpression="SET #s = :processing, #t = :ttl",
            ConditionExpression="attribute_not_exists(fileId) OR #s <> :done",
            ExpressionAttributeNames={"#s": "status", "#t": "ttl"},
            ExpressionAttributeValues={
                ":processing": "PROCESSING",
                ":done": "DONE",
                ":ttl": Decimal(int(time.time()) + PENDING_TTL_SECONDS),
            },
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        log_event("duplicate delivery ignored", correlation_id, fileId=file_id)
        return False


def record_success(file_id: str, correlation_id: str, **fields) -> None:
    """Write the finished record and remove the expiry.

    REMOVE #t is what turns a provisional row into a permanent one: a record
    that reached DONE is real data and must outlive the hour an abandoned
    upload gets.
    """
    names = {"#s": "status", "#t": "ttl"}
    values = {":done": "DONE"}
    sets = ["#s = :done"]

    for index, (key, value) in enumerate(fields.items()):
        names[f"#f{index}"] = key
        values[f":v{index}"] = value
        sets.append(f"#f{index} = :v{index}")

    table.update_item(
        Key={"fileId": file_id},
        UpdateExpression=f"SET {', '.join(sets)} REMOVE #t",
        ConditionExpression="#s <> :done",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    log_event("completed", correlation_id, fileId=file_id, **fields)


def record_failure(file_id: str, correlation_id: str, reason: str) -> None:
    table.update_item(
        Key={"fileId": file_id},
        UpdateExpression="SET #s = :failed, errorReason = :reason",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":failed": "FAILED", ":reason": reason[:500]},
    )
    log_event("failed", correlation_id, fileId=file_id, errorReason=reason)


def load_and_tag(path: Path, correlation_id: str) -> tuple[dict[str, int], int, int]:
    """Tag a local file. Returns (tags, model_load_ms, inference_ms)."""
    version = artefact_version()

    started = time.time()
    detector, species_model, classes, label_map = get_models(s3, artefact_bucket(), version)
    model_load_ms = int((time.time() - started) * 1000)

    # Readability was established by the caller. Note that tagger.tag_images
    # swallows a failed open and returns no tags rather than raising, so an
    # unreadable file reaching here would be scored as "no animals present"
    # instead of failing - which is precisely why the check happens earlier.
    # Now measures inference alone. Before Phase 5 this number also carried the
    # detector's deserialisation, because the tagger was handed a path and loaded
    # it here, inside the timed section. That is why the old benchmark showed
    # "inference" at 8 s warm when the real figure was closer to 7.
    started = time.time()
    tags = tag_image(str(path), detector, species_model, classes, label_map)
    inference_ms = int((time.time() - started) * 1000)

    return tags, model_load_ms, inference_ms


def process_object(bucket: str, key: str, correlation_id: str, cold_start: bool) -> dict:
    """Download, identify, tag, thumbnail and record one object."""
    started = time.time()
    suffix = Path(key).suffix.lower()

    if suffix not in IMAGE_SUFFIXES:
        # Video arrives in Phase 12. Until then an unknown suffix is a file this
        # system cannot process, and no number of retries will change that.
        raise PermanentFailure(f"unsupported file type: {suffix or '(none)'}")

    local_path = TMP / f"src-{uuid.uuid4().hex}{suffix}"
    head = s3.head_object(Bucket=bucket, Key=key)
    s3.download_file(bucket, key, str(local_path))

    try:
        if local_path.stat().st_size == 0:
            raise PermanentFailure("object is empty")

        # Hashing comes first and works on any bytes, valid image or not. The
        # record must be keyed by something before a failure can be recorded
        # against it, and from Phase 6 the upload function will already have
        # written a PENDING row under this same digest. Verifying first would
        # leave that row stuck at PENDING until its hour expired, so the client
        # would be told "processing" for an hour instead of "failed".
        file_id = sha256_of(local_path)
        log_event(
            "claimed", correlation_id, fileId=file_id, bucket=bucket, key=key, coldStart=cold_start
        )

        if not claim(file_id, correlation_id):
            return {"fileId": file_id, "skipped": "already DONE"}

        try:
            # Verify readability before spending anything on inference. PIL reads
            # only the header, so this costs microseconds and saves both a
            # multi-second inference run and a thumbnail step that would fail
            # anyway. Checking once, here, also means a corrupt file is classified
            # in one place rather than raising from whichever component happens to
            # open it first - which is how it was misclassified as transient and
            # retried three times.
            try:
                with Image.open(local_path) as probe:
                    probe.verify()
            except (UnidentifiedImageError, OSError) as error:
                raise PermanentFailure(f"not a readable image: {error}") from error

            tags, model_load_ms, inference_ms = load_and_tag(local_path, correlation_id)

            thumb_key = f"{file_id}{suffix}"
            thumb_path = TMP / f"thumb-{uuid.uuid4().hex}.jpg"
            make_thumbnail(local_path, thumb_path)
            # ContentType is set explicitly because S3 does not infer it. Without
            # it the object is served as binary/octet-stream, which a browser may
            # still render by sniffing but will offer to download rather than
            # display, and which gives the wrong type to anything that trusts the
            # header. make_thumbnail always writes JPEG, whatever came in.
            s3.upload_file(
                str(thumb_path),
                os.environ["THUMB_BUCKET"],
                thumb_key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            thumb_path.unlink(missing_ok=True)
        except PermanentFailure as failure:
            record_failure(file_id, correlation_id, str(failure))
            raise

        record_success(
            file_id,
            correlation_id,
            type="image",
            s3Key=key,
            thumbKey=thumb_key,
            tags={name: Decimal(count) for name, count in tags.items()},
            # Set by the upload function from the Cognito email claim in Phase 6.
            uploadedBy=head.get("Metadata", {}).get("uploadedby", "unknown"),
            createdAt=Decimal(int(time.time())),
            modelVersion=artefact_version(),
            modelLoadMs=Decimal(model_load_ms),
            processingMs=Decimal(int((time.time() - started) * 1000)),
            coldStart=cold_start,
        )
        return {"fileId": file_id, "tags": tags, "inferenceMs": inference_ms}
    finally:
        local_path.unlink(missing_ok=True)


def handler(event, _context):
    global _COLD_START
    cold_start = _COLD_START
    _COLD_START = False

    # --- query mode: tag bytes and discard them -----------------------------
    #
    # The search API sends a file to be identified, never stored. That is a
    # requirement rather than an optimisation, and it is why this branch exists
    # instead of reusing the normal path with a flag on the storage call.
    if event.get("query_mode"):
        correlation_id = event.get("correlationId") or str(uuid.uuid4())
        query_path = TMP / f"query-{correlation_id}.{event.get('ext', 'jpg')}"
        query_path.write_bytes(base64.b64decode(event["file_bytes"]))
        try:
            tags, model_load_ms, inference_ms = load_and_tag(query_path, correlation_id)
        finally:
            query_path.unlink(missing_ok=True)
        return {
            "tags": tags,
            "coldStart": cold_start,
            "modelVersion": artefact_version(),
            "modelLoadMs": model_load_ms,
            "inferenceMs": inference_ms,
        }

    # --- direct invocation, used for testing --------------------------------
    if "bucket" in event and "key" in event:
        correlation_id = event.get("correlationId") or str(uuid.uuid4())
        return process_object(event["bucket"], event["key"], correlation_id, cold_start)

    # --- queue records ------------------------------------------------------
    #
    # Failures are reported per message rather than by raising. Raising would
    # fail the whole batch, and with ReportBatchItemFailures configured this is
    # what lets a batch larger than one redeliver only what actually failed.
    failures: list[dict[str, str]] = []

    for message in event.get("Records", []):
        correlation_id = message["messageId"]
        body = json.loads(message["body"])

        # An S3 notification can carry several records, and a test event carries
        # none, which is why this is a loop and not an index.
        for s3_record in body.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            # Keys arrive URL-encoded: a space is "+", so an object named
            # "wild boar.jpg" is unreachable without decoding.
            key = unquote_plus(s3_record["s3"]["object"]["key"])

            try:
                process_object(bucket, key, correlation_id, cold_start)
                cold_start = False
            except PermanentFailure as failure:
                # Recorded as FAILED already. Deleting the message stops a file
                # that cannot succeed from consuming two more invocations.
                log_event(
                    "permanent failure, not retrying", correlation_id, key=key, reason=str(failure)
                )
            except Exception:
                logger.exception("transient failure on %s, will retry", key)
                failures.append({"itemIdentifier": message["messageId"]})

    return {"batchItemFailures": failures}
