"""Image derivatives produced alongside tagging.

Only thumbnails for now. Video frame sampling arrives in Phase 12.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# Results pages show a grid of these. At 300px a thumbnail is roughly 30 KB
# against 5 MB for the original, so a fifty-result page costs 1.5 MB instead of
# 250 MB. That ratio is the reason thumbnails exist at all.
MAX_SIDE = 300
JPEG_QUALITY = 80


def make_thumbnail(source: str | Path, destination: str | Path, max_side: int = MAX_SIDE) -> None:
    """Write a thumbnail whose longest side is at most `max_side` pixels.

    Aspect ratio is preserved: Image.thumbnail scales to fit inside the box
    rather than stretching to fill it, so a panorama stays a panorama.

    Images smaller than the box are left at their original size rather than
    being upscaled, which would add bytes without adding detail.
    """
    with Image.open(source) as image:
        # Camera traps emit greyscale infrared and palette PNGs among others;
        # JPEG cannot store either, so normalise first.
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((max_side, max_side), Image.LANCZOS)
        image.save(destination, format="JPEG", quality=JPEG_QUALITY, optimize=True)
