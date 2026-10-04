"""Three search routes, one function.

    POST /search/tags     {"wombat": 2, "magpie": 1}   AND, with minimum counts
    POST /search/species  ["dingo", "koala"]           OR, any of these
    POST /search/byfile   {"file": "<base64>", "ext"}  files that contain at
                                                       least what this one does

They share a function because they share almost everything: the same scan, the
same matching loop, the same signed-URL assembly. Only the predicate differs, so
each route is a few lines and the dispatch is on the path.

Every query scans the whole table and filters in memory. That is a deliberate
choice at this scale rather than an oversight. The short version: tags are a map,
DynamoDB cannot index into one, and an index per species would mean rewriting the
schema for a query pattern that currently takes 40 ms.
See docs/adr/0004-scan-and-filter-in-memory.md for the threshold at which that
stops being true and what replaces it.
"""

from __future__ import annotations

import base64
import json
import os

import boto3
import botocore.exceptions
from botocore.config import Config

from common import (
    TABLE_NAME,
    ClientError,
    correlation_id,
    handle_errors,
    json_body,
    log_event,
    plain,
    response,
    urls_for,
)

dynamodb = boto3.client("dynamodb")

# The tagging function, invoked synchronously for /search/byfile.
#
# Two settings, and both matter more than they look.
#
# read_timeout is 25 s because API Gateway cuts an integration off at 29 s and
# cannot be raised. Timing out here, inside the function, produces an error this
# code chooses; letting the gateway cut first produces a bare 504 with no body.
#
# max_attempts is 0 because botocore otherwise retries a timed-out invocation —
# and the request it is retrying is a twenty-second ML inference that is still
# running. That pays twice for one answer. Query mode writes nothing, so the
# idempotency guard that protects the upload path does not apply here: there is no
# record to refuse a second time. This was observed for real while benchmarking,
# when one `aws lambda invoke` produced two invocations 11 seconds apart.
lambda_client = boto3.client(
    "lambda",
    # total_max_attempts rather than max_attempts: botocore normalises
    # max_attempts=0 into one total attempt, which is right but relies on a
    # conversion a reader has to know about. This says "once, ever".
    config=Config(read_timeout=25, connect_timeout=5, retries={"total_max_attempts": 1}),
)

PROCESS_FUNCTION = os.environ["PROCESS_FUNCTION"]

# Attributes the search needs. Everything else — the timing measurements, the model
# version, the ttl — stays out of the response.
#
# Worth being precise about what this saves, because it is a common
# misunderstanding: a ProjectionExpression does not reduce what DynamoDB charges
# for. The whole item is read and billed either way. What it reduces is bytes over
# the wire, and therefore how many items fit in the 1 MB page below — which is the
# thing that actually matters here.
PROJECTION = "fileId, #s, tags, s3Key, thumbKey, uploadedBy, createdAt"

# Upper bound on one response. A hundred signed URLs is already a large payload,
# and a client that needs more needs pagination rather than a bigger page.
MAX_RESULTS = 100

# API Gateway caps a request payload at 10 MB, and base64 inflates by a third, so
# roughly 7 MB of original file. Unrelated to the 25 MB upload limit, because that
# path never sends bytes through the API at all — which is the reason it can be
# larger.
MAX_QUERY_BYTES = 7 * 1024 * 1024


def done_records():
    """Yield every finished record, one page at a time.

    The paginator is not optional. A Scan returns at most 1 MB per call and hands
    back a continuation key; reading one page and stopping returns a partial answer
    with no error and no indication that anything is missing. At the current size
    that never happens, which is exactly what makes it dangerous — it would first
    appear as "the tagging is wrong" long after this code was written.

    The status filter is applied by DynamoDB, but after the 1 MB has been read: a
    filter narrows the response, not the scan. It is here to keep PENDING and
    FAILED records out of results, not to make the scan cheaper.
    """
    paginator = dynamodb.get_paginator("scan")
    pages = paginator.paginate(
        TableName=TABLE_NAME,
        ProjectionExpression=PROJECTION,
        FilterExpression="#s = :done",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":done": {"S": "DONE"}},
    )
    for page in pages:
        yield from page["Items"]


def counts(item: dict) -> dict[str, int]:
    """Pull {name: count} out of a raw DynamoDB item."""
    return {
        name: int(value["N"]) for name, value in item.get("tags", {}).get("M", {}).items()
    }


def readable(item: dict) -> dict:
    """The shape a client receives for one hit."""
    record = {key: plain(_unwrap(value)) for key, value in item.items()}
    return {
        "fileId": record["fileId"],
        "tags": counts(item),
        "uploadedBy": record.get("uploadedBy"),
        "createdAt": record.get("createdAt"),
        **urls_for(record),
    }


def _unwrap(value: dict):
    """Turn one DynamoDB attribute value into a plain Python one.

    The low-level client is used rather than the resource so that the paginator is
    available, which means unwrapping is this module's job.
    """
    for kind, inner in value.items():
        if kind == "N":
            return int(inner) if "." not in inner else float(inner)
        if kind == "M":
            return {key: _unwrap(nested) for key, nested in inner.items()}
        if kind == "L":
            return [_unwrap(nested) for nested in inner]
        if kind == "BOOL":
            return inner
        if kind == "NULL":
            return None
        return inner
    return None


# --- predicates --------------------------------------------------------------


def has_at_least(tags: dict[str, int], wanted: dict[str, int]) -> bool:
    """AND, with minimum counts: every requested species, at the requested number.

    "at least two wombats" excludes a file with one, which is why the counts are
    stored at all rather than a set of names.
    """
    return all(tags.get(species, 0) >= minimum for species, minimum in wanted.items())


