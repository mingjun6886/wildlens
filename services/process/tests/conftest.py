"""Make the flat handler modules importable, mirroring the Lambda task root."""

import os
import sys
from pathlib import Path

HANDLERS = Path(__file__).resolve().parents[1]
ML = Path(__file__).resolve().parents[3] / "ml"

for directory in (HANDLERS, ML):
    sys.path.insert(0, str(directory))

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("THUMB_BUCKET", "test-thumb")
