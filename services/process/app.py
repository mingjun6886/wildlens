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
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

from media_utils import make_thumbnail
from metrics import emit
from model_loader import artefact_bucket, artefact_version, get_models
from notify import publish_tags
from tagger import tag_image
from tracing import span
from video import VIDEO_SUFFIXES, UnreadableVideo, busiest_frame, max_n, sample_frames

# True until the first invocation finishes. A warm invocation reuses the same
# execution environment, and therefore the same module globals, so this flag
# distinguishes the two without asking AWS anything.
_COLD_START = True

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
sns = boto3.client("sns")

TMP = Path("/tmp")  # noqa: S108 - the only writable path in a Lambda container

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# A PENDING record that never completes is abandoned work. It expires rather
# than accumulating; the attribute is removed on the transition to DONE.
PENDING_TTL_SECONDS = 3600

# A FAILED record is diagnostic data, not abandoned work, so it outlives a stalled
# upload by a long way — but it still expires, because a permanent failure is not
# worth keeping forever. See record_failure.
FAILED_TTL_SECONDS = 7 * 24 * 3600

# Stop tagging a video when less than this much of the invocation remains. Enough
# to make the thumbnail, write the record and publish, which take about a second;
# the rest is margin. Without the guard a slow video times out, is retried three
# times and lands in the dead-letter queue with nothing recorded.
VIDEO_TIME_MARGIN_MS = 60_000


def _no_deadline() -> int:
    """The clock for a call with no Lambda context: tests and local runs."""
    return 2**31


@dataclass
class Tagged:
    """What tagging one file produced, image or video, before it is recorded."""

    type: str
    tags: dict[str, int]
    model_load_ms: int
    inference_ms: int
    thumb_key: str
    extra: dict = field(default_factory=dict)


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


def _is_digest(value: str) -> bool:
    """Does this string look like a SHA-256 hex digest?

    Both halves matter. Length alone would accept 64 letters, and the character
    check alone would accept a short hex name such as "cafe".
    """
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


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
    """Mark the record FAILED, with an expiry chosen rather than inherited.

    Until Phase 6 this left the ttl that claim() had set, so a failed upload
    vanished after an hour and a client polling afterwards was told "not found"
    instead of "failed". That was an accident of not touching the attribute, not a
    decision.

    Seven days: a failure is diagnostic data with a short useful life. Long enough
    that someone can investigate on Monday, short enough that dead records do not
    accumulate. What matters is that the number is chosen.
    """
    table.update_item(
        Key={"fileId": file_id},
        UpdateExpression="SET #s = :failed, errorReason = :reason, #t = :ttl",
        ExpressionAttributeNames={"#s": "status", "#t": "ttl"},
        ExpressionAttributeValues={
            ":failed": "FAILED",
            ":reason": reason[:500],
            ":ttl": Decimal(int(time.time()) + FAILED_TTL_SECONDS),
        },
    )
    log_event("failed", correlation_id, fileId=file_id, errorReason=reason)


def emit_model_metrics(
    model_load_ms: int, inference_ms: int, cold_start: bool, total_ms: int
) -> None:
    """The metrics that describe the model rather than the file.

    Shared by stored files and query mode, because both run the model. The two cold
    metrics are emitted only on a cold start, so the sample count of
    ModelLoadDuration over Invocations is the cold-start ratio on the dashboard.
    "Cold" is the same flag stored as coldStart in DynamoDB; if the first call on a
    container fails before the model loads, the load on the next call is not counted.
    That undercounts and never overcounts.
    """
    emit("InferenceDuration", inference_ms, "Milliseconds")
    if cold_start:
        emit("ModelLoadDuration", model_load_ms, "Milliseconds")
        emit("ColdStartDuration", total_ms, "Milliseconds")


def load_models() -> tuple[tuple, int]:
    """The cached models and how long getting them took (0-ish when warm)."""
    started = time.time()
    with span("model-load"):
        models = get_models(s3, artefact_bucket(), artefact_version())
    return models, int((time.time() - started) * 1000)


