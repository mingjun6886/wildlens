"""The video branch of process_object: what it records, publishes and cleans up.

The detector and the tagger are stand-ins, as in test_app_publish.py. tag_image
returns the next entry of app.frame_tags for each frame, so a test decides what
each frame "contains".
"""

import importlib
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path, make_video):
    fake_loader = types.ModuleType("model_loader")
    fake_loader.artefact_bucket = lambda: "models"
    fake_loader.artefact_version = lambda: "v-test"
    fake_loader.get_models = lambda *args: (None, None, [], {})
    fake_tagger = types.ModuleType("tagger")
    fake_tagger.tag_image = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, "model_loader", fake_loader)
    monkeypatch.setitem(sys.modules, "tagger", fake_tagger)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")

    scratch = tmp_path / "lambda-tmp"
    scratch.mkdir()
    monkeypatch.setattr(module, "TMP", scratch)

    module.source = make_video("clip.avi", seconds=5, fps=10)
    module.uploads = []
    module.recorded = {}
    module.failures = []
    module.published = []
    module.emitted = []
    module.frame_tags = []

    class FakeS3:
        def download_file(self, bucket, key, destination):
            Path(destination).write_bytes(module.source.read_bytes())

        def upload_file(self, source, bucket, key, ExtraArgs=None):  # noqa: N803 - boto3's name
            module.uploads.append((bucket, key, ExtraArgs))

    monkeypatch.setattr(module, "s3", FakeS3())
    monkeypatch.setattr(module, "claim", lambda file_id, cid: True)
    monkeypatch.setattr(
        module, "record_success", lambda file_id, cid, **fields: module.recorded.update(fields)
    )
    monkeypatch.setattr(
        module, "record_failure", lambda file_id, cid, reason: module.failures.append(reason)
    )
    monkeypatch.setattr(
        module, "publish_tags", lambda client, topic, fid, tags, cid: module.published.append(tags)
    )
    monkeypatch.setattr(
        module, "emit", lambda name, value, unit: module.emitted.append((name, value, unit))
    )
    monkeypatch.setattr(
        module,
        "tag_image",
        lambda *args, **kwargs: module.frame_tags.pop(0) if module.frame_tags else {},
    )
    yield module
    sys.modules.pop("app", None)


def test_a_video_record_has_its_type_frame_count_duration_and_max_n_tags(app):
    app.frame_tags = [{}, {"dingo": 1}, {"dingo": 2, "emu": 1}, {"emu": 3}, {}]

    result = app.process_object("raw", "clip.avi", "cid", cold_start=False)

    assert app.recorded["type"] == "video"
    assert app.recorded["frameCount"] == Decimal(5)
    assert app.recorded["durationSec"] == Decimal("5.0")
    assert app.recorded["tags"] == {"dingo": Decimal(2), "emu": Decimal(3)}
    assert result["tags"] == {"dingo": 2, "emu": 3}


def test_the_video_thumbnail_is_a_jpeg_named_after_the_file(app):
    app.process_object("raw", "clip.avi", "cid", cold_start=False)

    file_id = app.sha256_of(app.source)
    assert app.recorded["thumbKey"] == f"{file_id}.jpg"
    assert app.uploads == [("test-thumb", f"{file_id}.jpg", {"ContentType": "image/jpeg"})]


def test_the_thumbnail_comes_from_the_busiest_frame(app, monkeypatch):
    sources = []
    monkeypatch.setattr(
        app, "make_thumbnail", lambda source, destination: sources.append(Path(source).name)
    )
    app.frame_tags = [{}, {"emu": 3}, {"dingo": 1}, {}, {}]

    app.process_object("raw", "clip.avi", "cid", cold_start=False)

    assert sources == ["frame-001.jpg"]


def test_the_max_n_tags_are_published(app):
    app.frame_tags = [{"emu": 1}, {"emu": 2}, {}, {}, {}]
    app.process_object("raw", "clip.avi", "cid", cold_start=False)
    assert app.published == [{"emu": 2}]


def test_tags_per_file_is_the_max_n_total(app):
    app.frame_tags = [{"emu": 1}, {"emu": 2, "dingo": 1}, {}, {}, {}]
    app.process_object("raw", "clip.avi", "cid", cold_start=False)
    assert ("TagsPerFile", 3, "Count") in app.emitted
    assert [name for name, _v, _u in app.emitted].count("InferenceDuration") == 1


def test_a_corrupt_video_is_failed_and_not_retried(app, tmp_path):
    app.source = tmp_path / "junk.avi"
    app.source.write_bytes(b"\x00garbage" * 100)

    with pytest.raises(app.PermanentFailure, match="not a readable video"):
        app.process_object("raw", "junk.avi", "cid", cold_start=False)

    assert app.failures and app.failures[0].startswith("not a readable video")


def test_the_time_guard_records_a_partial_result(app):
    clock = iter([120_000, 120_000, 1_000])
    app.frame_tags = [{"emu": 1}, {"emu": 2}, {"emu": 9}]

    app.process_object(
        "raw", "clip.avi", "cid", cold_start=False, remaining_ms=lambda: next(clock, 0)
    )

    assert app.recorded["frameCount"] == Decimal(2)
    assert app.recorded["tags"] == {"emu": Decimal(2)}


def test_running_out_of_time_before_the_first_frame_fails(app):
    with pytest.raises(app.PermanentFailure, match="ran out of time"):
        app.process_object("raw", "clip.avi", "cid", cold_start=False, remaining_ms=lambda: 0)
    assert app.failures[0].startswith("ran out of time")


def test_frame_files_are_removed_after_success(app):
    app.process_object("raw", "clip.avi", "cid", cold_start=False)
    assert list(app.TMP.iterdir()) == []


def test_frame_files_are_removed_after_a_failure(app):
    with pytest.raises(app.PermanentFailure):
        app.process_object("raw", "clip.avi", "cid", cold_start=False, remaining_ms=lambda: 0)
    assert list(app.TMP.iterdir()) == []


def test_an_image_still_records_type_image_without_frame_fields(app, tmp_path):
    from PIL import Image

    app.source = tmp_path / "still.png"
    Image.new("RGB", (32, 32), "white").save(app.source)

    app.process_object("raw", "wild.png", "cid", cold_start=False)

    assert app.recorded["type"] == "image"
    assert "frameCount" not in app.recorded
    assert "durationSec" not in app.recorded


def test_the_direct_path_passes_the_lambda_deadline(app):
    class Context:
        def get_remaining_time_in_millis(self):
            return 0

    with pytest.raises(app.PermanentFailure, match="ran out of time"):
        app.handler({"bucket": "raw", "key": "clip.avi"}, Context())


def test_the_queue_path_passes_the_lambda_deadline(app):
    """A permanent failure on the queue path is logged and swallowed, so the
    evidence that the deadline arrived is the recorded failure reason."""

    class Context:
        def get_remaining_time_in_millis(self):
            return 0

    body = '{"Records": [{"s3": {"bucket": {"name": "raw"}, "object": {"key": "clip.avi"}}}]}'
    result = app.handler({"Records": [{"messageId": "m1", "body": body}]}, Context())

    assert result == {"batchItemFailures": []}
    assert app.failures[0].startswith("ran out of time")
