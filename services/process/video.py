"""Turn a video file into still frames, and per-frame tags into one result.

Pure: OpenCV and the filesystem only, so all of it is tested without models or
AWS. app.py decides what to do with the frames.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi"}

# One frame per second, the first minute. At about 7 s of inference per frame
# warm, 60 frames is about 7 minutes, inside the 900 s timeout with room for a
# 41 s cold start and the thumbnail.
MAX_FRAMES = 60

# Some AVI and MOV files report no frame rate. 30 is the most common camera rate,
# and a wrong guess only changes which second each kept frame comes from.
FALLBACK_FPS = 30.0

# Frames are inputs to the detector, not thumbnails, so quality stays high.
FRAME_JPEG_QUALITY = 95


class UnreadableVideo(Exception):
    """The file is not a video OpenCV can read any frame from."""


@dataclass(frozen=True)
class SampledVideo:
    frames: list[Path]
    duration_sec: float | None
    fps_assumed: bool


def effective_fps(reported: float) -> tuple[float, bool]:
    """The frame rate to sample by, and whether it was assumed."""
    if not math.isfinite(reported) or reported <= 0:
        return FALLBACK_FPS, True
    return reported, False


def sample_frames(path: Path, out_dir: Path, max_frames: int = MAX_FRAMES) -> SampledVideo:
    """Write one JPEG per second of video, at most `max_frames`, in time order.

    Reads sequentially rather than seeking: seeking lands on the wrong frame in
    some AVI and MOV files. grab() advances without converting the frame to an
    image, so the skipped frames cost a decode but not a copy.

    A read that fails part-way through ends sampling and keeps what was read. A
    file that yields no frame at all raises UnreadableVideo.
    """
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise UnreadableVideo("OpenCV could not open the file")

        fps, assumed = effective_fps(capture.get(cv2.CAP_PROP_FPS))
        step = max(1, round(fps))

        # A renamed JPEG reports a frame count of about -9.2e18, so the count is
        # trusted only when it is finite and positive and the rate was reported.
        total = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = (
            round(total / fps, 1) if not assumed and math.isfinite(total) and total > 0 else None
        )

        out_dir.mkdir(parents=True, exist_ok=True)
        frames: list[Path] = []
        index = 0
        while len(frames) < max_frames and capture.grab():
            if index % step == 0:
                ok, image = capture.retrieve()
                if not ok:
                    break
                destination = out_dir / f"frame-{len(frames):03d}.jpg"
                if not cv2.imwrite(
                    str(destination), image, [cv2.IMWRITE_JPEG_QUALITY, FRAME_JPEG_QUALITY]
                ):
                    # Disk full or similar: not a property of the video, so it is
                    # raised as an ordinary error and SQS retries it.
                    raise OSError(f"could not write {destination}")
                frames.append(destination)
            index += 1
    finally:
        capture.release()

    if not frames:
        raise UnreadableVideo("no frame could be read")
    return SampledVideo(frames=frames, duration_sec=duration, fps_assumed=assumed)


def max_n(per_frame: list[dict[str, int]]) -> dict[str, int]:
    """For each species, the largest count seen in any single frame.

    Summing would count one animal standing still once per second. MaxN is the
    standard camera-trap measure and never exceeds what was visible at once.
    """
    result: dict[str, int] = {}
    for tags in per_frame:
        for name, count in tags.items():
            result[name] = max(result.get(name, 0), count)
    return result


def busiest_frame(per_frame: list[dict[str, int]]) -> int:
    """Index of the frame with the most animals; the earliest wins a tie.

    The thumbnail comes from here rather than the first frame, because the first
    second of a trap clip is often empty: the animal is what triggered it.
    """
    totals = [sum(tags.values()) for tags in per_frame]
    return totals.index(max(totals))
