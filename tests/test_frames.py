from pathlib import Path

from srtpx_cover.core.geometry import (
    compute_scale,
    frame_time,
    smallest_pixel_size,
    visible_frame_range,
)
from srtpx_cover.core.model import Box, Subtitle
from srtpx_cover.core.parser import read_srtpx

SAMPLE = Path(__file__).parent / "data" / "sample.srtpx"


def S(start, end):
    return Subtitle(1, start, end, "x", Box(0, 10, 0, 10), [])


def test_frame_time():
    assert frame_time(306, 30, 1) == 10.2
    assert abs(frame_time(30, 30000, 1001) - 1.001) < 1e-12


def test_visible_range_half_open_on_30fps():
    assert visible_frame_range(S(9.5, 10.2), 30, 1) == (285, 305)
    assert visible_frame_range(S(10.2, 11.2), 30, 1) == (306, 335)


def test_adjacent_subtitles_never_share_a_frame():
    a, b = visible_frame_range(S(9.5, 10.2), 30, 1), visible_frame_range(S(10.2, 11.2), 30, 1)
    assert a[1] + 1 == b[0]


def test_every_sample_boundary_matches_float_noise_free():
    # 10.2*30 = 306.00000000000006 trong số thực; vẫn phải ra khung 306, không phải 307
    assert visible_frame_range(S(10.2, 10.3), 30, 1)[0] == 306


def test_non_integer_fps():
    assert visible_frame_range(S(1.0, 2.0), 30000, 1001) == (30, 59)


def test_start_of_video_and_too_short():
    assert visible_frame_range(S(0.0, 0.05), 30, 1) == (0, 1)
    assert visible_frame_range(S(0.301, 0.32), 30, 1) is None


def test_smallest_size_uses_smallest_keyframe_box():
    doc = read_srtpx(SAMPLE).document
    scale = compute_scale(doc.res, (768, 576))
    # phụ đề 1: KF 242-525 / 29-59 và KF 257-515 / 45-80, lề 3 => rộng nhỏ nhất 258+6, cao nhỏ nhất 30+6
    assert smallest_pixel_size(doc.subtitles[0], scale, 3, (768, 576)) == (264, 36)
    # phụ đề 2 không có KF
    assert smallest_pixel_size(doc.subtitles[1], scale, 3, (768, 576)) == (141 + 6, 35 + 6)
