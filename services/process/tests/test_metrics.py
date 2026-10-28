"""One EMF line per metric: its shape, and the promise never to raise."""

import json
import sys

import metrics


def emitted(capsys) -> dict:
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_emit_writes_one_json_line_in_the_embedded_metric_format(capsys):
    metrics.emit("InferenceDuration", 7150, "Milliseconds")
    line = emitted(capsys)
    directive = line["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "WildLens/Process"
    assert directive["Metrics"] == [{"Name": "InferenceDuration", "Unit": "Milliseconds"}]
    assert line["InferenceDuration"] == 7150
    assert isinstance(line["_aws"]["Timestamp"], int)


def test_emit_uses_an_empty_dimension_set_so_each_name_is_one_billed_metric(capsys):
    metrics.emit("TagsPerFile", 4, "Count")
    assert emitted(capsys)["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]


def test_emit_never_raises_when_stdout_fails(monkeypatch):
    class Broken:
        def write(self, _text):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    monkeypatch.setattr(sys, "stdout", Broken())
    metrics.emit("TagsPerFile", 1, "Count")  # a metric must not fail a tagged file
