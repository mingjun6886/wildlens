# Observability (Phase 9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alarms that email the owner, X-Ray traces with subsegments for the tagger, four custom metrics, API access logs and one dashboard, so a failure or a slow request can be found without guessing.

**Architecture:** `process` prints EMF metric lines and opens X-Ray subsegments through two new flat modules (`metrics.py`, `tracing.py`). Terraform turns on active tracing for the five functions and the API stage, adds an account-level API Gateway logging role (`apigw_logging`), and a new `observability` module that owns the alerts topic, the alarms and the dashboard.

**Tech Stack:** Python 3.11, `aws-xray-sdk` 2.15.0 (image only), CloudWatch EMF, Terraform 1.14 with AWS provider 6.67, checkov 3.3.26.

**Spec:** `docs/superpowers/specs/2026-10-29-observability-design.md`. Where this plan differs from the spec, the difference is in "Rulings made while planning" below and Task 7 brings the spec back in line.

## Global Constraints

- **English only** in every committed file. The pre-commit hook rejects Vietnamese characters.
- Python line length 100; ruff rules `E W F I B C4 UP S T20`. A `print()` needs `# noqa: T201` with a reason.
- `services/process/Dockerfile` copies modules **one by one**: every new flat module imported by `app.py` needs a `COPY` line, and `known-first-party` in `pyproject.toml` lists it.
- Terraform: `terraform fmt -recursive infra/terraform` clean; resource comments explain *why*; a checkov finding is suppressed only with `#checkov:skip=<ID>:<reason>` on the resource.
- Always `export AWS_PROFILE=wildlens`. **Never** `apply`, push, merge, build or push an image, or send mail (`set-alarm-state`) without the owner's go-ahead. Never commit to `main`; work is on `observe/phase-9`.
- The owner's email address goes only into an untracked `terraform.tfvars`, and only when the owner says to use it.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH`.
- Tests: `.venv/bin/pytest -m "not integration" -q`, lint: `.venv/bin/ruff check . && .venv/bin/ruff format --check .`.

## Rulings made while planning

1. **`tracing.py`, not `trace.py`.** A flat module called `trace` sits first on `sys.path` in Lambda and would shadow the standard library's `trace`. (Spec already corrected.)
2. **`patch(["botocore"])`, not `patch(["boto3"])`.** The SDK's list of patchable libraries names `botocore`; boto3 clients go through it. Task 3 proves the call on the real SDK before the image is built.
3. **The `thumbnail` span wraps `make_thumbnail` only.** The thumbnail upload is already an S3 subsegment through the botocore patch, so a span around it would nest the same time twice.
4. **The alerts topic is unencrypted** and `CKV_AWS_26` is skipped on it (spec section 3.1: CloudWatch cannot use the AWS-managed SNS key).
5. **`CKV2_AWS_4` is resolved by running checkov**, as spec section 6 says: Task 5 keeps the skip with a rewritten reason if it still fires.
6. **The query-mode log line carries `coldStart` as an integer** (`1` or `0`), unlike the other lines. Logs Insights can sum an integer; a JSON boolean is not guaranteed to be summable.

## Review Focus

The inputs and failure modes the spec implies that most need a deliberate look, each with the test that pins it:

1. A metric must never fail a tagged file: `emit` with a broken stdout (Task 1).
2. A span must never swallow or hide an error from its body, and must close its subsegment when the body raises (Task 2).
3. Metrics describe files that were processed: a file skipped as already `DONE`, and one that fails permanently, emit nothing (Task 3).
4. Query mode is not a stored file: it emits the model metrics and never `TagsPerFile`, and it is countable in the logs (Task 3).
5. A file with no animals is still a processed file: `TagsPerFile` is emitted as `0`, so "files per hour" counts it (Task 3).
6. An alarm that fires must reach a person: proven only by `set-alarm-state` in Task 8, because the topic is unencrypted for exactly this reason.

---

### Task 1: `metrics.py`

**Files:**
- Create: `services/process/metrics.py`
- Create: `services/process/tests/test_metrics.py`
- Modify: `pyproject.toml` (the `known-first-party` list)

**Interfaces:**
- Consumes: nothing.
- Produces: `metrics.emit(name: str, value: float, unit: str) -> None`. Prints one single-line JSON object in the embedded metric format to stdout. Never raises. Namespace `WildLens/Process`, dimension set `[[]]`.

- [ ] **Step 1: Write the failing tests**

Create `services/process/tests/test_metrics.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest services/process/tests/test_metrics.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'metrics'`.

- [ ] **Step 3: Write the module**

Create `services/process/metrics.py`:

```python
"""Custom CloudWatch metrics through the embedded metric format (EMF).

A line printed in this shape becomes a metric when CloudWatch reads the function's
log stream. There is no API call to make, no PutMetricData permission to grant, and
nothing on the request path that can fail.

The dimension set is empty on purpose. Each distinct set of dimensions is billed as
a separate metric, so with none there are exactly as many metrics as names: four,
about $1.20 a month. The cost is that nothing can be split by function or version,
which is fine for an estate with one function that matters.
"""

from __future__ import annotations

import contextlib
import json
import time

NAMESPACE = "WildLens/Process"


def emit(name: str, value: float, unit: str) -> None:
    """Print one metric. Never raises: a metric must not fail a correctly tagged file."""
    line = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [[]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        name: value,
    }
    with contextlib.suppress(Exception):
        print(json.dumps(line), flush=True)  # noqa: T201 - stdout is the metrics channel
```

- [ ] **Step 4: Add the module to `known-first-party`**

In `pyproject.toml`, add `"metrics",` to the `known-first-party` list, keeping the list alphabetical (it goes between `"media_utils"` and the next entry).

- [ ] **Step 5: Run the tests and the linter**

Run: `.venv/bin/pytest services/process/tests/test_metrics.py -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: `3 passed`, then ruff clean. If `ruff format --check` reports a file, run `.venv/bin/ruff format .` and re-run.

- [ ] **Step 6: Commit**

```bash
git add services/process/metrics.py services/process/tests/test_metrics.py pyproject.toml
git commit -m "feat(observe): add EMF metric emission to the tagger

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```

---

### Task 2: `tracing.py`

**Files:**
- Create: `services/process/tracing.py`
- Create: `services/process/tests/test_tracing.py`
- Modify: `pyproject.toml` (`known-first-party`)

**Interfaces:**
- Consumes: nothing.
- Produces: `tracing.span(name: str)`, a context manager; and the module attribute `tracing.xray_recorder`, which is `None` when `aws_xray_sdk` is not installed. With the SDK installed it opens `xray_recorder.in_subsegment(name)`.

- [ ] **Step 1: Write the failing tests**

Create `services/process/tests/test_tracing.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest services/process/tests/test_tracing.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'tracing'`.

- [ ] **Step 3: Write the module**

Create `services/process/tracing.py`:

```python
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
```

- [ ] **Step 4: Add the module to `known-first-party`**

In `pyproject.toml`, add `"tracing",` to `known-first-party`, in alphabetical position.

- [ ] **Step 5: Run the tests and the linter**

Run: `.venv/bin/pytest services/process/tests/test_tracing.py -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: `3 passed`, ruff clean. (`with pytest.raises(...), tracing.span(...)` is one statement on purpose; if ruff format rewraps it, accept its output.)

