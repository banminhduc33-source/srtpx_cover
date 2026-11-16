from srtpx_cover.core.playback import (
    clamp_frame,
    format_clock,
    frame_at,
    last_frame_index,
    position_ms_for_frame,
)


def test_format_clock():
    assert format_clock(0) == "00:00.000"
    assert format_clock(9.5) == "00:09.500"
    assert format_clock(75.043) == "01:15.043"
    assert format_clock(3600 + 61.5) == "1:01:01.500"
    assert format_clock(-3) == "00:00.000"
    assert format_clock(59.9996) == "01:00.000"  # làm tròn mili giây không được ra '00:59.1000'


def test_frame_at_30fps_boundaries():
    assert frame_at(0.0, 30, 1) == 0
    assert frame_at(9.5, 30, 1) == 285
    assert frame_at(9.4999, 30, 1) == 285       # backend báo lệch dưới 1 ms vẫn ở khung 285
    assert frame_at(9.49, 30, 1) == 284         # lệch 10 ms (hơn dung sai) thì là khung trước
    assert frame_at(10.2, 30, 1) == 306


def test_frame_at_ntsc():
    # 30000/1001 fps: khung 1000 ở giờ 33.3667s
    k = 1000
    t = k * 1001 / 30000
    assert frame_at(t, 30000, 1001) == k
    assert frame_at(t - 0.0005, 30000, 1001) == k


def test_position_roundtrip_all_frames():
    for num, den in ((30, 1), (30000, 1001), (25, 1), (60, 1), (24000, 1001)):
        for k in list(range(0, 400)) + [5000, 100000]:
            ms = position_ms_for_frame(k, num, den)
            assert frame_at(ms / 1000, num, den) == k, (num, den, k)


def test_last_frame_and_clamp():
    assert last_frame_index(1.0, 30, 1) == 29
    assert last_frame_index(1.01, 30, 1) == 30
    assert last_frame_index(0.0, 30, 1) == 0
    assert clamp_frame(-5, 10.0, 30, 1) == 0
    assert clamp_frame(10**6, 10.0, 30, 1) == 299
    assert clamp_frame(42, 10.0, 30, 1) == 42
