"""A span must be invisible when tracing is absent, and honest when it is not."""

import pytest

import tracing


class FakeRecorder:
    """Records enter and exit, the way the real recorder opens and closes a subsegment."""

    def __init__(self):
        self.events = []

    def in_subsegment(self, name):
        events = self.events

        class Context:
            def __enter__(self):
                events.append(("enter", name))
                return self

            def __exit__(self, error_type, error, traceback):
                events.append(("exit", name, error_type))
                return False

        return Context()


def test_span_does_nothing_without_the_sdk(monkeypatch):
    monkeypatch.setattr(tracing, "xray_recorder", None)
    ran = False
    with tracing.span("tagging"):
        ran = True
    assert ran


def test_span_opens_and_closes_a_subsegment_with_the_given_name(monkeypatch):
    recorder = FakeRecorder()
    monkeypatch.setattr(tracing, "xray_recorder", recorder)
    with tracing.span("model-load"):
        pass
    assert recorder.events == [("enter", "model-load"), ("exit", "model-load", None)]


def test_span_closes_the_subsegment_and_lets_the_error_through(monkeypatch):
    recorder = FakeRecorder()
    monkeypatch.setattr(tracing, "xray_recorder", recorder)
    with pytest.raises(ValueError, match="boom"), tracing.span("tagging"):
        raise ValueError("boom")
    assert recorder.events[-1] == ("exit", "tagging", ValueError)