- [ ] **Step 6: Commit**

```bash
git add services/process/tracing.py services/process/tests/test_tracing.py pyproject.toml
git commit -m "feat(observe): add an optional X-Ray span helper for the tagger

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```

---

### Task 3: Wire metrics and spans into `app.py`, and ship them in the image

**Files:**
- Modify: `services/process/app.py`
- Create: `services/process/tests/test_app_observability.py`
- Modify: `services/process/Dockerfile`

**Interfaces:**
- Consumes: `metrics.emit(name, value, unit)`, `tracing.span(name)` (Tasks 1 and 2).
- Produces: `app.emit_model_metrics(model_load_ms: int, inference_ms: int, cold_start: bool, total_ms: int) -> None`; the log line `query tagged` with integer fields `coldStart`, `modelLoadMs`, `inferenceMs`; spans `model-load`, `tagging`, `thumbnail`. Task 6's dashboard reads the metric names and the log line.

- [ ] **Step 1: Write the failing tests**

Create `services/process/tests/test_app_observability.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest services/process/tests/test_app_observability.py -q`
Expected: failures or errors because `app` has no attribute `span` or `emit` (`monkeypatch.setattr` raises `AttributeError`). All ten tests fail the same way, which is the right reason.

- [ ] **Step 3: Edit `services/process/app.py`**

Apply these edits with the Edit tool. Each `old` string occurs exactly once.

(a) Imports.

old:
```python
from media_utils import make_thumbnail
from model_loader import artefact_bucket, artefact_version, get_models
from notify import publish_tags
from tagger import tag_image
```
new:
```python
from media_utils import make_thumbnail
from metrics import emit
from model_loader import artefact_bucket, artefact_version, get_models
from notify import publish_tags
from tagger import tag_image
from tracing import span
```

(b) The shared metrics helper, placed immediately before `load_and_tag`.

old:
```python
def load_and_tag(path: Path, correlation_id: str) -> tuple[dict[str, int], int, int]:
```
new:
```python
def emit_model_metrics(
    model_load_ms: int, inference_ms: int, cold_start: bool, total_ms: int
) -> None:
    """The metrics that describe the model rather than the file.

    Shared by stored files and query mode, because both run the model. The two cold
    metrics are emitted only on a cold start, so the sample count of
    ModelLoadDuration over Invocations is the cold-start ratio on the dashboard.
    "Cold" is the same flag stored as coldStart in DynamoDB; if the first call on a
    container fails before the model loads, the load on the next call is not counted.
    That undercounts and never overcounts.
    """
    emit("InferenceDuration", inference_ms, "Milliseconds")
    if cold_start:
        emit("ModelLoadDuration", model_load_ms, "Milliseconds")
        emit("ColdStartDuration", total_ms, "Milliseconds")


def load_and_tag(path: Path, correlation_id: str) -> tuple[dict[str, int], int, int]:
```

(c) The model-load span.

old:
```python
    started = time.time()
    detector, species_model, classes, label_map = get_models(s3, artefact_bucket(), version)
    model_load_ms = int((time.time() - started) * 1000)
```
new:
```python
    started = time.time()
    with span("model-load"):
        detector, species_model, classes, label_map = get_models(s3, artefact_bucket(), version)
    model_load_ms = int((time.time() - started) * 1000)
```

(d) The tagging span. One span covers the detector and the species model because `tagger.tag_image` runs both in one call and exposes no boundary between them.

old:
```python
    tags = tag_image(str(path), detector, species_model, classes, label_map)
```
new:
```python
    with span("tagging"):
        tags = tag_image(str(path), detector, species_model, classes, label_map)
```

(e) The thumbnail span. The upload that follows is already its own S3 subsegment.

old:
```python
            make_thumbnail(local_path, thumb_path)
```
new:
```python
            with span("thumbnail"):
                make_thumbnail(local_path, thumb_path)
```

(f) Compute the processing time once, ahead of `record_success`.

old:
```python
        record_success(
            file_id,
            correlation_id,
            type="image",
```
new:
```python
        processing_ms = int((time.time() - started) * 1000)
        record_success(
            file_id,
            correlation_id,
            type="image",
```

(g) Use it in the record.

old:
```python
            processingMs=Decimal(int((time.time() - started) * 1000)),
```
new:
```python
            processingMs=Decimal(processing_ms),
```

(h) Emit after the record is written, before the publish.

old:
```python
            coldStart=cold_start,
        )
        # After the record is written, not before: the tags are the product and the
```
new:
```python
            coldStart=cold_start,
        )
        # Metrics follow the record and precede the publish, so neither can be lost to
        # the other failing. TagsPerFile is emitted for an empty result too: a file
        # with no animals is still a file that was processed.
        emit_model_metrics(model_load_ms, inference_ms, cold_start, processing_ms)
        emit("TagsPerFile", sum(tags.values()), "Count")
        # After the record is written, not before: the tags are the product and the
```

(i) Time the whole handler call, for the cold query path.

old:
```python
    cold_start = _COLD_START
    _COLD_START = False
```
new:
```python
    handler_started = time.time()
    cold_start = _COLD_START
    _COLD_START = False
```

(j) Query mode: metrics and the countable log line.

old:
```python
        finally:
            query_path.unlink(missing_ok=True)
        return {
            "tags": tags,
```
new:
```python
        finally:
            query_path.unlink(missing_ok=True)
        emit_model_metrics(
            model_load_ms, inference_ms, cold_start, int((time.time() - handler_started) * 1000)
        )
        # Counted by a Logs Insights widget (ADR 5 wants to know how often this route
        # is hit cold). coldStart is an integer here because Insights can sum one; a
        # JSON boolean is not guaranteed to be summable.
        log_event(
            "query tagged",
            correlation_id,
            coldStart=int(cold_start),
            modelLoadMs=model_load_ms,
            inferenceMs=inference_ms,
        )
        return {
            "tags": tags,
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest services/process/tests/test_app_observability.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Run the whole suite and the linter**

Run: `.venv/bin/pytest -m "not integration" -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: all pass (previously 145, now 161 with Tasks 1 to 3), ruff clean. If `ruff check` reports import order in `app.py`, run `.venv/bin/ruff check --fix .`.

- [ ] **Step 6: Prove the packaging test catches the missing `COPY` lines**

Run: `.venv/bin/pytest services/process/tests/test_packaging.py -q`
Expected: FAIL with `imported by app.py but not copied by the Dockerfile: ['metrics', 'tracing']`.

- [ ] **Step 7: Edit `services/process/Dockerfile`**

(a) Add the two `COPY` lines after the `notify.py` line.

old:
```
COPY services/process/notify.py       ${LAMBDA_TASK_ROOT}/
```
new:
```
COPY services/process/notify.py       ${LAMBDA_TASK_ROOT}/
COPY services/process/metrics.py      ${LAMBDA_TASK_ROOT}/
COPY services/process/tracing.py      ${LAMBDA_TASK_ROOT}/
```

