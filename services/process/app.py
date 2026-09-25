"""Lambda entry point for the tagging pipeline.

Phase 3 scope: fetch an object, tag it, and report timings. Persistence to
DynamoDB, thumbnail upload and SNS notification arrive in Phase 4 once the
queue is in front of this function.

The timings are the point of this phase. Cold-start latency cannot be improved
without first being measured, and "it felt faster" is not a number anyone can
put in a README.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path

import boto3

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

TMP = Path("/tmp")  # noqa: S108 - the only writable path in a Lambda container


def log_event(message: str, correlation_id: str, **fields) -> None:
    """Emit one line of JSON so CloudWatch Logs Insights can query the fields.

    Every line carries the same correlation id, which is what makes it possible
    to follow one file across every function it touches.
    """
    logger.info(json.dumps({"msg": message, "correlationId": correlation_id, **fields}))


def _tag_local_file(path: Path, correlation_id: str) -> tuple[dict[str, int], int, int]:
    """Tag a file already on local disk. Returns (tags, model_load_ms, inference_ms)."""
    version = artefact_version()

    started = time.time()
    detector_path, species_model, classes, label_map = get_models(s3, artefact_bucket(), version)
    model_load_ms = int((time.time() - started) * 1000)

    started = time.time()
    tags = tag_image(str(path), detector_path, species_model, classes, label_map)
    inference_ms = int((time.time() - started) * 1000)

    log_event(
        "tagged",
        correlation_id,
        tags=tags,
        modelVersion=version,
        modelLoadMs=model_load_ms,
        inferenceMs=inference_ms,
    )
    return tags, model_load_ms, inference_ms


def handler(event, _context):
    global _COLD_START
    cold_start = _COLD_START
    _COLD_START = False

    correlation_id = event.get("correlationId") or str(uuid.uuid4())
    started = time.time()

    # Query mode: the search API sends bytes to be tagged and thrown away. The
    # file is never written anywhere durable — that is a requirement, not an
    # optimisation, and it is why this branch exists rather than reusing the
    # normal path with a flag on the storage call.
    if event.get("query_mode"):
        extension = event.get("ext", "jpg")
        query_path = TMP / f"query-{correlation_id}.{extension}"
        query_path.write_bytes(base64.b64decode(event["file_bytes"]))
        try:
            tags, model_load_ms, inference_ms = _tag_local_file(query_path, correlation_id)
        finally:
            query_path.unlink(missing_ok=True)
        return {
            "tags": tags,
            "coldStart": cold_start,
            "modelVersion": artefact_version(),
            "modelLoadMs": model_load_ms,
            "inferenceMs": inference_ms,
            "totalMs": int((time.time() - started) * 1000),
        }

    bucket = event["bucket"]
    key = event["key"]
    log_event("received", correlation_id, bucket=bucket, key=key, coldStart=cold_start)

    local_path = TMP / Path(key).name
    download_started = time.time()
    s3.download_file(bucket, key, str(local_path))
    download_ms = int((time.time() - download_started) * 1000)

    try:
        tags, model_load_ms, inference_ms = _tag_local_file(local_path, correlation_id)

        thumbnail_ms = None
        thumbnail_bucket = os.environ.get("THUMB_BUCKET")
        if thumbnail_bucket:
            thumbnail_path = TMP / f"thumb-{Path(key).name}"
            thumbnail_started = time.time()
            make_thumbnail(local_path, thumbnail_path)
            s3.upload_file(str(thumbnail_path), thumbnail_bucket, Path(key).name)
            thumbnail_ms = int((time.time() - thumbnail_started) * 1000)
            thumbnail_path.unlink(missing_ok=True)
    finally:
        local_path.unlink(missing_ok=True)

    result = {
        "tags": tags,
        "coldStart": cold_start,
        "modelVersion": artefact_version(),
        "downloadMs": download_ms,
        "modelLoadMs": model_load_ms,
        "inferenceMs": inference_ms,
        "thumbnailMs": thumbnail_ms,
        "totalMs": int((time.time() - started) * 1000),
    }
    log_event("completed", correlation_id, **result)
    return result
