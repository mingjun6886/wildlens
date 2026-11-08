"""The status endpoint returns the video fields once a video is DONE."""

import json
from decimal import Decimal

import status


class FakeTable:
    def __init__(self, item):
        self.item = item

    def get_item(self, Key):  # noqa: N803 - boto3's parameter name
        return {"Item": self.item}


def event(file_id: str) -> dict:
    return {
        "pathParameters": {"fileId": file_id},
        "requestContext": {"requestId": "t-1", "authorizer": {"claims": {"email": "a@b.c"}}},
    }


def test_a_finished_video_reports_its_frame_count_and_duration(monkeypatch):
    monkeypatch.setattr(
        status,
        "table",
        FakeTable(
            {
                "fileId": "v",
                "status": "DONE",
                "type": "video",
                "tags": {"emu": Decimal(2)},
                "frameCount": Decimal(42),
                "durationSec": Decimal("75.2"),
                "thumbKey": "v.jpg",
                "s3Key": "v.mp4",
            }
        ),
    )

    body = json.loads(status.handler(event("v"), None)["body"])

    assert body["type"] == "video"
    assert body["frameCount"] == 42
    assert body["durationSec"] == 75.2