(b) Install the SDK as a new step 7, after step 6 and before the verification block. Insert this immediately before the line `# --- Verification ---...`:

```
# --- Step 7: X-Ray SDK -------------------------------------------------------
#
# Not in the Lambda runtime, and only this image needs it: the API functions are
# traced without the SDK (ADR 12). It depends on botocore and wrapt, and wrapt
# ships a wheel, so --only-binary costs nothing and turns a surprise compile into
# an immediate error. The protobuf pin from step 4 should not move; the
# verification below asserts it, because that pin was won against a conflict and
# any later install could undo it.
RUN pip install --no-cache-dir --only-binary=:all: "aws-xray-sdk==2.15.0"

```

(c) Extend the verification.

old:
```
import torch, onnx, onnx2torch, cv2, numpy, google.protobuf as pb; \
assert not torch.cuda.is_available(), 'CUDA build leaked in'; \
```
new:
```
import torch, onnx, onnx2torch, cv2, numpy, google.protobuf as pb; \
from aws_xray_sdk.core import patch, xray_recorder; \
assert not torch.cuda.is_available(), 'CUDA build leaked in'; \
assert pb.__version__ == '3.20.3', 'protobuf pin moved: ' + pb.__version__; \
```

- [ ] **Step 8: Run the packaging test**

Run: `.venv/bin/pytest services/process/tests/test_packaging.py -q`
Expected: `1 passed`.

- [ ] **Step 9: Smoke-test `tracing.py` against the real SDK in a throwaway environment**

The unit tests use a fake recorder. This is the only check, before the image build, that the real `configure(context_missing=...)` and `patch(["botocore"])` calls are valid and that a span outside Lambda does not raise.

```bash
SCRATCH=<any directory outside the repository>
python3 -m venv "$SCRATCH/xray-venv"
"$SCRATCH/xray-venv/bin/pip" install -q "aws-xray-sdk==2.15.0"
cd services/process && "$SCRATCH/xray-venv/bin/python" -I -c "
import sys; sys.path.insert(0, '.')
import tracing
assert tracing.xray_recorder is not None, 'SDK not imported'
with tracing.span('smoke'):
    print('span ran outside a segment without raising')
"; cd ../..
```
Expected: prints `span ran outside a segment without raising`. If `patch(["botocore"])` raises, the error names the supported libraries; use the name it gives and note it in the ledger as a ruling. If the span raises `SegmentNotFoundException`, `context_missing` was not applied: fix `tracing.py` and add a test for it.

- [ ] **Step 10: Commit**

```bash
git add services/process/app.py services/process/tests/test_app_observability.py services/process/Dockerfile
git commit -m "feat(observe): emit metrics and spans from the tagger, and ship them in the image

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```

---

### Task 4: Active tracing on the five functions

**Files:**
- Modify: `infra/terraform/modules/compute/main.tf`
- Modify: `infra/terraform/modules/api_functions/main.tf`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing other modules read. Removes the `CKV_AWS_50` skip lines.

- [ ] **Step 1: Install checkov in a throwaway environment and take a baseline**

```bash
SCRATCH=<any directory outside the repository>
python3 -m venv "$SCRATCH/checkov-venv"
"$SCRATCH/checkov-venv/bin/pip" install -q checkov==3.3.26
terraform -chdir=infra/terraform/envs/dev init -backend=false -input=false
"$SCRATCH/checkov-venv/bin/checkov" -d infra/terraform --framework terraform --quiet --compact
```
Expected: exits 0 with no failed checks (this is the CI command). If the baseline already fails, stop and report: the branch started from a green `main`.

- [ ] **Step 2: Turn on tracing for `process`**

In `infra/terraform/modules/compute/main.tf`:

(a) Delete the line
```
  #checkov:skip=CKV_AWS_50:Lambda X-Ray tracing is enabled in Phase 9; correlation ids in the JSON logs cover diagnosis until then
```
(b) In `resource "aws_lambda_function" "process"`, add after the `ephemeral_storage { ... }` block:
```hcl
  # Active tracing starts a segment for every invocation, so the SDK's subsegments
  # (model-load, tagging, thumbnail) and the patched AWS calls have a parent. Free
  # up to 100,000 traces a month.
  tracing_config {
    mode = "Active"
  }
```
(c) In the role policy `Statement` list, add this statement after `WriteOwnLogs`:
```hcl
      {
        # Both actions only accept "*": X-Ray has no resource-level permissions for
        # writing trace data, so this is as narrow as the policy can be.
        Sid      = "WriteTraces"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = ["*"]
      },
```

- [ ] **Step 3: Turn on tracing for the four API functions**

In `infra/terraform/modules/api_functions/main.tf`:

(a) Delete the same `#checkov:skip=CKV_AWS_50:...` line from `aws_lambda_function.function`.
(b) In that resource, add after the `environment { ... }` block:
```hcl
  # A segment per call with its duration and errors. There is no SDK in the zip, so
  # no per-call subsegments here (ADR 12); the access log and the correlation id
  # cover the rest.
  tracing_config {
    mode = "Active"
  }
```
(c) In `aws_iam_role_policy.function`, add a second statement to the `concat(each.value.statements, [ ... ])` list, after `WriteOwnLogs`:
```hcl
      {
        # "*" is the only resource X-Ray accepts for these two actions.
        Sid      = "WriteTraces"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = ["*"]
      },
```

- [ ] **Step 4: Format and validate**

Run:
```bash
terraform fmt -recursive infra/terraform
terraform -chdir=infra/terraform/envs/dev validate
```
Expected: `Success! The configuration is valid.` `fmt` prints only files it changed; `git diff` should show only the edits above.

- [ ] **Step 5: Run checkov**

Run: `"$SCRATCH/checkov-venv/bin/checkov" -d infra/terraform --framework terraform --quiet --compact`
Expected: no failures. If it reports a wildcard-resource finding on the new statement (IAM policy checks such as `CKV_AWS_290`, `CKV_AWS_355` or `CKV_AWS_289`), add on the `aws_iam_role_policy` resource, one line per ID reported:
`#checkov:skip=<ID>:The X-Ray write actions do not support resource-level permissions, so "*" is the narrowest possible resource`
Re-run until clean.

- [ ] **Step 6: Commit**

```bash
git add infra/terraform
git commit -m "feat(observe): turn on active X-Ray tracing for every function

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```

---

### Task 5: API Gateway access logs and stage tracing

**Files:**
- Create: `infra/terraform/modules/apigw_logging/main.tf`
- Create: `infra/terraform/modules/apigw_logging/variables.tf`
- Modify: `infra/terraform/modules/api/main.tf`
- Modify: `infra/terraform/modules/api/variables.tf`
- Modify: `infra/terraform/modules/api/outputs.tf`
- Modify: `infra/terraform/envs/dev/main.tf`