def has_any(tags: dict[str, int], wanted: list[str]) -> bool:
    """OR: any one of the listed species, at any count."""
    return any(tags.get(species, 0) >= 1 for species in wanted)


def is_superset_of(tags: dict[str, int], query: dict[str, int]) -> bool:
    """Every species in the query file, at least as many of each.

    Deliberately not equality. A photograph holding a dingo and a wallaby is a
    reasonable answer to "find pictures like this dingo", while a strict match would
    return almost nothing.
    """
    return has_at_least(tags, query)


# --- the query-by-file round trip -------------------------------------------


def tags_of_query_file(encoded: str, extension: str, correlation: str) -> dict[str, int]:
    """Identify an uploaded sample without storing it.

    The sample is never written to S3 or DynamoDB — that is a requirement, not an
    optimisation, and it is why the tagging function has a separate query mode
    rather than a flag on the normal path. Only one function in this system carries
    the models, so this one asks it rather than loading them itself.
    """
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ClientError(400, f"file is not valid base64: {error}") from error

    if not raw:
        raise ClientError(400, "file is empty")
    if len(raw) > MAX_QUERY_BYTES:
        limit = MAX_QUERY_BYTES // (1024 * 1024)
        raise ClientError(400, f"query file exceeds {limit} MB once base64-encoded")

    payload = {
        "query_mode": True,
        "file_bytes": encoded,
        "ext": extension,
        "correlationId": correlation,
    }

    try:
        result = lambda_client.invoke(
            FunctionName=PROCESS_FUNCTION,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
    except botocore.exceptions.ReadTimeoutError as error:
        # The tagging function is cold: 20 s warm-up against a 25 s budget leaves
        # very little, and the first invocation after a deployment takes over 40 s.
        #
        # This is the honest limitation of serving inference synchronously behind an
        # API Gateway that cuts off at 29 s. The alternatives were measured:
        # provisioned concurrency removes it for about $15 a month, 15% of this
        # project's budget for one route, and making the route asynchronous like
        # upload adds a second polling flow. Telling the caller what happened and
        # when to retry costs nothing.
        # docs/adr/0005-accept-the-29-second-ceiling-on-query-by-file.md has the
        # priced comparison of all three options.
        log_event("query mode timed out", correlation, function=PROCESS_FUNCTION)
        raise ClientError(503, "the model is warming up, retry in about 30 seconds") from error

    body = json.loads(result["Payload"].read())

    if result.get("FunctionError"):
        # The tagging function raised. Its message is not shown to the caller: it
        # describes internals, and "unreadable image" and "model failed to load"
        # need different answers that this function cannot distinguish.
        log_event("query mode failed", correlation, error=str(body)[:300])
        raise ClientError(502, "could not identify the sample")

    return {name: int(count) for name, count in body.get("tags", {}).items()}


# --- routes ------------------------------------------------------------------


def by_tags(body, correlation: str) -> dict:
    if not isinstance(body, dict) or not body:
        raise ClientError(400, 'body must be a non-empty object, e.g. {"wombat": 2}')
    try:
        wanted = {str(name): int(minimum) for name, minimum in body.items()}
    except (TypeError, ValueError) as error:
        raise ClientError(400, "every value must be a whole number") from error
    if any(minimum < 1 for minimum in wanted.values()):
        raise ClientError(400, "minimum counts must be at least 1")

    log_event("search by tags", correlation, wanted=wanted)
    return {"query": wanted, "results": collect(lambda tags: has_at_least(tags, wanted))}


def by_species(body, correlation: str) -> dict:
    if not isinstance(body, list) or not body:
        raise ClientError(400, 'body must be a non-empty array, e.g. ["dingo"]')
    wanted = [str(name) for name in body]

    log_event("search by species", correlation, wanted=wanted)
    return {"query": wanted, "results": collect(lambda tags: has_any(tags, wanted))}


def by_file(body, correlation: str) -> dict:
    if not isinstance(body, dict) or "file" not in body:
        raise ClientError(400, 'body must be {"file": "<base64>", "ext": "jpg"}')

    query_tags = tags_of_query_file(body["file"], str(body.get("ext", "jpg")), correlation)

    if not query_tags:
        # No animals found, so every file is trivially a superset. Returning
        # everything would be technically correct and useless.
        log_event("search by file found nothing to match", correlation)
        return {"query": {}, "results": [], "note": "no animals detected in the sample"}

    log_event("search by file", correlation, queryTags=query_tags)
    return {
        "query": query_tags,
        "results": collect(lambda tags: is_superset_of(tags, query_tags)),
    }


def collect(predicate) -> list[dict]:
    """Scan, filter in memory, and stop at MAX_RESULTS.

    Stopping early also stops signing URLs, which is the expensive part of building
    a response — two signatures per hit.
    """
    hits = []
    for item in done_records():
        if predicate(counts(item)):
            hits.append(readable(item))
            if len(hits) >= MAX_RESULTS:
                break
    return hits


ROUTES = {
    "/search/tags": by_tags,
    "/search/species": by_species,
    "/search/byfile": by_file,
}


@handle_errors
def handler(event, _context):
    cid = correlation_id(event)

    # REST API proxy integration reports the configured path template here. Matching
    # on it rather than on the raw path means a request to an undeclared route never
    # reaches this function at all — API Gateway rejects it first.
    route = ROUTES.get(event.get("resource", ""))
    if route is None:
        raise ClientError(404, f"unknown route: {event.get('resource')}")

    return response(200, route(json_body(event), cid))