def load_and_tag(path: Path, correlation_id: str) -> tuple[dict[str, int], int, int]:
    """Tag a local file. Returns (tags, model_load_ms, inference_ms)."""
    (detector, species_model, classes, label_map), model_load_ms = load_models()

    # Readability was established by the caller. Note that tagger.tag_images
    # swallows a failed open and returns no tags rather than raising, so an
    # unreadable file reaching here would be scored as "no animals present"
    # instead of failing - which is precisely why the check happens earlier.
    #
    # The timing below measures inference alone. Before Phase 5 it also carried the
    # detector's deserialisation, because the tagger was handed a path and loaded
    # the weights inside this timed section. That is why the old benchmark showed
    # "inference" at 8 s warm when the real figure was closer to 7.
    started = time.time()
    with span("tagging"):
        tags = tag_image(str(path), detector, species_model, classes, label_map)
    inference_ms = int((time.time() - started) * 1000)

    return tags, model_load_ms, inference_ms


def store_thumbnail(source: Path, thumb_key: str) -> None:
    """Make a JPEG thumbnail of `source` and upload it under `thumb_key`."""
    thumb_path = TMP / f"thumb-{uuid.uuid4().hex}.jpg"
    try:
        with span("thumbnail"):
            make_thumbnail(source, thumb_path)
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
    finally:
        thumb_path.unlink(missing_ok=True)


def tag_still(local_path: Path, file_id: str, suffix: str, correlation_id: str) -> Tagged:
    """Verify, tag and thumbnail one image."""
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
    store_thumbnail(local_path, thumb_key)
    return Tagged("image", tags, model_load_ms, inference_ms, thumb_key)


def tag_video(
    local_path: Path, file_id: str, correlation_id: str, remaining_ms: Callable[[], int]
) -> Tagged:
    """Sample, tag frame by frame under the time guard, and combine with MaxN.

    Frames are tagged one at a time with tag_image rather than in one tag_images
    call, because tag_images detects every frame before classifying any, and the
    time guard could not interrupt it.
    """
    frame_dir = TMP / f"frames-{uuid.uuid4().hex}"
    try:
        try:
            sampled = sample_frames(local_path, frame_dir)
        except UnreadableVideo as error:
            raise PermanentFailure(f"not a readable video: {error}") from error
        if sampled.fps_assumed:
            log_event("video frame rate assumed", correlation_id, fileId=file_id)

        (detector, species_model, classes, label_map), model_load_ms = load_models()

        per_frame: list[dict[str, int]] = []
        started = time.time()
        for frame in sampled.frames:
            if remaining_ms() < VIDEO_TIME_MARGIN_MS:
                log_event(
                    "video truncated",
                    correlation_id,
                    fileId=file_id,
                    framesTagged=len(per_frame),
                    framesSampled=len(sampled.frames),
                )
                break
            with span("tagging"):
                per_frame.append(tag_image(str(frame), detector, species_model, classes, label_map))
        tagging_ms = int((time.time() - started) * 1000)

        if not per_frame:
            # A retry would meet the same cold start and the same clock.
            raise PermanentFailure("ran out of time before tagging any frame")

        thumb_key = f"{file_id}.jpg"
        store_thumbnail(sampled.frames[busiest_frame(per_frame)], thumb_key)

        extra = {"frameCount": Decimal(len(per_frame))}
        if sampled.duration_sec is not None:
            extra["durationSec"] = Decimal(str(sampled.duration_sec))

        # The mean per frame, so InferenceDuration keeps meaning "time to tag one
        # image" and the dashboard's percentiles stay comparable with images.
        return Tagged(
            "video", max_n(per_frame), model_load_ms, tagging_ms // len(per_frame), thumb_key, extra
        )
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)


