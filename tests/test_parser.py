from pathlib import Path

import pytest

from srtpx_cover.core.model import Box, Keyframe
from srtpx_cover.core.parser import (
    SrtpxError,
    format_time,
    parse_srtpx,
    read_srtpx,
    save_srtpx,
    write_srtpx,
)

SAMPLE = Path(__file__).parent / "data" / "sample.srtpx"


def _sample_text() -> str:
    return SAMPLE.read_text(encoding="utf-8")


def test_sample_parses_without_warnings():
    r = parse_srtpx(_sample_text())
    assert r.warnings == []
    doc = r.document
    assert doc.res == (768, 576)
    assert [s.index for s in doc.subtitles] == [1, 2, 3, 4, 5]
    assert [s.text for s in doc.subtitles] == [
        "原神大世界做镜子了？", "米提亚先生", "该回去上班了", "瓦列里!?", "啊哈哈...",
    ]


def test_sample_times_and_boxes():
    subs = parse_srtpx(_sample_text()).document.subtitles
    assert (subs[0].start, subs[0].end) == (0.3, 9.5)
    assert (subs[1].start, subs[1].end) == (9.5, 10.2)
    assert subs[2].end == 11.2 and subs[3].start == 11.2
    assert subs[4].end == 16.233
    # Block 1 có KF: bỏ qua dòng Độ rộng/Vị trí (kèm chú thích trong ngoặc).
    assert subs[0].box is None
    assert subs[0].keyframes == [
        Keyframe(0.6, Box(242, 525, 29, 59)),
        Keyframe(9.4, Box(257, 515, 45, 80)),
    ]
    # Block 2 không có KF: hộp cố định, chú thích không gây lỗi.
    assert subs[1].box == Box(491, 632, 102, 137)
    assert subs[1].keyframes == []
    assert subs[4].keyframes[1] == Keyframe(16.2, Box(330, 435, 364, 394))


def test_adjacent_block_times_are_exactly_equal():
    subs = parse_srtpx(_sample_text()).document.subtitles
    assert subs[0].end == subs[1].start  # 9.5 == 9.5 tuyệt đối, không lệch số thực
    assert subs[1].end == subs[2].start
    assert subs[2].end == subs[3].start


def test_sample_round_trip():
    doc1 = parse_srtpx(_sample_text()).document
    r2 = parse_srtpx(write_srtpx(doc1))
    assert r2.warnings == []
    assert r2.document == doc1


def test_written_file_uses_first_keyframe_box_and_drops_comments():
    out = write_srtpx(parse_srtpx(_sample_text()).document)
    assert "cuối câu" not in out
    assert "Độ rộng: 242px - 525px\nVị trí: 29px - 59px\nKF 0.60s: 242 525 29 59" in out
    assert out.startswith("#res 768x576\n")
    assert not out.startswith("\ufeff")


def test_bom_and_crlf():
    text = "\ufeff" + _sample_text().replace("\n", "\r\n")
    r = parse_srtpx(text)
    assert r.warnings == []
    assert r.document == parse_srtpx(_sample_text()).document


def test_content_with_blank_lines_and_multiple_lines():
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\nDòng một\n\nDòng ba sau dòng trống\n"
        "Độ rộng: 10px - 20px\nVị trí: 30px - 40px\n"
    )
    r = parse_srtpx(text)
    assert r.warnings == []
    assert r.document.subtitles[0].text == "Dòng một\n\nDòng ba sau dòng trống"
    assert parse_srtpx(write_srtpx(r.document)).document == r.document


def test_content_may_start_with_hash_and_contain_arrow():
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n#1 fan --> 2 fan\n"
        "Độ rộng: 1px - 2px\nVị trí: 3px - 4px\n"
    )
    r = parse_srtpx(text)
    assert r.warnings == []
    assert r.document.subtitles[0].text == "#1 fan --> 2 fan"


def test_missing_spaces_and_comma_or_dot():
    text = (
        "#res 100x50\n\n"
        "1\n00:00:01.500-->00:00:02,5\nA\n"
        "Độrộng:10px-20px\nVịtrí:30px-40px\n\n"
        "2\n0:0:3 --> 0:0:4\nB\n"
        "Độ rộng: 10,5px - 20.25px\nVị trí:30 - 40\nKF 3,5s:1 2 3 4\nKF 3.9:5,6,7,8\n"
    )
    r = parse_srtpx(text)
    assert r.warnings == []
    s1, s2 = r.document.subtitles
    assert (s1.start, s1.end) == (1.5, 2.5)  # ",5" nghĩa là 500 ms
    assert s1.box == Box(10, 20, 30, 40)
    assert (s2.start, s2.end) == (3.0, 4.0)
    assert s2.keyframes == [Keyframe(3.5, Box(1, 2, 3, 4)), Keyframe(3.9, Box(5, 6, 7, 8))]


def test_decimals_in_box():
    text = "1\n00:00:01,000 --> 00:00:02,000\nX\nĐộ rộng: 10.5px - 20,25px\nVị trí: 1px - 2px\n"
    assert parse_srtpx(text).document.subtitles[0].box == Box(10.5, 20.25, 1, 2)


