"""What app.py reports about itself: metrics, spans and the query-mode log line.

The detector and the tagger are replaced with stand-ins before app is imported,
exactly as test_app_publish.py does, because this is about what app does around
them and not about inference.
"""

import base64
import importlib
import logging
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def app(monkeypatch, tmp_path):
    fake_loader = types.ModuleType("model_loader")
    fake_loader.artefact_bucket = lambda: "models"
    fake_loader.artefact_version = lambda: "v-test"
    fake_loader.get_models = lambda *args: (None, None, [], {})
    fake_tagger = types.ModuleType("tagger")
    fake_tagger.tag_image = lambda *args, **kwargs: {"dingo": 2}
    monkeypatch.setitem(sys.modules, "model_loader", fake_loader)
    monkeypatch.setitem(sys.modules, "tagger", fake_tagger)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "white").save(image)

    class FakeS3:
        def download_file(self, bucket, key, destination):
            Path(destination).write_bytes(image.read_bytes())

        def upload_file(self, *args, **kwargs):
            return None

    monkeypatch.setattr(module, "s3", FakeS3())
    monkeypatch.setattr(module, "claim", lambda file_id, cid: True)
    monkeypatch.setattr(module, "record_success", lambda file_id, cid, **fields: None)
    monkeypatch.setattr(module, "record_failure", lambda file_id, cid, reason: None)
    monkeypatch.setattr(module, "publish_tags", lambda *args, **kwargs: True)

    module.real_load_and_tag = module.load_and_tag
    monkeypatch.setattr(module, "load_and_tag", lambda path, cid: ({"dingo": 3}, 1, 2))

    module.emitted = []
    module.spans = []

    @contextmanager
    def record_span(name):
        module.spans.append(name)
        yield

    monkeypatch.setattr(module, "span", record_span)
    monkeypatch.setattr(
        module, "emit", lambda name, value, unit: module.emitted.append((name, value, unit))
    )
    yield module
    sys.modules.pop("app", None)


def names(app) -> list[str]:
    return [name for name, _value, _unit in app.emitted]


def test_a_stored_file_emits_inference_time_and_its_tag_count(app):
    app.process_object("raw", "wild.png", "cid", cold_start=False)
    assert ("InferenceDuration", 2, "Milliseconds") in app.emitted
    assert ("TagsPerFile", 3, "Count") in app.emitted
    assert "ModelLoadDuration" not in names(app)
    assert "ColdStartDuration" not in names(app)


def test_a_cold_stored_file_also_emits_model_load_and_cold_start_time(app):
    app.process_object("raw", "wild.png", "cid", cold_start=True)
    values = {name: value for name, value, _unit in app.emitted}
    assert values["ModelLoadDuration"] == 1
    assert values["ColdStartDuration"] >= 0


def test_a_file_with_no_animals_still_counts_as_processed(app, monkeypatch):
    monkeypatch.setattr(app, "load_and_tag", lambda path, cid: ({}, 1, 2))
    app.process_object("raw", "wild.png", "cid", cold_start=False)
    assert ("TagsPerFile", 0, "Count") in app.emitted


def test_a_file_already_done_emits_nothing(app, monkeypatch):
    monkeypatch.setattr(app, "claim", lambda file_id, cid: False)
    app.process_object("raw", "wild.png", "cid", cold_start=True)
    assert app.emitted == []


def test_a_file_that_fails_permanently_emits_nothing(app):
    with pytest.raises(app.PermanentFailure):
        app.process_object("raw", "wild.txt", "cid", cold_start=True)
    assert app.emitted == []


def test_an_unreadable_image_emits_nothing(app, monkeypatch):
    class Garbage:
        def download_file(self, bucket, key, destination):
            Path(destination).write_bytes(b"not an image")

    monkeypatch.setattr(app, "s3", Garbage())
    with pytest.raises(app.PermanentFailure):
        app.process_object("raw", "wild.png", "cid", cold_start=True)
    assert app.emitted == []


def test_query_mode_emits_model_metrics_but_never_a_tag_count(app):
    app._COLD_START = True
    event = {"query_mode": True, "file_bytes": base64.b64encode(b"x").decode(), "ext": "png"}
    app.handler(event, None)
    assert "TagsPerFile" not in names(app)
    assert {"InferenceDuration", "ModelLoadDuration", "ColdStartDuration"} <= set(names(app))


def test_query_mode_is_logged_so_the_dashboard_can_count_it(app, caplog):
    app._COLD_START = True
    event = {"query_mode": True, "file_bytes": base64.b64encode(b"x").decode(), "ext": "png"}
    with caplog.at_level(logging.INFO):
        app.handler(event, None)
    lines = [r.getMessage() for r in caplog.records if '"msg": "query tagged"' in r.getMessage()]
    assert len(lines) == 1
    assert '"coldStart": 1' in lines[0]
    assert '"inferenceMs": 2' in lines[0]


def test_a_stored_file_opens_a_thumbnail_span(app):
    app.process_object("raw", "wild.png", "cid", cold_start=False)
    assert "thumbnail" in app.spans


def test_load_and_tag_wraps_model_load_and_tagging_in_spans(app, monkeypatch):
    monkeypatch.setattr(app, "load_and_tag", app.real_load_and_tag)
    app.load_and_tag(Path("x.png"), "cid")
    assert app.spans == ["model-load", "tagging"]