**Interfaces:**
- Consumes: nothing.
- Produces: `module.api.rest_api_name` (string, the REST API's name) for Task 6. The stage writes access logs to `/aws/apigateway/<name_prefix>`.

- [ ] **Step 1: Create the logging module**

`infra/terraform/modules/apigw_logging/variables.tf`:
```hcl
variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}
```

`infra/terraform/modules/apigw_logging/main.tf`:
```hcl
# API Gateway writes its logs with a role that is set once for the whole account and
# region, not once per API. That makes this a resource that does not belong to any
# one API's module, and it has a consequence worth knowing: with the AWS provider
# 6.x, destroying it resets the account's setting to "no role", so a destroy of this
# estate would switch API Gateway logging off for any other API in the account.
# Nothing else is in this account (the provider pins allowed_account_ids), which is
# why it lives here rather than outside Terraform. See ADR 12.

resource "aws_iam_role" "apigw_cloudwatch" {
  name = "${var.name_prefix}-apigw-cloudwatch"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The AWS-managed policy for exactly this purpose: it allows writing to log groups.
resource "aws_iam_role_policy_attachment" "apigw_cloudwatch" {
  role       = aws_iam_role.apigw_cloudwatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "this" {
  cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch.arn

  depends_on = [aws_iam_role_policy_attachment.apigw_cloudwatch]
}
```

- [ ] **Step 2: Add the access log group, access log settings and tracing to the `api` module**

In `infra/terraform/modules/api/variables.tf`, append:
```hcl
variable "log_retention_days" {
  description = "Retention of the access log group. The CloudWatch default is forever."
  type        = number
  default     = 14
}
```

In `infra/terraform/modules/api/main.tf`:

(a) Add this resource immediately before `resource "aws_api_gateway_stage" "stage"`:
```hcl
# --- Access log ---------------------------------------------------------------
#
# One JSON line per request. requestId is the same id the functions log as
# correlationId (common.correlation_id), so an access-log line ties straight to the
# function's own lines. The caller's email and IP are deliberately not logged: this
# repository is public and these logs would hold user data.
resource "aws_cloudwatch_log_group" "access" {
  #checkov:skip=CKV_AWS_158:Logs are encrypted at rest with the default AWS-managed key; a customer-managed key costs $1 a month and adds nothing for access logs that hold no secrets
  #checkov:skip=CKV_AWS_338:Retention is 14 days by design, to bound log storage cost (design document, observability)
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = var.log_retention_days
}

```

(b) In `resource "aws_api_gateway_stage" "stage"`, delete these three skip lines:
```
  #checkov:skip=CKV_AWS_73:API Gateway X-Ray tracing is enabled in Phase 9
  #checkov:skip=CKV_AWS_76:Access logging is configured in Phase 9 together with tracing and the dashboard
```
and `#checkov:skip=CKV2_AWS_4:...` is handled in Step 6 below, so leave it for now.

(c) Replace the trailing comment block inside the stage resource, which starts `# Access logging and X-Ray tracing are configured in Phase 9, together with the` and ends with `# would silently disable logging for anything else using it.`, with:
```hcl
  xray_tracing_enabled = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      extendedRequestId  = "$context.extendedRequestId"
      httpMethod         = "$context.httpMethod"
      resourcePath       = "$context.resourcePath"
      status             = "$context.status"
      responseLatency    = "$context.responseLatency"
      integrationLatency = "$context.integrationLatency"
      integrationStatus  = "$context.integrationStatus"
      errorMessage       = "$context.error.message"
    })
  }
```

- [ ] **Step 3: Export the API name**

Append to `infra/terraform/modules/api/outputs.tf`:
```hcl
output "rest_api_name" {
  description = "The ApiName dimension of the AWS/ApiGateway metrics, used by the dashboard."
  value       = aws_api_gateway_rest_api.api.name
}
```
Also in the same file, change the description of `rest_api_id` from `Needed to redeploy a stage by hand, and by Phase 9's alarms.` to `Needed to redeploy a stage by hand.`

- [ ] **Step 4: Wire it in the dev environment**

In `infra/terraform/envs/dev/main.tf`, add before `module "api"`:
```hcl
# The account-level role API Gateway needs before a stage may write access logs.
module "apigw_logging" {
  source = "../../modules/apigw_logging"

  name_prefix = local.name_prefix
}
```
and inside `module "api" { ... }` add, after `allowed_origin = var.allowed_origin`:
```hcl

  # A stage cannot enable access logging until the account has a role, and the error
  # when it has not does not say so. On destroy the order reverses: the stage goes
  # first, then the account setting is reset.
  depends_on = [module.apigw_logging]
```

- [ ] **Step 5: Format and validate**

Run:
```bash
terraform fmt -recursive infra/terraform
terraform -chdir=infra/terraform/envs/dev init -backend=false -input=false
terraform -chdir=infra/terraform/envs/dev validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 6: Run checkov and settle `CKV2_AWS_4`**

Run: `"$SCRATCH/checkov-venv/bin/checkov" -d infra/terraform --framework terraform --quiet --compact`

- If it **passes with the `CKV2_AWS_4` skip still present**, the skip may be unnecessary: delete the `#checkov:skip=CKV2_AWS_4:...` line, re-run, and keep it deleted if the run is clean.
- If it **reports `CKV2_AWS_4`** after deletion (expected: it wants a `logging_level` in method settings, which means execution logging), restore the line with a new reason:
`#checkov:skip=CKV2_AWS_4:Access logging is on (one JSON line per request). Execution logging is not: it writes verbose per-request detail to a second log group that API Gateway creates with no retention unless declared, and the access log plus the function logs already answer what happened`
- Any other new finding: fix the cause, or skip it with a real reason.

Record which case happened in the ledger.

- [ ] **Step 7: Commit**

```bash
git add infra/terraform
git commit -m "feat(observe): log every API request and trace the stage

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```

---

### Task 6: The `observability` module, and wiring

**Files:**
- Create: `infra/terraform/modules/observability/variables.tf`
- Create: `infra/terraform/modules/observability/main.tf`
- Create: `infra/terraform/modules/observability/dashboard.tf`
- Create: `infra/terraform/modules/observability/outputs.tf`
- Modify: `infra/terraform/modules/queue/outputs.tf`
- Modify: `infra/terraform/modules/queue/variables.tf`
- Modify: `infra/terraform/envs/dev/main.tf`
- Modify: `infra/terraform/envs/dev/variables.tf`
- Modify: `infra/terraform/envs/dev/outputs.tf`
- Create: `infra/terraform/envs/dev/terraform.tfvars.example`

**Interfaces:**
- Consumes: `module.api.rest_api_name` (Task 5); `module.queue.queue_name`; `module.compute.function_name` and `module.compute.log_group`; `module.api_functions.function_names` (a map keyed `status`, `upload`, `search`, `subscribe`).
- Produces: `module.observability.alerts_topic_arn`, consumed by `module.queue` as `alarm_topic_arns`; `module.queue.dlq_name`.

- [ ] **Step 1: Queue outputs and the alarm-topic description**

Append to `infra/terraform/modules/queue/outputs.tf`:
```hcl

output "dlq_name" {
  description = "Dead-letter queue name, the QueueName dimension of its metrics."
  value       = aws_sqs_queue.dlq.name
}
```
In `infra/terraform/modules/queue/variables.tf`, change the description of `alarm_topic_arns` to:
`"SNS topics notified when the dead-letter queue is not empty. The dev environment passes the alerts topic from the observability module."`

- [ ] **Step 2: Module variables**

`infra/terraform/modules/observability/variables.tf`:
```hcl
variable "name_prefix" {
  description = "Resource name prefix, e.g. wildlens-dev."
  type        = string
}

variable "alert_email" {
  description = <<-EOT
    Address that receives alarm mail. Empty creates the topic and the alarms but no
    subscription, which means an alarm fires and nobody is told. The real value
    belongs in an untracked terraform.tfvars: this repository is public.
  EOT
  type        = string
  default     = ""
}

variable "function_names" {
  description = "Function names keyed by short name (process, status, upload, search, subscribe). One errors alarm each."
  type        = map(string)
}

variable "queue_name" {
  description = "The ingest queue's name (QueueName dimension)."
  type        = string
}

variable "dlq_name" {
  description = "The dead-letter queue's name."
  type        = string
}

variable "process_log_group" {
  description = "Log group of the tagging function, queried by the dashboard's Logs Insights widget."
  type        = string
}

variable "api_name" {
  description = "REST API name (ApiName dimension of AWS/ApiGateway)."
  type        = string
}

variable "api_stage" {
  description = "REST API stage name (Stage dimension of AWS/ApiGateway)."
  type        = string
}

variable "queue_age_threshold_seconds" {
  description = <<-EOT
    Age of the oldest message in the ingest queue that raises the alarm. 1200, not
    900: a message being processed still counts toward its age and process may run
    for its full 900 s timeout, so 900 would fire on a file that merely used all of
    it. 1200 means a file has waited, or been retried, past any legitimate run.
  EOT
  type        = number
  default     = 1200
}
```

- [ ] **Step 3: Topic, subscription and alarms**

`infra/terraform/modules/observability/main.tf`:
```hcl
data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
}

# --- Where alarms go ---------------------------------------------------------
#
# Separate from the tags topic: a topic that users subscribe to must never carry
# operator alerts, and the reverse.
resource "aws_sns_topic" "alerts" {
  #checkov:skip=CKV_AWS_26:Deliberately unencrypted. A CloudWatch alarm cannot publish to a topic encrypted with the AWS-managed key alias/aws/sns, because that key's policy cannot be changed to allow CloudWatch, and the failure is silent: the alarm fires and no mail arrives. A customer-managed key would cost $1 a month. An alert holds a resource name, a metric and a threshold, nothing a user submitted
  name = "${var.name_prefix}-alerts"
}

# Created only when an address is given. SNS mails a confirmation link on the first
# apply and delivers nothing until it is clicked.
resource "aws_sns_topic_subscription" "email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Alarms ------------------------------------------------------------------
#
# All use treat_missing_data = notBreaching, as the dead-letter queue alarm does: an
# idle estate reports no data, and an alarm stuck in INSUFFICIENT_DATA trains
# everyone to ignore it. None sends an OK notification: the runbook says to look at
# the dashboard, and a second mail per incident teaches the owner to ignore both.

resource "aws_cloudwatch_metric_alarm" "function_errors" {
  for_each = var.function_names

  alarm_name        = "${var.name_prefix}-${each.key}-errors"
  alarm_description = "More than 3 errors from ${each.value} in 5 minutes. See docs/runbook.md, An alarm fired."

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  dimensions  = { FunctionName = each.value }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Replaces the design document's "more than 10 records stuck PENDING". CloudWatch has
# no per-status count for DynamoDB, a PENDING record expires through its own
# one-hour TTL, and abandoned uploads would make that alarm noisy. A file waiting in
# the queue is the failure that matters, and its age is a native metric.
#
# Blind spot: if the S3 to SQS notification itself breaks, the queue is empty and
# its age is zero, so this stays quiet. The notification is Terraform-managed, so a
# plan shows the drift.
resource "aws_cloudwatch_metric_alarm" "queue_oldest_message" {
  alarm_name        = "${var.name_prefix}-queue-oldest-message"
  alarm_description = "A file has waited in the ingest queue longer than ${var.queue_age_threshold_seconds} seconds. See docs/runbook.md, An alarm fired."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateAgeOfOldestMessage"
  dimensions  = { QueueName = var.queue_name }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.queue_age_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

- [ ] **Step 4: The dashboard**

`infra/terraform/modules/observability/dashboard.tf`:
```hcl
# One board. The grid is 24 columns wide; every widget here is half a row or a full
# row. The custom metrics have no dimensions (services/process/metrics.py), so their
# metric rows carry only a namespace and a name.

locals {
  process = var.function_names["process"]

  widgets = [
    {
      type = "metric", x = 0, y = 0, width = 12, height = 6
      properties = {
        title   = "Files tagged per hour"
        region  = local.region
        view    = "timeSeries"
        period  = 3600
        stat    = "SampleCount"
        metrics = [["WildLens/Process", "TagsPerFile"]]
      }
    },
    {
      type = "metric", x = 12, y = 0, width = 12, height = 6
      properties = {
        title  = "Queue depth and dead-letter queue depth"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Maximum"
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name, { label = "ingest" }],
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name, { label = "dead-letter" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 6, width = 12, height = 6
      properties = {
        title  = "Age of the oldest queued message (s)"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Maximum"
        metrics = [
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.queue_name],
        ]
        annotations = {
          horizontal = [{ label = "alarm", value = var.queue_age_threshold_seconds }]
        }
      }
    },
    {
      type = "metric", x = 12, y = 6, width = 12, height = 6
      properties = {
        title  = "Tagging function error rate (%)"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Sum"
        metrics = [
          [{ expression = "100 * errors / invocations", label = "error rate", id = "rate" }],
          ["AWS/Lambda", "Errors", "FunctionName", local.process, { id = "errors", visible = false }],
          ["AWS/Lambda", "Invocations", "FunctionName", local.process, { id = "invocations", visible = false }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 12, width = 12, height = 6
      properties = {
        title  = "Tagging function duration (ms)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["AWS/Lambda", "Duration", "FunctionName", local.process, { stat = "p50", label = "p50" }],
          ["AWS/Lambda", "Duration", "FunctionName", local.process, { stat = "p95", label = "p95" }],
          ["AWS/Lambda", "Duration", "FunctionName", local.process, { stat = "p99", label = "p99" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 12, width = 12, height = 6
      properties = {
        title  = "Cold-start ratio (% of invocations that loaded the model)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          [{ expression = "100 * FILL(loads, 0) / invocations", label = "cold", id = "ratio" }],
          ["WildLens/Process", "ModelLoadDuration", { id = "loads", stat = "SampleCount", visible = false }],
          ["AWS/Lambda", "Invocations", "FunctionName", local.process, { id = "invocations", stat = "Sum", visible = false }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 18, width = 12, height = 6
      properties = {
        title  = "Model load and inference time (ms)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["WildLens/Process", "ModelLoadDuration", { stat = "Average", label = "model load, average" }],
          ["WildLens/Process", "ModelLoadDuration", { stat = "Maximum", label = "model load, max" }],
          ["WildLens/Process", "InferenceDuration", { stat = "Average", label = "inference, average" }],
          ["WildLens/Process", "InferenceDuration", { stat = "Maximum", label = "inference, max" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 18, width = 12, height = 6
      properties = {
        title  = "API requests, errors and latency"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["AWS/ApiGateway", "Count", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "Sum", label = "requests" }],
          ["AWS/ApiGateway", "4XXError", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "Sum", label = "4xx" }],
          ["AWS/ApiGateway", "5XXError", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "Sum", label = "5xx" }],
          ["AWS/ApiGateway", "Latency", "ApiName", var.api_name, "Stage", var.api_stage, { stat = "p95", label = "latency p95 (ms)", yAxis = "right" }],
        ]
      }
    },
    {
      type = "log", x = 0, y = 24, width = 24, height = 6
      properties = {
        title  = "/search/byfile calls per hour, and how many were cold (ADR 5)"
        region = local.region
        view   = "table"
        query  = "SOURCE '${var.process_log_group}' | filter msg = 'query tagged' | stats count(*) as calls, sum(coldStart) as cold by bin(1h) | sort bin(1h) desc"
      }
    },
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({ widgets = local.widgets })
}
```

- [ ] **Step 5: Module outputs**

`infra/terraform/modules/observability/outputs.tf`:
```hcl
output "alerts_topic_arn" {
  description = "Every alarm acts on this topic. The queue module's dead-letter alarm is wired to it too."
  value       = aws_sns_topic.alerts.arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard."
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}
```

- [ ] **Step 6: Wire the dev environment**

In `infra/terraform/envs/dev/main.tf`:

(a) Change `module "queue"` to pass the topic:
```hcl
module "queue" {
  source = "../../modules/queue"

  name_prefix     = local.name_prefix
  raw_bucket_name = module.storage.bucket_names["raw"]
  raw_bucket_arn  = module.storage.bucket_arns["raw"]

  # The dead-letter alarm already existed; this is what finally tells someone.
  alarm_topic_arns = [module.observability.alerts_topic_arn]
}
```
(b) Add after `module "api"`:
```hcl
module "observability" {
  source = "../../modules/observability"

  name_prefix = local.name_prefix
  alert_email = var.alert_email

  function_names    = merge(module.api_functions.function_names, { process = module.compute.function_name })
  queue_name        = module.queue.queue_name
  dlq_name          = module.queue.dlq_name
  process_log_group = module.compute.log_group
  api_name          = module.api.rest_api_name
  api_stage         = var.api_stage
}
```

Append to `infra/terraform/envs/dev/variables.tf`:
```hcl

variable "alert_email" {
  description = <<-EOT
    Address that receives alarm mail. Empty means the alarms exist and nobody is told.
    Set it in an untracked terraform.tfvars (see terraform.tfvars.example): this
    repository is public.
  EOT
  type        = string
  default     = ""
}
```
Append to `infra/terraform/envs/dev/outputs.tf`:
```hcl

output "alerts_topic_arn" {
  description = "Alarm notifications go here."
  value       = module.observability.alerts_topic_arn
}

output "dashboard_name" {
  description = "Open it in the CloudWatch console under Dashboards."
  value       = module.observability.dashboard_name
}
```
Create `infra/terraform/envs/dev/terraform.tfvars.example`:
```hcl
# Copy to terraform.tfvars (which git ignores) and set a real address. SNS sends a
# confirmation mail on the first apply; no alarm mail arrives until it is confirmed.
alert_email = "you@example.com"
```

- [ ] **Step 7: Format, validate and check**

Run:
```bash
terraform fmt -recursive infra/terraform
terraform -chdir=infra/terraform/envs/dev validate
"$SCRATCH/checkov-venv/bin/checkov" -d infra/terraform --framework terraform --quiet --compact
```
Expected: valid; checkov clean. `terraform fmt` may realign the one-line `type = "metric", x = 0, ...` object heads; accept its output. If `validate` reports `for_each ... values derived from resource attributes that cannot be determined until apply`, change the alarm resource to `for_each = toset(keys(var.function_names))` and use `var.function_names[each.key]` in place of `each.value`; keys are known at plan time. If checkov reports a finding on a new resource, fix the cause or add `#checkov:skip=<ID>:<reason>` with a real reason.

- [ ] **Step 8: Commit**

```bash
git add infra/terraform
git commit -m "feat(observe): add the alerts topic, alarms and dashboard

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```

---

### Task 7: Documentation

**Files:**
- Create: `docs/adr/0012-observe-with-a-separate-alert-topic-emf-metrics-and-xray-in-the-tagger.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/runbook.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-10-29-observability-design.md`

**Interfaces:**
- Consumes: everything above (the names, thresholds and widget titles).
- Produces: nothing code reads. The English-only hook checks every file here.

- [ ] **Step 1: Write ADR 12**

Create `docs/adr/0012-observe-with-a-separate-alert-topic-emf-metrics-and-xray-in-the-tagger.md`:

```markdown
# 12. Observe with a separate alert topic, EMF metrics and X-Ray in the tagger

**Status:** accepted · Phase 9 · 2026-10-29

## Context

Until now the only signal was one alarm that changed colour in a console nobody was
watching, structured logs, and the correlation id. A failure was found by someone
looking. The design document asked for X-Ray, four custom metrics, a dashboard,
alarms to email and 14-day log retention, and six `checkov:skip` lines promised the
rest "in Phase 9".

## Decision

**Alarms go to their own SNS topic**, `wildlens-dev-alerts`, with an email
subscription created from the variable `alert_email`. The address lives in an
untracked `terraform.tfvars` because the repository is public. The topic is **not
encrypted**: a CloudWatch alarm cannot publish to a topic encrypted with the
AWS-managed key `alias/aws/sns`, the failure is silent, and a customer-managed key
costs $1 a month for an alert that carries no user data.

**Alarms:** the existing dead-letter alarm (now wired to the topic), more than 3
errors in 5 minutes for each of the five functions, and the age of the oldest message
in the ingest queue above 1200 seconds.

**Metrics through EMF**, namespace `WildLens/Process`, with no dimensions:
`ModelLoadDuration` and `ColdStartDuration` (cold starts only), `InferenceDuration`
and `TagsPerFile`. A line on stdout becomes a metric, so there is no API call and no
permission, and with no dimensions the cost is four metrics.

**X-Ray: active tracing on all five functions and the API stage; the SDK only in
`process`.** The SDK is not in the Python 3.11 runtime, the API artefact is one zip
with no third-party packages, and every subsegment the design asked for is in
`process`: `model-load`, `tagging`, `thumbnail`, plus the DynamoDB, S3 and SNS calls
through the botocore patch.

**API Gateway access logs** as one JSON line per request, with the request id and no
caller email or IP. The account-level role they need is created in this
environment, by its own small module.

| Considered | Why not |
|---|---|
| Alarm on "records stuck PENDING" | CloudWatch has no per-status count for DynamoDB. A PENDING record expires through its own one-hour TTL, and abandoned uploads would make it noisy. A Lambda that scans for them was the alternative |
| Custom metrics through `PutMetricData` | A permission to grant and a call on the request path, for the same four metrics |
| Percentiles and the cold-start ratio from Logs Insights alone | No curve over time, nothing an alarm can act on |
| The X-Ray SDK in the API functions | Would put a third-party package in a zip that has none, for per-call subsegments in functions that already log a correlation id |
| Splitting `tagging` into MegaDetector and SpeciesNet spans | `ml/tagger.py` runs both in one call and exposes no boundary. Splitting means changing `ml/`, which the evaluation script depends on |
| Creating the API Gateway logging role by hand | Infrastructure that is not in code |

## Consequences

**The queue-age alarm has a blind spot.** If the S3 to SQS notification itself
breaks, the queue is empty, the age is zero and nothing fires. The notification is
managed by Terraform, so a `plan` shows the drift. The 1200 s threshold exceeds the
900 s function timeout on purpose, because a message being processed still counts
toward its age.

**Destroying this estate switches off API Gateway logging for the account.** With
the AWS provider 6.x, destroying `aws_api_gateway_account` resets the account's
CloudWatch role. Nothing else is in this account today. If that changes, move the
role out of this environment.

**"Cold" is the existing `cold_start` flag.** If the first call on a container fails
before the model loads, the load on the next call is not counted. The ratio
undercounts and never overcounts.

**Metrics add about $1.20 a month.** X-Ray stays inside its free tier at this
volume, and the dashboard is one of the three CloudWatch does not bill.

**Alarm mail depends on someone confirming the subscription.** With `alert_email`
unset the alarms exist and nobody is told. The runbook says so.
```

- [ ] **Step 2: Add the row to the ADR index**

In `docs/adr/README.md`, add after the row for ADR 11:
```
| [12](0012-observe-with-a-separate-alert-topic-emf-metrics-and-xray-in-the-tagger.md) | Observe with a separate alert topic, EMF metrics and X-Ray in the tagger | 9 |
```

- [ ] **Step 3: Replace the placeholder note and the two `(Phase 9)` sections in the runbook**

(a) In `docs/runbook.md`, delete the paragraph
```
Sections marked **(Phase 9)** are placeholders for procedures that need the
dashboard and tracing to exist. Everything else is usable now.
```
together with the blank line after it.

(b) Replace everything from the heading `## (Phase 9) Tracing one request end to end` through the line `Needs the dashboard.` (the end of `## (Phase 9) The dashboard`) with:

````markdown
## Tracing one request end to end

Three views of the same request, from the outside in. The id that ties them together
is the API Gateway request id; for a queued file it is the SQS message id.

**1. The access log**, one JSON line per API request:

```bash
aws logs filter-log-events --log-group-name /aws/apigateway/wildlens-dev \
  --filter-pattern '"<requestId>"' --query 'events[].message' --output text
```

It shows the status, the total latency and the time spent in the function
(`integrationLatency`). A large gap between the two is API Gateway or the authoriser,
not your code.

**2. The function's own logs.** Every line carries `correlationId`:

```bash
aws logs filter-log-events --log-group-name /aws/lambda/wildlens-dev-process \
  --filter-pattern '"<correlationId>"' --query 'events[].message' --output text
```

**3. The X-Ray trace**, which answers "where did those forty seconds go". In the
console: CloudWatch, then X-Ray traces, then filter by the function. From the shell:

```bash
aws xray get-trace-summaries \
  --start-time $(( $(date +%s) - 3600 )) --end-time $(date +%s) \
  --filter-expression 'service("wildlens-dev-process")' \
  --query 'TraceSummaries[].[Id,Duration,HasError]' --output table
aws xray batch-get-traces --trace-ids <id>
```

For `process`, read the subsegments in order: `model-load` (present only on a cold
start, and usually 35 to 45 seconds), `tagging` (the detector and the species model
together, a few seconds), `thumbnail`, and the DynamoDB, S3 and SNS calls the botocore
patch records on their own. The four API functions have a single segment per call and
no subsegments, by design (ADR 12).

## The dashboard

CloudWatch, then Dashboards, then `wildlens-dev`.

| Widget | What a bad reading means |
|---|---|
| Files tagged per hour | Zero while files are being uploaded: the queue is not draining, see the age widget |
| Queue depth and dead-letter queue depth | Dead-letter above zero: see "The DLQ alarm fired" |
| Age of the oldest queued message | Above the dotted line (1200 s) is the alarm. See "A file is stuck at PENDING" |
| Tagging function error rate | Sustained above zero: read the logs for the correlation id |
| Tagging function duration | p99 near 900 s is a file about to hit the timeout. A p50 that doubles after a deploy is a regression |
| Cold-start ratio | Near 100 percent means containers are not being reused. Expected right after a deploy |
| Model load and inference time | Model load far above 40 s is S3 or the image pull. Inference far above its usual few seconds is memory or CPU |
| API requests, errors and latency | 5xx is a function failing; 4xx is callers. Latency p95 near 29 s is `/search/byfile` hitting the API Gateway ceiling (ADR 5) |
| `/search/byfile` calls per hour | `cold` is how many of those calls paid for a model load. This is the number ADR 5 said would decide whether the route needs the asynchronous design |

## An alarm fired

Mail from `wildlens-dev-alerts`. If nothing ever arrives, the subscription was never
confirmed or `alert_email` was never set: check
`aws sns list-subscriptions-by-topic --topic-arn <alerts_topic_arn from terraform output>`;
a status of `PendingConfirmation` means the link in the first mail was not clicked.

| Alarm | What it means | First thing to do |
|---|---|---|
| `wildlens-dev-dlq-not-empty` | A file failed three times | "The DLQ alarm fired" |
| `wildlens-dev-queue-oldest-message` | A file has waited over 20 minutes and nothing processed it | Check the event source mapping is enabled and the function is not throttled; then "A file is stuck at PENDING" |
| `wildlens-dev-process-errors` | More than 3 errors from the tagging function in 5 minutes | Dashboard, then the function's logs for the correlation id |
| `wildlens-dev-upload-errors`, `-status-errors`, `-search-errors`, `-subscribe-errors` | More than 3 errors from that API function in 5 minutes | The access log for the failing requests, then that function's logs |

An alarm that fires and clears produces one mail, not two: there is no mail on
recovery. Look at the dashboard to see that it cleared.
````

- [ ] **Step 4: Update `CLAUDE.md`**

(a) In the "Ingest path" bullet, after the sentence `Nothing consumes the DLQ by design; see the runbook.` add:
` Observability (ADR 12): the tagger prints EMF metric lines (\`services/process/metrics.py\`, namespace \`WildLens/Process\`, no dimensions) and opens X-Ray subsegments through \`tracing.py\` (a no-op without the SDK, which only the image has); alarms mail the \`alerts\` topic, whose address comes from the untracked \`terraform.tfvars\`.`

(b) In the "Terraform" bullet, change the module list `(storage, database, queue, registry, compute, api, api_functions, auth, cdn, ci)` to `(storage, database, queue, registry, compute, api, api_functions, auth, cdn, ci, notify, observability, apigw_logging)`.

(c) In the "Flat packaging" bullet, change `(\`app\`, \`common\`, \`search\`, \`tagger\`, …)` to `(\`app\`, \`common\`, \`search\`, \`tagger\`, \`metrics\`, \`tracing\`, …)`.

- [ ] **Step 5: Bring the spec back in line with the plan**

In `docs/superpowers/specs/2026-10-29-observability-design.md`:
- Section 4.2: change `patch(["boto3"])` to `patch(["botocore"])` and add after "their own subsegments with no code of ours": ` (boto3 clients go through botocore, which is the name the SDK accepts).`
- Section 4.2: change "Spans in `app.py`: `model-load` (around `get_models`), `tagging` (around `tag_image`), `thumbnail` (around `make_thumbnail` and the upload)." to "... `thumbnail` (around `make_thumbnail`; the upload is already an S3 subsegment)."
- Section 5.2: add at the end of the paragraph: ` The line carries \`coldStart\` as an integer, because Logs Insights can sum an integer and a JSON boolean is not guaranteed to be summable.`
- Status line: change `Awaiting review` to `Approved; implemented in Phase 9`.

- [ ] **Step 6: Check the language hook and commit**

Run: `git add -A docs CLAUDE.md && git commit -m "docs: record ADR 12, the observability runbook and CLAUDE.md updates

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"`
Expected: the commit succeeds (the pre-commit hook passes). If it rejects a file, it names the character; replace it with plain English and re-run.

---

### Task 8: Deploy and verify (every step waits for the owner)

**Files:** none changed unless a check disagrees with ADR 12 or the runbook; then fix them and commit.

**Interfaces:**
- Consumes: the finished branch.
- Produces: evidence that tracing, metrics, access logs, the dashboard and alarm mail work on AWS; a pushed branch and PR #3.

All commands use `export AWS_PROFILE=wildlens`. Each numbered step that changes anything outside this worktree stops for the owner.

- [ ] **Step 1: Ask for the alert address, then plan**

Ask the owner which address should receive alarm mail, and **do not assume** the address on file. Write it to `infra/terraform/envs/dev/terraform.tfvars` (git ignores it; confirm with `git status` that it is not listed). Run:
```bash
export AWS_PROFILE=wildlens
terraform -chdir=infra/terraform/envs/dev plan
```
Show the owner the full plan. Expected: about 20 to add (topic, subscription, 6 alarms, dashboard, IAM role and attachment, account setting, access log group), about 8 to change in place (five functions for tracing and their role policies, the stage, the DLQ alarm gaining an action) and **0 to destroy**. Anything destroyed, and in particular the API Gateway deployment being replaced, is reported and explained before going on. **STOP: wait for "apply".**

- [ ] **Step 2: Apply**

Run: `terraform -chdir=infra/terraform/envs/dev apply` (after the owner's go-ahead). Expected: success. If the stage update fails with a message about a CloudWatch Logs role, the account setting had not propagated: re-run apply once.

- [ ] **Step 3: Build, push and update the image (STOP: ask first)**

The new code is not in the running function until the image is rebuilt. After the owner says to:
```bash
./scripts/build-and-push.sh
aws lambda update-function-code --function-name wildlens-dev-process \
  --image-uri "$(terraform -chdir=infra/terraform/envs/dev output -raw ecr_repository_url):latest"
aws lambda wait function-updated --function-name wildlens-dev-process
```
Expected: the build's verification step prints `verified: ...` and the protobuf assertion passes; `LastUpdateStatus` is `Successful`. If the build fails at the verification step on the protobuf assert, the SDK moved it: pin `protobuf==3.20.3` again after step 7 and rebuild.

- [ ] **Step 4: The owner confirms the alert subscription**

Ask the owner to open the mail "AWS Notification - Subscription Confirmation" from `wildlens-dev-alerts` and click the link. Check:
```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform -chdir=infra/terraform/envs/dev output -raw alerts_topic_arn)" \
  --query 'Subscriptions[].SubscriptionArn'
```
Expected: a real ARN, not `PendingConfirmation`.

- [ ] **Step 5: Upload a new image and check all four signals**

A test image the table has not seen: copy one and append a unique tail, so its digest is new and it is still a readable JPEG:
```bash
cp "<a test image>" "$SCRATCH/new.jpg" && printf 'wildlens-%s' "$(date +%s)" >> "$SCRATCH/new.jpg"
```
Upload it through `/upload` and the presigned PUT as in the Phase 8 check (`./scripts/get-token.sh` for the token), poll `/files/<sha>` until `DONE`, then:

1. **X-Ray:** `aws xray get-trace-summaries` for the last 10 minutes, then `batch-get-traces` on the newest id. Expected: subsegments named `model-load` (only if the container was cold), `tagging`, `thumbnail`, and calls to DynamoDB and S3.
2. **Metrics:** `aws cloudwatch list-metrics --namespace WildLens/Process`. Expected: `InferenceDuration`, `TagsPerFile`, and after a cold start `ModelLoadDuration` and `ColdStartDuration`, each with an **empty** `Dimensions` list. Metrics can take a few minutes to appear. If they never appear, read one `process` log line starting with `{"_aws"` to see whether the shape is wrong.
3. **Access log:** `aws logs filter-log-events --log-group-name /aws/apigateway/wildlens-dev --limit 5`. Expected: JSON lines with `requestId`, `status` and `responseLatency`, and **no** email or IP field.
4. **Dashboard:** `aws cloudwatch get-dashboard --dashboard-name wildlens-dev` returns a body with 9 widgets; ask the owner to open it in the console and confirm every widget renders (an empty graph is fine; an error box is not).

- [ ] **Step 6: Prove an alarm reaches the owner (STOP: this sends mail)**

This is the check the design hinges on, because the alerts topic is unencrypted for exactly this reason. With the owner's go-ahead:
```bash
aws cloudwatch set-alarm-state --alarm-name wildlens-dev-queue-oldest-message \
  --state-value ALARM --state-reason "Phase 9 manual check"
```
Ask the owner whether the mail arrived within a minute or two, then:
```bash
aws cloudwatch set-alarm-state --alarm-name wildlens-dev-queue-oldest-message \
  --state-value OK --state-reason "Phase 9 manual check done"
```
Expected: one mail on `ALARM`, none on the reset. If no mail arrives, check `aws cloudwatch describe-alarm-history --alarm-name wildlens-dev-queue-oldest-message --history-item-type Action` for a failed action, and do not continue until it works.

- [ ] **Step 7: Record anything that differed, push, and open the PR (STOP: ask first)**

If any check disagreed with ADR 12 or the runbook, fix the document and commit. Then, with the owner's go-ahead:
```bash
git push -u origin observe/phase-9
gh pr create --title "Phase 9: tracing, metrics, alarms and a dashboard" --body "<summary, the rulings, and the checks run in Task 8>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01HYhfgXQ65WB845c4XBM8tH"
```
Watch `gh pr checks`. The `plan` check runs under the read-only CI role: if it fails with an access-denied error on a new read (for example a CloudWatch or X-Ray describe action), report which action, and do not widen the role without asking.

Ask whether to merge the pull request.
