"""Make the flat handler modules importable, mirroring the Lambda task root."""

import os
import sys
from pathlib import Path

import pytest

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


@pytest.fixture
def make_video(tmp_path):
    """Write a small synthetic video and return its path.

    Generated rather than committed, so no binary sample lives in the repository.
    MJPG in an AVI is written by OpenCV itself, without FFmpeg's encoders; mp4v in
    an MP4 goes through FFmpeg. Each frame is a flat grey whose level is its index,
    so a test can tell which frames were kept by reading them back.
    """
    import cv2
    import numpy as np

    def write(name: str, seconds: float, fps: float, size: tuple[int, int] = (64, 48)) -> Path:
        path = tmp_path / name
        fourcc = "MJPG" if path.suffix == ".avi" else "mp4v"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, size)
        assert writer.isOpened(), f"OpenCV could not open a writer for {name}"
        for index in range(int(seconds * fps)):
            writer.write(np.full((size[1], size[0], 3), index % 256, np.uint8))
        writer.release()
        return path

    return write