def test_extra_blank_lines_and_unsorted_keyframes():
    text = (
        "\n\n\n1\n\n00:00:01,000 --> 00:00:09,000\n\nX\n\n\n"
        "KF 5s: 1 2 3 4\n\nKF 2s: 5 6 7 8\n\n\n\n"
    )
    r = parse_srtpx(text)
    assert r.warnings == []
    s = r.document.subtitles[0]
    assert s.text == "X"
    assert [k.time for k in s.keyframes] == [2.0, 5.0]


def test_keyframes_without_width_and_position_lines_are_valid():
    text = "1\n00:00:01,000 --> 00:00:05,000\nHi\nKF 1.00s: 1 2 3 4\nKF 4.00s: 5 6 7 8\n"
    r = parse_srtpx(text)
    assert r.warnings == []
    s = r.document.subtitles[0]
    assert s.text == "Hi" and s.box is None and len(s.keyframes) == 2
    assert s.first_box == Box(1, 2, 3, 4)


def test_block_missing_everything_is_error_but_others_are_read():
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\nA\nĐộ rộng: 1px - 2px\nVị trí: 3px - 4px\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nB không có toạ độ\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nC\nĐộ rộng: 5px - 6px\nVị trí: 7px - 8px\n"
    )
    r = parse_srtpx(text)
    assert [s.index for s in r.document.subtitles] == [1, 3]
    skipped = [w for w in r.warnings if w.block_skipped]
    assert len(skipped) == 1 and skipped[0].line == 7
    assert "thiếu" in skipped[0].message


def test_bad_number_and_bad_time_blocks_are_skipped():
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\nA\nĐộ rộng: abc\nVị trí: 3px - 4px\n\n"
        "2\n00:00:03,000 -> 00:04,000\nB\nĐộ rộng: 1px - 2px\nVị trí: 3px - 4px\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nC\nĐộ rộng: 5px - 6px\nVị trí: 7px - 8px\n"
    )
    r = parse_srtpx(text)
    assert [s.index for s in r.document.subtitles] == [3]
    assert len([w for w in r.warnings if w.block_skipped]) == 2
    # Block lỗi giờ không được nuốt vào nội dung block trước.
    assert r.document.subtitles[0].text == "C"


def test_bad_kf_line_is_skipped_with_warning():
    text = (
        "1\n00:00:01,000 --> 00:00:05,000\nA\nĐộ rộng: 1px - 2px\nVị trí: 3px - 4px\n"
        "KF 1s: 1 2 3\nKF 2s: 1 2 3 4\n"
    )
    r = parse_srtpx(text)
    assert len(r.document.subtitles[0].keyframes) == 1
    assert any(not w.block_skipped for w in r.warnings)


def test_swapped_edges_are_reordered_with_warning():
    text = "1\n00:00:01,000 --> 00:00:02,000\nA\nĐộ rộng: 20px - 10px\nVị trí: 40px - 30px\n"
    r = parse_srtpx(text)
    assert r.document.subtitles[0].box == Box(10, 20, 30, 40)
    assert len(r.warnings) == 1


def test_missing_index_gets_auto_number():
    text = "00:00:01,000 --> 00:00:02,000\nA\nĐộ rộng: 1px - 2px\nVị trí: 3px - 4px\n"
    r = parse_srtpx(text)
    assert r.document.subtitles[0].index == 1
    assert len(r.warnings) == 1


def test_no_res_line_and_empty_file():
    assert parse_srtpx(_sample_text().split("\n", 2)[2]).document.res is None
    r = parse_srtpx("")
    assert r.document.subtitles == [] and len(r.warnings) == 1


def test_nfd_labels_are_recognised():
    import unicodedata

    text = "1\n00:00:01,000 --> 00:00:02,000\nA\nĐộ rộng: 1px - 2px\nVị trí: 3px - 4px\n"
    r = parse_srtpx(unicodedata.normalize("NFD", text))
    assert r.warnings == []
    assert r.document.subtitles[0].box == Box(1, 2, 3, 4)


def test_round_trip_with_fractions_and_emoji(tmp_path):
    text = (
        "#res 1920x1080\n\n"
        "7\n01:02:03,004 --> 01:02:05,999\nHello 😀\nCJK 你好\n"
        "Độ rộng: 10.125px - 20.5px\nVị trí: 30px - 40.75px\n\n"
    )
    doc = parse_srtpx(text).document
    path = tmp_path / "out.srtpx"
    save_srtpx(doc, path)
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    r = read_srtpx(path)
    assert r.warnings == [] and r.document == doc


def test_read_srtpx_errors(tmp_path):
    with pytest.raises(SrtpxError):
        read_srtpx(tmp_path / "khong_co.srtpx")
    bad = tmp_path / "bad.srtpx"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(SrtpxError):
        read_srtpx(bad)


def test_format_time():
    assert format_time(0.3) == "00:00:00,300"
    assert format_time(16.233) == "00:00:16,233"
    assert format_time(3723.004) == "01:02:03,004"
