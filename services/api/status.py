"""GET /files/{fileId} — what happened to one upload.

This endpoint is what makes the asynchronous design usable. Tagging takes six
seconds warm and twenty cold, which is far too long to hold an HTTP request
open, so the upload returns immediately and the client polls here.

The four states map onto four answers:

    PENDING     the object has not arrived, or the queue has not reached it
    PROCESSING  a worker holds it now
    DONE        tags and image URLs are below
    FAILED      errorReason says why, and retrying the same bytes will not help

A caller that cannot tell PENDING from FAILED will show a spinner forever, which
is why the Phase 6 work included giving FAILED records an explicit seven-day
expiry instead of the accidental one-hour one they inherited from the claim.
"""

from __future__ import annotations

import boto3

from common import (
    TABLE_NAME,
    ClientError,
    correlation_id,
    handle_errors,
    log_event,
    path_param,
    plain,
    response,
    urls_for,
)

table = boto3.resource("dynamodb").Table(TABLE_NAME)

# Fields returned to the client. Listing them rather than returning the record
# wholesale keeps internal columns - ttl, the timing measurements, coldStart -
# out of the API, so they can change without being a breaking change.
PUBLIC_FIELDS = ("status", "type", "tags", "uploadedBy", "createdAt", "errorReason")


@handle_errors
def handler(event, _context):
    cid = correlation_id(event)
    file_id = path_param(event, "fileId")

    # Any signed-in user may read any record. This is a shared observation
    # platform rather than private storage: the value of a wildlife sighting is
    # that others can search it. uploadedBy is returned so a record still has an
    # author, and restricting reads to the uploader would make the search
    # endpoints pointless.
    item = table.get_item(Key={"fileId": file_id}).get("Item")

    if not item:
        # Genuinely absent, or expired. A PENDING upload that never completed is
        # reaped after an hour and a FAILED one after seven days, so a 404 here
        # can also mean "too long ago" - which is why the client is told to treat
        # it as unknown rather than as never-existed.
        raise ClientError(404, "no record for that fileId")

    body = {"fileId": file_id}
    body.update({field: plain(item[field]) for field in PUBLIC_FIELDS if field in item})

    # Signed URLs only once there is something to point at. Generating them for a
    # PROCESSING record would hand the client links to objects that do not exist
    # yet, and it would spend two signing calls per poll.
    if item.get("status") == "DONE":
        body.update(urls_for(item))

    log_event("status read", cid, fileId=file_id, status=item.get("status"))
    return response(200, body)
