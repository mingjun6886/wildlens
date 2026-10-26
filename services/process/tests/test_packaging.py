"""Every flat module app.py imports must be copied into the image.

The Dockerfile copies files one by one rather than a directory, so a new module
that nothing copies imports fine in every test and then fails on Lambda with an
ImportError at cold start, which takes the whole pipeline down. Phase 8 added
notify.py and nearly shipped exactly that.
"""

import ast
import re
from pathlib import Path

PROCESS = Path(__file__).resolve().parents[1]
ML = PROCESS.parents[1] / "ml"
DOCKERFILE = PROCESS / "Dockerfile"


def local_imports() -> set[str]:
    tree = ast.parse((PROCESS / "app.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return {n for n in names if (PROCESS / f"{n}.py").exists() or (ML / f"{n}.py").exists()}


def test_dockerfile_copies_every_local_module_app_imports():
    copied = set(re.findall(r"^COPY\s+\S+/(\w+)\.py\s", DOCKERFILE.read_text(), re.M))
    missing = local_imports() - copied
    assert not missing, f"imported by app.py but not copied by the Dockerfile: {sorted(missing)}"
