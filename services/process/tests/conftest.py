"""Make the flat handler modules importable, mirroring the Lambda task root."""

import sys
from pathlib import Path

HANDLERS = Path(__file__).resolve().parents[1]
ML = Path(__file__).resolve().parents[3] / "ml"

for directory in (HANDLERS, ML):
    sys.path.insert(0, str(directory))
