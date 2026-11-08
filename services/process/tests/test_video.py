"""Frame sampling and the two ways per-frame tags are combined.

All pure: OpenCV and the filesystem, no models and no AWS. Videos come from the
make_video fixture in conftest.py.
"""

import math

import cv2
import numpy as np
import pytest

import video

# --- effective_fps -----------------------------------------------------------


# 1000 and 90000 are container time bases that some AVI, MOV and variable-rate
# files report as their frame rate; trusting them would keep one frame a minute.
@pytest.mark.parametrize("reported", [0.0, -1.0, math.nan, math.inf, 1000.0, 90000.0])
def test_an_unusable_frame_rate_falls_back_and_is_flagged(reported):
    assert video.effective_fps(reported) == (video.FALLBACK_FPS, True)


def test_a_reported_frame_rate_is_used_as_is():
    assert video.effective_fps(25.0) == (25.0, False)


def test_a_high_speed_camera_rate_is_still_trusted():
    assert video.effective_fps(240.0) == (240.0, False)


# --- sample_frames -----------------------------------------------------------


def test_one_frame_is_kept_per_second(make_video, tmp_path):
    clip = make_video("clip.avi", seconds=5, fps=10)
    sampled = video.sample_frames(clip, tmp_path / "frames")
    assert len(sampled.frames) == 5
    assert all(frame.exists() and frame.suffix == ".jpg" for frame in sampled.frames)
    assert sampled.fps_assumed is False


def test_frames_are_returned_in_time_order(make_video, tmp_path):
    """Frame i of the synthetic clip is grey level i, so the kept frames (0, 10,
    20, ...) must read back brighter each time."""
    clip = make_video("clip.avi", seconds=5, fps=10)
    frames = video.sample_frames(clip, tmp_path / "frames").frames
    levels = [cv2.imread(str(frame)).mean() for frame in frames]
    assert levels == sorted(levels)
    assert levels[-1] - levels[0] > 30


def test_an_mp4_is_sampled_too(make_video, tmp_path):
    clip = make_video("clip.mp4", seconds=3, fps=10)
    assert len(video.sample_frames(clip, tmp_path / "frames").frames) == 3


def test_sampling_stops_at_the_frame_cap(make_video, tmp_path):
    clip = make_video("long.avi", seconds=90, fps=2)
    assert len(video.sample_frames(clip, tmp_path / "frames").frames) == video.MAX_FRAMES


def test_the_cap_can_be_lowered(make_video, tmp_path):
    clip = make_video("clip.avi", seconds=10, fps=5)
    assert len(video.sample_frames(clip, tmp_path / "frames", max_frames=3).frames) == 3


def test_the_duration_comes_from_the_container(make_video, tmp_path):
    clip = make_video("clip.avi", seconds=5, fps=10)
    assert video.sample_frames(clip, tmp_path / "frames").duration_sec == pytest.approx(5.0)


def test_a_file_of_random_bytes_is_unreadable(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00garbage" * 100)
    with pytest.raises(video.UnreadableVideo):
        video.sample_frames(junk, tmp_path / "frames")


def test_a_missing_file_is_unreadable(tmp_path):
    with pytest.raises(video.UnreadableVideo):
        video.sample_frames(tmp_path / "absent.mp4", tmp_path / "frames")


def test_a_jpeg_renamed_to_mp4_is_one_frame_without_a_duration(tmp_path):
    """FFmpeg probes content, not the name. Measured with OpenCV 5.0.0: a renamed
    JPEG opens, reports 25 fps and a frame count of about -9.2e18."""
    still = tmp_path / "still.mp4"
    ok, encoded = cv2.imencode(".jpg", np.full((48, 64, 3), 120, np.uint8))
    assert ok
    still.write_bytes(encoded.tobytes())

    sampled = video.sample_frames(still, tmp_path / "frames")

    assert len(sampled.frames) == 1
    assert sampled.duration_sec is None


# --- max_n -------------------------------------------------------------------


def test_max_n_keeps_the_largest_count_seen_in_one_frame():
    """One wallaby standing still for three seconds is one wallaby, not three."""
    per_frame = [{"wallaby": 1}, {"wallaby": 1, "emu": 2}, {"wallaby": 3}]
    assert video.max_n(per_frame) == {"wallaby": 3, "emu": 2}


def test_max_n_of_no_frames_is_empty():
    assert video.max_n([]) == {}


def test_max_n_of_empty_frames_is_empty():
    assert video.max_n([{}, {}]) == {}


# --- busiest_frame -----------------------------------------------------------


def test_the_busiest_frame_has_the_most_animals():
    assert video.busiest_frame([{"emu": 1}, {"emu": 1, "dingo": 2}, {"dingo": 2}]) == 1


def test_the_earliest_frame_wins_a_tie():
    assert video.busiest_frame([{}, {"emu": 2}, {"dingo": 2}]) == 1


def test_with_no_animals_the_first_frame_is_used():
    assert video.busiest_frame([{}, {}, {}]) == 0
