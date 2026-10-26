"""The call site: one publish per file that reaches DONE, and never otherwise.

app.py imports the detector and the tagger, which pull in torch and MegaDetector.
They are replaced with stand-ins before app is imported, because this test is about
what app does around them, not about inference.
"""

import importlib
import sys
import types
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def app(monkeypatch, tmp_path):
    fake_loader = types.ModuleType("model_loader")
    fake_loader.artefact_bucket = lambda: "models"
    fake_loader.artefact_version = lambda: "v-test"
    fake_loader.get_models = lambda: None
    fake_tagger = types.ModuleType("tagger")
    fake_tagger.tag_image = lambda *args, **kwargs: {}
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
    monkeypatch.setattr(module, "load_and_tag", lambda path, cid: ({"dingo": 3}, 1, 2))
    monkeypatch.setenv("TAGS_TOPIC_ARN", "arn:aws:sns:ap-southeast-2:1:tags")

    published = []
    monkeypatch.setattr(
        module, "publish_tags", lambda *args, **kwargs: published.append(args) or True
    )
    module.published = published
    yield module
    sys.modules.pop("app", None)


def test_a_file_that_reaches_done_publishes_once(app):
    result = app.process_object("raw", "wild.png", "cid-1", cold_start=False)
    assert len(app.published) == 1
    _client, topic, file_id, tags, cid = app.published[0]
    assert topic == "arn:aws:sns:ap-southeast-2:1:tags"
    assert file_id == result["fileId"]
    assert tags == {"dingo": 3}
    assert cid == "cid-1"


def test_a_file_already_done_publishes_nothing(app, monkeypatch):
    monkeypatch.setattr(app, "claim", lambda file_id, cid: False)
    assert app.process_object("raw", "wild.png", "cid-1", cold_start=False)["skipped"]
    assert app.published == []


def test_query_mode_publishes_nothing(app):
    import base64

    event = {"query_mode": True, "file_bytes": base64.b64encode(b"x").decode(), "ext": "png"}
    app.handler(event, None)
    assert app.published == []
