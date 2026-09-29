"""Tests for thumbnail generation.

The last test here is a regression test. A corrupt file uploaded to the raw
bucket was retried three times and reported as a transient failure, because the
two components that open an image disagree about what to do when they cannot:

  tagger.tag_images  catches the failure and returns no tags
  make_thumbnail     lets it propagate

So inference "succeeded" with an empty result and the thumbnail step raised,
past the point where the handler classified errors. The handler now verifies
readability once, before either step runs. These tests pin both behaviours so
that the asymmetry cannot be reintroduced silently.
"""

import pytest
from PIL import Image, UnidentifiedImageError

from media_utils import MAX_SIDE, make_thumbnail


def write_image(path, size, mode="RGB"):
    Image.new(mode, size, color=(90, 120, 90) if mode == "RGB" else 128).save(path)
    return path


def test_longest_side_is_capped(tmp_path):
    source = write_image(tmp_path / "wide.jpg", (2400, 1600))
    destination = tmp_path / "thumb.jpg"

    make_thumbnail(source, destination)

    with Image.open(destination) as thumbnail:
        assert max(thumbnail.size) == MAX_SIDE


def test_aspect_ratio_is_preserved(tmp_path):
    """A panorama must stay a panorama; fitting inside the box, not filling it."""
    source = write_image(tmp_path / "pano.jpg", (3000, 1000))
    destination = tmp_path / "thumb.jpg"

    make_thumbnail(source, destination)

    with Image.open(destination) as thumbnail:
        assert thumbnail.size == (MAX_SIDE, MAX_SIDE // 3)


def test_small_images_are_not_upscaled(tmp_path):
    """Enlarging adds bytes without adding detail."""
    source = write_image(tmp_path / "small.jpg", (120, 80))
    destination = tmp_path / "thumb.jpg"

    make_thumbnail(source, destination)

    with Image.open(destination) as thumbnail:
        assert thumbnail.size == (120, 80)


def test_greyscale_infrared_survives(tmp_path):
    """Camera traps shoot single-channel infrared at night."""
    source = write_image(tmp_path / "night.jpg", (1000, 800), mode="L")
    destination = tmp_path / "thumb.jpg"

    make_thumbnail(source, destination)

    assert destination.stat().st_size > 0


def test_a_palette_image_is_converted_rather_than_failing(tmp_path):
    """JPEG cannot store a palette, so the mode has to be normalised first."""
    source = tmp_path / "paletted.png"
    Image.new("P", (600, 400)).save(source)
    destination = tmp_path / "thumb.jpg"

    make_thumbnail(source, destination)

    with Image.open(destination) as thumbnail:
        assert thumbnail.mode == "RGB"


def test_thumbnail_is_much_smaller_than_the_original(tmp_path):
    """The entire reason thumbnails exist: a results grid must stay cheap."""
    source = write_image(tmp_path / "big.jpg", (4000, 3000))
    destination = tmp_path / "thumb.jpg"

    make_thumbnail(source, destination)

    assert destination.stat().st_size < source.stat().st_size


def test_an_unreadable_file_raises(tmp_path):
    """Regression: this raises, while tagger.tag_images swallows the same error.

    The handler must therefore establish readability before either component
    runs, rather than relying on whichever one happens to open the file first.
    """
    source = tmp_path / "not-really.jpg"
    source.write_bytes(b"this is text, not an image")

    with pytest.raises(UnidentifiedImageError):
        make_thumbnail(source, tmp_path / "thumb.jpg")
