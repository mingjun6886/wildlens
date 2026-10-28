"""X-Ray subsegments for the tagger, without making the SDK a requirement.

The SDK is installed in the container image only (ADR 12). Unit tests, the
evaluation script and local runs do not have it, so every use goes through span(),
which does nothing when the SDK is absent.

Named tracing and not trace: a flat module called trace would sit ahead of the
standard library's trace module on sys.path inside Lambda and shadow it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

try:
    from aws_xray_sdk.core import patch, xray_recorder
except ImportError:  # unit tests, ml/eval and local runs
    xray_recorder = None
else:
    # Outside Lambda with active tracing there is no segment to attach to, and the
    # default is to raise on every subsegment. Ignoring the missing context turns
    # that into a no-op, so a local invocation of the handler still works.
    xray_recorder.configure(context_missing="IGNORE_ERROR")
    # botocore, which every boto3 client goes through: this gives the DynamoDB, S3
    # and SNS calls their own subsegments with no code of ours.
    patch(["botocore"])


@contextmanager
def span(name: str) -> Iterator[None]:
    """Open a subsegment called `name` for the body, or do nothing without the SDK."""
    if xray_recorder is None:
        yield
        return
    with xray_recorder.in_subsegment(name):
        yield