def process_object(
    bucket: str,
    key: str,
    correlation_id: str,
    cold_start: bool,
    remaining_ms: Callable[[], int] = _no_deadline,
) -> dict:
    """Download, identify, tag, thumbnail and record one object."""
    started = time.time()
    suffix = Path(key).suffix.lower()

    if suffix not in IMAGE_SUFFIXES and suffix not in VIDEO_SUFFIXES:
        # A file this system cannot process, and no number of retries will change
        # that.
        raise PermanentFailure(f"unsupported file type: {suffix or '(none)'}")

    local_path = TMP / f"src-{uuid.uuid4().hex}{suffix}"
    # No head_object. It existed only to read the uploadedBy metadata, which the
    # upload function now writes from a verified Cognito claim instead — so this is
    # one fewer S3 call per invocation as well as one fewer thing to trust.
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

        # The key is a claim; the digest is the fact. From Phase 6 the upload
        # function names the object after the digest the client said it was
        # sending, because the browser must know the key before it can upload.
        #
        # This comparison is a security control, not a tidiness check. Without it a
        # client can compute the digest of file B, request an upload, and send the
        # bytes of file A. The record stays keyed by the true digest, so B's record
        # is unharmed — but the object at B's key now holds A's bytes, and B's
        # fullUrl would serve them. Content substitution under another identifier.
        #
        # The failure is recorded against the *claimed* digest, not the computed
        # one, because that is the row the client created and is polling. Recording
        # it under the true digest instead — which the first version of this check
        # did — leaves the caller's own record at PENDING until its hour runs out,
        # so an honest client whose hashing is buggy is told "processing" for an
        # hour rather than "your file did not match". That is the same defect the
        # Phase 4 reordering fixed, arriving by a different route.
        #
        # Enforced only for keys that look like a digest, so a direct upload with a
        # descriptive filename — `aws s3 cp`, and the pre-Phase-6 test path — still
        # works.
        claimed = Path(key).stem.lower()
        if _is_digest(claimed) and claimed != file_id:
            reason = (
                f"content does not match the key it was stored under: "
                f"key claims {claimed[:12]}..., content hashes to {file_id[:12]}..."
            )
            record_failure(claimed, correlation_id, reason)
            raise PermanentFailure(reason)

        if not claim(file_id, correlation_id):
            return {"fileId": file_id, "skipped": "already DONE"}

        try:
            if suffix in VIDEO_SUFFIXES:
                tagged = tag_video(local_path, file_id, correlation_id, remaining_ms)
            else:
                tagged = tag_still(local_path, file_id, suffix, correlation_id)
        except PermanentFailure as failure:
            record_failure(file_id, correlation_id, str(failure))
            raise

        processing_ms = int((time.time() - started) * 1000)
        record_success(
            file_id,
            correlation_id,
            type=tagged.type,
            s3Key=key,
            thumbKey=tagged.thumb_key,
            tags={name: Decimal(count) for name, count in tagged.tags.items()},
            # uploadedBy is deliberately absent. The upload function wrote it onto
            # the PENDING record from a Cognito claim, and this update must not
            # overwrite it — which is exactly what it used to do, replacing a
            # verified address with "unknown" read back from S3 object metadata.
            #
            # An object that arrived without going through the API (aws s3 cp, or a
            # direct invocation) therefore has no uploadedBy at all, which is the
            # honest answer: nobody authenticated for it.
            createdAt=Decimal(int(time.time())),
            modelVersion=artefact_version(),
            modelLoadMs=Decimal(tagged.model_load_ms),
            processingMs=Decimal(processing_ms),
            coldStart=cold_start,
            **tagged.extra,
        )
        # Metrics follow the record and precede the publish, so neither can be lost to
        # the other failing. TagsPerFile is emitted for an empty result too: a file
        # with no animals is still a file that was processed.
        emit_model_metrics(tagged.model_load_ms, tagged.inference_ms, cold_start, processing_ms)
        emit("TagsPerFile", sum(tagged.tags.values()), "Count")
        # After the record is written, not before: the tags are the product and the
        # email is a side effect. publish_tags never raises, so a failure here
        # cannot send the message back to SQS, where claim() would skip it as DONE
        # and the notification would be lost regardless.
        publish_tags(
            sns, os.environ.get("TAGS_TOPIC_ARN", ""), file_id, tagged.tags, correlation_id
        )
        return {
            "fileId": file_id,
            "type": tagged.type,
            "tags": tagged.tags,
            "inferenceMs": tagged.inference_ms,
        }
    finally:
        local_path.unlink(missing_ok=True)


def handler(event, context):
    global _COLD_START
    handler_started = time.time()
    cold_start = _COLD_START
    _COLD_START = False

    # The video path stops tagging before the invocation runs out. A direct call
    # without a context (tests, local runs) has no deadline.
    remaining_ms = getattr(context, "get_remaining_time_in_millis", None) or _no_deadline

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
        emit_model_metrics(
            model_load_ms, inference_ms, cold_start, int((time.time() - handler_started) * 1000)
        )
        # Counted by a Logs Insights widget (ADR 5 wants to know how often this route
        # is hit cold). coldStart is an integer here because Insights can sum one; a
        # JSON boolean is not guaranteed to be summable.
        log_event(
            "query tagged",
            correlation_id,
            coldStart=int(cold_start),
            modelLoadMs=model_load_ms,
            inferenceMs=inference_ms,
        )
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
        return process_object(
            event["bucket"], event["key"], correlation_id, cold_start, remaining_ms=remaining_ms
        )

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
                process_object(bucket, key, correlation_id, cold_start, remaining_ms=remaining_ms)
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
