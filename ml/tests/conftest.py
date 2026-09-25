"""Make ml/ importable so tests can `import tagger` without installing it.

The ml directory is not a package and is copied flat into the Lambda image, so
adding it to sys.path here mirrors how the code is actually laid out at runtime.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
