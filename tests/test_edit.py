from srtpx_cover.core.edit import (apply_drag, clone_subtitles, default_box, insert_position,
                                    parse_time_text, renumber, valid_times)
from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
from srtpx_cover.core.parser import parse_srtpx, write_srtpx

RES = (768, 576)
B = Box(100, 200, 50, 80)


def test_move_and_clamp():
    assert apply_drag(B, "move", 10, -5, RES) == Box(110, 210, 45, 75)
    assert apply_drag(B, "move", -999, -999, RES) == Box(0, 100, 0, 30)
    assert apply_drag(B, "move", 9999, 9999, RES) == Box(668, 768, 546, 576)


def test_resize_handles():
    assert apply_drag(B, "e", 20, 99, RES) == Box(100, 220, 50, 80)
    assert apply_drag(B, "nw", -10, -10, RES) == Box(90, 200, 40, 80)
    assert apply_drag(B, "se", 5, 5, RES) == Box(100, 205, 50, 85)


def test_resize_min_size_and_frame():
    assert apply_drag(B, "w", 999, 0, RES) == Box(199, 200, 50, 80)
    assert apply_drag(B, "s", 0, -999, RES) == Box(100, 200, 50, 51)
    assert apply_drag(B, "e", 9999, 0, RES).x2 == 768


def test_parse_time_text():
    assert parse_time_text("00:00:09,500") == 9.5
    assert parse_time_text("1:02.5") == 62.5
    assert parse_time_text(" 3,25 ") == 3.25
    assert parse_time_text("abc") is None and parse_time_text("") is None


def test_valid_times_insert_renumber_clone():
    assert valid_times(0, 1) and not valid_times(1, 1) and not valid_times(-1, 2)
    subs = [Subtitle(7, 0, 1, "a", B), Subtitle(9, 2, 3, "b", B)]
    assert insert_position(subs, 1.5) == 1 and insert_position(subs, 2) == 2 and insert_position(subs, -1) == 0
    c = clone_subtitles(subs)
    renumber(c)
    assert [s.index for s in c] == [1, 2] and [s.index for s in subs] == [7, 9]
    assert c[0] is not subs[0] and c[0] == Subtitle(1, 0, 1, "a", B)
    assert default_box((768, 576)).width > 0


def test_empty_and_multiline_text_round_trip():
    doc = SrtpxDocument(RES, [Subtitle(1, 0, 1, "", B), Subtitle(2, 1, 2, "a\n\nb", B)])
    again = parse_srtpx(write_srtpx(doc)).document
    assert again.subtitles == doc.subtitles


# ---- keyframe (mốc 5)
from srtpx_cover.core.edit import (  # noqa: E402
    add_keyframe, copy_keyframes, delete_keyframe, find_keyframe, move_keyframe, set_box_at,
)
from srtpx_cover.core.model import Box as _Box, Keyframe as _Kf, Subtitle as _Sub


def _kfsub(start=1.0):
    return _Sub(1, start, start + 5, "x", None, [_Kf(start, _Box(0, 10, 0, 10)), _Kf(start + 2, _Box(5, 15, 5, 15))])


def test_set_box_at_creates_and_replaces_keyframe():
    s = _kfsub()
    set_box_at(s, 2.0, _Box(1, 2, 3, 4))
    assert [k.time for k in s.keyframes] == [1.0, 2.0, 3.0]
    set_box_at(s, 2.0, _Box(9, 9, 9, 9))
    assert len(s.keyframes) == 3 and s.keyframes[1].box == _Box(9, 9, 9, 9)


def test_set_box_at_fixed_box_edits_box():
    s = _Sub(1, 0, 1, "x", _Box(0, 1, 0, 1))
    set_box_at(s, 0.5, _Box(2, 3, 2, 3))
    assert s.box == _Box(2, 3, 2, 3) and not s.keyframes


def test_add_keyframe_converts_fixed_box():
    s = _Sub(1, 0, 1, "x", _Box(0, 1, 0, 1))
    assert add_keyframe(s, 0.5, _Box(0, 1, 0, 1)) == 0
    assert s.box is None and len(s.keyframes) == 1
    s.validate()


def test_move_keyframe_clamps_between_neighbours():
    s = _kfsub()
    assert move_keyframe(s, 0, 99.0) < s.keyframes[1].time
    assert move_keyframe(s, 1, -5.0) > s.keyframes[0].time
    s.validate()


def test_delete_last_keyframe_returns_to_fixed_box():
    s = _kfsub()
    delete_keyframe(s, 0)
    delete_keyframe(s, 0)
    assert s.keyframes == [] and s.box == _Box(5, 15, 5, 15)
    s.validate()


def test_copy_keyframes_shifts_times():
    src, dst = _kfsub(1.0), _Sub(2, 10.0, 12.0, "y", _Box(0, 1, 0, 1))
    assert copy_keyframes(src, dst)
    assert [k.time for k in dst.keyframes] == [10.0, 12.0] and dst.box is None
    dst.validate()
    assert not copy_keyframes(dst.__class__(3, 0, 1, "z", _Box(0, 1, 0, 1)), src)
    assert find_keyframe(dst, 12.0) == 1
