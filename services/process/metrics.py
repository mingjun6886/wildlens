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
