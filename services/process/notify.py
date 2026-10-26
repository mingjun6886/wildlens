"""Tell subscribers that a file has been tagged.

Kept out of app.py so it can be tested without the models: app.py imports the
detector, which no unit test can load.

A failure here must not fail the file. The tags are already written. Raising would
make SQS redeliver the message, claim() would see DONE and skip it, and the
notification would be lost anyway, while a correctly tagged file was reported as a
failure. So the error is logged with the correlation id and the caller carries on;
the runbook has a Logs Insights query for finding them.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger()

SUBJECT = "WildLens: new sighting"


def publish_tags(
    client, topic_arn: str, file_id: str, tags: dict[str, int], correlation_id: str
) -> bool:
    """Publish one message for a file that reached DONE. Never raises."""
    if not tags:
        # Nothing was detected, so no subscription could match.
        return False
    if not topic_arn:
        logger.error(
            json.dumps(
                {
                    "msg": "notification not sent: TAGS_TOPIC_ARN is not set",
                    "correlationId": correlation_id,
                    "fileId": file_id,
                }
            )
        )
        return False
    try:
        # The body is the JSON itself, which is what the subscribers' filter
        # policies read (FilterPolicyScope=MessageBody). Email shows it as is.
        client.publish(
            TopicArn=topic_arn,
            Subject=SUBJECT,
            Message=json.dumps({"fileId": file_id, "tags": tags}, indent=2),
        )
    except Exception:
        logger.exception(
            json.dumps(
                {
                    "msg": "notification not sent: publish failed",
                    "correlationId": correlation_id,
                    "fileId": file_id,
                }
            )
        )
        return False
    return True
