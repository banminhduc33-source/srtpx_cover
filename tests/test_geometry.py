import pytest

from srtpx_cover.core.geometry import (
    PixelBox,
    compute_scale,
    interpolate_box,
    is_visible,
    pixel_box,
    video_box,
)
from srtpx_cover.core.model import Box, Keyframe, Subtitle


def fixed(box: Box, start: float = 0.0, end: float = 10.0) -> Subtitle:
    return Subtitle(1, start, end, "x", box, [])


def animated(start: float = 0.0, end: float = 20.0) -> Subtitle:
    return Subtitle(
        1, start, end, "x", None,
        [Keyframe(1.0, Box(100, 200, 10, 20)), Keyframe(3.0, Box(200, 400, 30, 60))],
    )


# ---- scale
def test_scale_independent_axes():
    s = compute_scale((768, 576), (1536, 864))
    assert (s.sx, s.sy) == (2.0, 1.5)
    assert s.is_mismatched and s.warning() is not None


def test_scale_without_res_is_one():
    s = compute_scale(None, (1920, 1080))
    assert (s.sx, s.sy) == (1.0, 1.0) and s.warning() is None


def test_scale_mismatch_threshold_is_one_percent():
    assert not compute_scale((1000, 1000), (1000, 1009)).is_mismatched
    assert compute_scale((1000, 1000), (1000, 1011)).is_mismatched


# ---- làm tròn, lề, kẹp
def test_rounding_floor_start_ceil_end():
    s = fixed(Box(10.2, 20.2, 30.7, 40.1))
    pb = pixel_box(s, 1.0, compute_scale(None, (100, 100)), 0, (100, 100))
    assert pb == PixelBox(10, 30, 21, 41)


def test_integer_edges_do_not_gain_a_pixel_from_float_noise():
    # 525 * (1280/768) = 875.0000000000001 -> không được làm tròn lên 876
    s = fixed(Box(242, 525, 29, 59))
    sc = compute_scale((768, 576), (1280, 960))
    pb = pixel_box(s, 1.0, sc, 0, (1280, 960))
    assert pb.x2 == 875 and pb.x1 == 403  # 242*1.6666.. = 403.33 -> xuống 403


def test_margin_added_on_all_sides_in_video_pixels():
    s = fixed(Box(100, 200, 50, 80))
    pb = pixel_box(s, 1.0, compute_scale((768, 576), (768, 576)), 3, (768, 576))
    assert pb == PixelBox(97, 47, 203, 83)


def test_margin_is_not_scaled():
    # Lề tính theo điểm ảnh video thực: 3px, không bị nhân với scale (=2).
    s = fixed(Box(50, 100, 25, 40))
    pb = pixel_box(s, 1.0, compute_scale((200, 200), (400, 400)), 3, (400, 400))
    assert pb == PixelBox(97, 47, 203, 83)


def test_clamped_inside_frame():
    s = fixed(Box(1, 767, 1, 575))
    pb = pixel_box(s, 1.0, compute_scale((768, 576), (768, 576)), 3, (768, 576))
    assert pb == PixelBox(0, 0, 768, 576)
    s2 = fixed(Box(-50, 900, -5, 700))
    pb2 = pixel_box(s2, 1.0, compute_scale((768, 576), (768, 576)), 3, (768, 576))
    assert pb2 == PixelBox(0, 0, 768, 576)


def test_video_box_is_unrounded_float():
    b = video_box(fixed(Box(10.5, 20.5, 1, 2)), 1.0, compute_scale(None, (100, 100)), 0.5, (100, 100))
    assert (b.x1, b.x2, b.y1, b.y2) == (10.0, 21.0, 0.5, 2.5)


# ---- nửa mở
def test_half_open_interval():
    s = fixed(Box(0, 1, 0, 1), start=9.5, end=10.2)
    assert not is_visible(s, 9.499999)
    assert is_visible(s, 9.5)
    assert is_visible(s, 10.199999)
    assert not is_visible(s, 10.2)


def test_adjacent_blocks_never_overlap():
    a = fixed(Box(0, 1, 0, 1), 0.0, 9.5)
    b = fixed(Box(0, 1, 0, 1), 9.5, 10.2)
    for t in (0.0, 4.0, 9.4999, 9.5, 9.5001, 10.1999, 10.2):
        assert not (is_visible(a, t) and is_visible(b, t))
    assert is_visible(b, 9.5) and not is_visible(a, 9.5)


# ---- nội suy
def test_interpolation_between_keyframes():
    b = interpolate_box(animated(), 2.0)  # đúng giữa 1.0 và 3.0
    assert (b.x1, b.x2, b.y1, b.y2) == (150, 300, 20, 40)


def test_interpolation_quarter_point_each_value_independent():
    b = interpolate_box(animated(), 1.5)
    assert (b.x1, b.x2, b.y1, b.y2) == (125, 250, 15, 30)


def test_holds_nearest_keyframe_outside_range():
    s = animated()
    assert interpolate_box(s, 0.0) == Box(100, 200, 10, 20)
    assert interpolate_box(s, 1.0) == Box(100, 200, 10, 20)
    assert interpolate_box(s, 3.0) == Box(200, 400, 30, 60)
    assert interpolate_box(s, 19.0) == Box(200, 400, 30, 60)


def test_single_keyframe_is_constant():
    s = Subtitle(1, 0, 5, "x", None, [Keyframe(2.0, Box(1, 2, 3, 4))])
    assert interpolate_box(s, 0) == interpolate_box(s, 4.9) == Box(1, 2, 3, 4)


def test_sample_block_5_keyframes():
    s = Subtitle(5, 15.4, 16.233, "x", None,
                 [Keyframe(15.4, Box(362, 420, 335, 346)), Keyframe(16.2, Box(330, 435, 364, 394))])
    b = interpolate_box(s, 15.8)  # giữa 15.4 và 16.2
    assert b.x1 == pytest.approx(346) and b.x2 == pytest.approx(427.5)
    assert b.y1 == pytest.approx(349.5) and b.y2 == pytest.approx(370)
