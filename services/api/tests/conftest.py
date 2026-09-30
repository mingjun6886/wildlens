"""The handlers are packaged flat, so tests import them flat too.

They also read configuration from the environment at import time, which is what a
Lambda does. Setting it here rather than inside each test means the modules can be
imported at all.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("RAW_BUCKET", "test-raw")
os.environ.setdefault("THUMB_BUCKET", "test-thumb")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:3000")

# botocore builds a signer at client creation and wants credentials to exist, even
# though no test here makes a network call.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
