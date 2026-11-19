import re
import tempfile
from pathlib import Path

import pytest

from srtpx_cover.core.export import (
    ExportOptions,
    build_command,
    build_filter_script,
    escape_path,
    filter_script_args,
    lin_expr,
    write_textfiles,
)
from srtpx_cover.core.ffmpeg_tools import FfmpegError, VideoInfo, find_tool
from srtpx_cover.core.geometry import compute_scale, pixel_box
from srtpx_cover.core.model import Box, Keyframe, SrtpxDocument, Subtitle
from srtpx_cover.core.parser import read_srtpx
from tests.helpers import fake_chain, layer_at

SAMPLE = Path(__file__).parent / "data" / "sample.srtpx"
VIDEO = VideoInfo(768, 576, 30, 1, 17.0)
CHAIN = list(fake_chain(Path(tempfile.mkdtemp())))
BOX_RE = re.compile(r"drawbox=x=(\d+):y=(\d+):w=(\d+):h=(\d+):color=black:t=fill:"
                    r"enable='gte\(t,([-\d.]+)\)\*lt\(t,([-\d.]+)\)'")


def sample_doc() -> SrtpxDocument:
    return read_srtpx(SAMPLE).document


def build(doc=None, video=VIDEO, **kw):
    return build_filter_script(doc or sample_doc(), video, CHAIN, ExportOptions(**kw), Path("/tmp/work"))


def boxes_of(text: str):
    return [(int(a), int(b), int(c), int(d), float(e), float(f)) for a, b, c, d, e, f in BOX_RE.findall(text)]


# ---- escape và biểu thức

def test_escape_windows_drive_path():
    assert escape_path("C:\\Windows\\Fonts\\arial.ttf") == "C\\\\:/Windows/Fonts/arial.ttf"


def test_escape_special_characters():
    assert escape_path("/a/O'Brien,x;[y]=z.ttf") == "/a/O\\\\\\'Brien\\,x\\;\\[y\\]\\=z.ttf"


def _eval(expr: str, t: float) -> float:
    py = expr.replace("if(", "_if(").replace("lt(", "_lt(")
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "t": t})


def test_lin_expr_interpolates_and_clamps():
    e = lin_expr([(1, 10), (3, 30), (4, 0)])
    assert [_eval(e, t) for t in (0, 1, 2, 3, 3.5, 4, 9)] == pytest.approx([10, 10, 20, 30, 15, 0, 0])


def test_lin_expr_single_point_is_constant():
    assert lin_expr([(5, 12.5)]) == "12.5"


# ---- hộp

def test_no_between_and_half_open_enable():
    text = build().text
    assert "between(" not in text
    assert "gte(t," in text and "*lt(t," in text


def test_fixed_box_is_single_drawbox_with_frame_aligned_window():
    doc = sample_doc()
    text = build(doc).text
    # phụ đề 2: 9.5s -> 10.2s = khung 285..305; cửa sổ lệch nửa khung
    assert "x=490:y=101:w=143:h=37:color=black:t=fill:enable='gte(t,9.483333)*lt(t,10.183333)'" in text


def test_adjacent_blocks_have_touching_but_not_overlapping_windows():
    b = boxes_of(build().text)
    win2 = next(w for w in b if w[:4] == (490, 101, 143, 37))
    win3 = next(w for w in b if w[0] == 499)     # phụ đề 3: x1 = 500 - 1
    assert win2[5] == win3[4]


def test_keyframed_boxes_match_geometry_on_every_frame():
    doc = sample_doc()
    scale = compute_scale(doc.res, VIDEO.size)
    runs = boxes_of(build(doc).text)
    for sub in (doc.subtitles[0], doc.subtitles[4]):
        mine = [r for r in runs if r[4] >= sub.start - 1 / 30 and r[5] <= sub.end + 1 / 30]
        assert len(mine) > 3
        for r in mine:                                    # mỗi khung trong cửa sổ phải khớp hình học
            k0, k1 = round(r[4] * 30 + 0.5), round(r[5] * 30 - 0.5)
            for k in range(k0, k1 + 1):
                b = pixel_box(sub, k / 30, scale, 1, VIDEO.size)
                assert (b.x1, b.y1, b.w, b.h) == r[:4]


def test_keyframed_windows_are_contiguous():
    doc = sample_doc()
    runs = [r for r in boxes_of(build(doc).text) if r[4] < 10]
    sub1 = [r for r in runs if r[5] <= 9.5]
    assert sub1[0][4] == pytest.approx(8.5 / 30, abs=1e-6) and sub1[-1][5] == pytest.approx(284.5 / 30, abs=1e-6)
    for a, b in zip(sub1, sub1[1:]):
        assert a[5] == b[4]


def test_scaled_video_halves_the_boxes():
    video = VideoInfo(384, 288, 30, 1, 17.0)
    b = next(r for r in boxes_of(build(video=video).text) if r[4] == pytest.approx(9.483333))
    exp = pixel_box(sample_doc().subtitles[1], 9.6, compute_scale((768, 576), (384, 288)), 1, (384, 288))
    assert b[:4] == (exp.x1, exp.y1, exp.w, exp.h)


def test_empty_document_gives_null_filter():
    assert build(SrtpxDocument()).text == "null"


# ---- chữ

def test_text_goes_to_utf8_textfiles_not_into_the_command_line():
    s = build()
    assert "原神大世界做镜子了？" not in s.text
    assert "expansion=none" in s.text and "textfile=" in s.text
    tmp = Path(tempfile.mkdtemp())
    doc = sample_doc()
    s = build_filter_script(doc, VIDEO, CHAIN, ExportOptions(), tmp)
    write_textfiles(s)
    f = tmp / "sub0001_0.txt"
    assert f.read_bytes() == "原神大世界做镜子了？".encode("utf-8")      # không BOM


def test_multiline_text_is_one_drawtext_per_line_centered_around_box():
    doc = SrtpxDocument(None, [Subtitle(1, 1.0, 2.0, "abc\ndef", Box(100, 300, 100, 160), [])])
    s = build(doc)
    lines = [ln for ln in s.text.split(",\n") if ln.startswith("drawtext")]
    assert len(lines) == 2
    assert "+(-" in lines[0] and "+(" in lines[1] and "text_w/2" in lines[0]
    assert len(s.textfiles) == 2


def test_no_text_option_draws_only_boxes():
    s = build(draw_text=False)
    assert "drawtext" not in s.text and not s.textfiles


def test_keyframed_text_position_follows_box_over_time():
    # chữ chỉ dời (cỡ không đổi) cũng đi qua lớp perspective để vị trí mượt dưới 1px, không bị drawtext làm tròn
    doc = SrtpxDocument(None, [Subtitle(1, 1.0, 3.0, "abc", None,
                                        [Keyframe(1.0, Box(50, 150, 50, 90)), Keyframe(2.0, Box(150, 250, 100, 140))])])
    s = build(doc)
    assert s.complex and "perspective=" in s.text
    for t, (cx, cy) in {1.0: (100, 70), 1.5: (150, 95), 2.0: (200, 120)}.items():   # lề đối xứng nên tâm không đổi
        _, x, y, _ = layer_at(s.text, 1, t)
        assert x == pytest.approx(cx, abs=1e-3) and y == pytest.approx(cy, abs=1e-3)


def _resizing_doc() -> SrtpxDocument:
    return SrtpxDocument(None, [Subtitle(1, 1.0, 3.0, "abc", None,
                                         [Keyframe(1.0, Box(50, 150, 50, 90)), Keyframe(2.0, Box(40, 260, 40, 110))])])


def test_resizing_text_uses_subpixel_perspective_layer_not_integer_fontsize():
    # drawtext chỉ nhận fontsize/vị trí nguyên px; scale+overlay làm tròn kích thước (bước 2px) và vị trí (bội số của 2
    # với yuv420p) -> chữ nhảy nấc. Phải dùng perspective (toạ độ số thực) trong canvas đặt cố định.
    s = build(_resizing_doc())
    assert s.complex and "perspective=" in s.text and "eval=frame" in s.text and "overlay=" in s.text
    assert s.text.rstrip().endswith("[vout]")
    assert "scale=" not in s.text              # scale làm tròn kích thước theo số nguyên (bước 2px) -> giật
    assert "fontsize='" not in s.text          # fontsize cố định, không còn biểu thức theo t trong drawtext
    assert "interpolation=cubic" in s.text and "sense=destination" in s.text


def test_resizing_text_overlay_position_is_constant_even_integers():
    # overlay làm tròn vị trí (và ép về bội số của 2 với yuv420p) nên vị trí phải là hằng số chẵn, không phải biểu thức
    s = build(_resizing_doc())
    ovs = re.findall(r"overlay=x=(-?\d+):y=(-?\d+)", s.text)
    assert ovs and all(int(x) % 2 == 0 and int(y) % 2 == 0 for x, y in ovs)
    assert "round(" not in s.text and "overlay=x='" not in s.text


def test_resizing_text_size_and_center_are_continuous_not_quantized():
    s = build(_resizing_doc())
    ts = [1.0 + k / 30 for k in range(31)]
    sizes = [layer_at(s.text, 1, t)[0] for t in ts]
    cxs = [layer_at(s.text, 1, t)[1] for t in ts]
    dsz = [b - a for a, b in zip(sizes, sizes[1:])]
    assert all(0 < d < 1 for d in dsz)       # MỖI khung đều đổi, bước dưới 1px: không đứng yên vài khung rồi nhảy nấc
    # tâm chữ = tâm hộp thực (số thực, KHÔNG làm tròn): hộp 50..150 -> 40..260 nên tâm 100 -> 150, dịch ~1.67px/khung
    assert cxs[0] == pytest.approx(100, abs=1e-3) and cxs[-1] == pytest.approx(150, abs=1e-3)
    assert any(abs(c - round(c)) > 0.1 for c in cxs)


def test_fixed_size_text_keeps_plain_drawtext_chain():
    s = build()   # sample.srtpx: các phụ đề cố định / cùng cỡ chữ thì giữ chuỗi filter đơn như cũ
    s2 = build(SrtpxDocument(None, [Subtitle(1, 1.0, 3.0, "abc", Box(50, 150, 50, 90), [])]))
    assert not s2.complex and "overlay=" not in s2.text and "drawtext" in s2.text
    assert isinstance(s.complex, bool)


def test_complex_command_maps_video_label_and_keeps_audio():
    p = Path("/x/f.txt")
    assert filter_script_args(6, p, True) == ["-filter_complex_script", "/x/f.txt", "-map", "[vout]", "-map", "0:a?"]
    assert filter_script_args(7, p, True)[0] == "-/filter_complex"
    cmd = build_command("ffmpeg", "in.mp4", p, "out.mp4", ExportOptions(), 6, True)
    assert "[vout]" in cmd and "0:a?" in cmd and "-c:a" in cmd


# ---- cảnh báo

def test_font_fallback_warning_is_single_and_lists_subtitles():
    doc = SrtpxDocument(None, [Subtitle(1, 0, 1, "abc", Box(0, 90, 0, 40), []),
                               Subtitle(2, 1, 2, "Việt", Box(0, 90, 0, 40), [])])
    w = build(doc).warnings
    assert len([x for x in w if "dự phòng" in x]) == 1
    assert "2" in next(x for x in w if "dự phòng" in x)


def test_missing_glyph_warning():
    doc = SrtpxDocument(None, [Subtitle(7, 0, 1, "ab ☃", Box(0, 90, 0, 40), [])])
    assert any("Phụ đề 7" in x and "U+2603" in x for x in build(doc).warnings)


def test_long_text_shrinks_without_wrapping_or_warning():
    doc = SrtpxDocument(None, [Subtitle(3, 0, 1, "abc " * 5, Box(0, 40, 0, 14), [])])
    s = build(doc)
    assert len(s.textfiles) == 1                      # đúng một dòng, không xuống dòng
    assert not any("tràn hộp" in x for x in s.warnings)


def test_overflow_warning_only_when_even_1px_does_not_fit():
    doc = SrtpxDocument(None, [Subtitle(3, 0, 1, "a" * 200, Box(0, 40, 0, 14), [])])
    assert any("tràn hộp" in x and "3" in x for x in build(doc).warnings)


def test_too_short_subtitle_warning():
    doc = SrtpxDocument(None, [Subtitle(4, 0.301, 0.32, "abc", Box(0, 90, 0, 40), [])])
    s = build(doc)
    assert s.text == "null" and any("ngắn hơn một khung hình" in x for x in s.warnings)


def test_scale_mismatch_warning():
    assert any("lệch nhau quá 1%" in x for x in build(video=VideoInfo(768, 700, 30, 1, 17)).warnings)


# ---- lệnh

def test_filter_script_option_depends_on_ffmpeg_version():
    p = Path("/x/f.txt")
    assert filter_script_args(6, p) == ["-filter_script:v", "/x/f.txt"]
    assert filter_script_args(None, p) == ["-filter_script:v", "/x/f.txt"]
    assert filter_script_args(7, p) == ["-/filter:v", "/x/f.txt"]


def test_command_keeps_audio_and_uses_settings():
    cmd = build_command("ffmpeg", "in.mp4", Path("f.txt"), "out.mp4", ExportOptions(crf=20, preset="slow", threads=3))
    joined = " ".join(cmd)
    assert "-c:a copy" in joined and "-crf 20" in joined and "-preset slow" in joined and "-threads 3" in joined
    assert "-c:v libx264" in joined and cmd[-1] == "out.mp4"


def test_missing_ffmpeg_gives_vietnamese_error():
    import os
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = tempfile.mkdtemp()
    try:
        with pytest.raises(FfmpegError, match="Không tìm thấy ffmpeg"):
            find_tool("ffmpeg_khong_ton_tai_xyz")
        with pytest.raises(FfmpegError, match="tải"):
            find_tool("ffmpeg_khong_ton_tai_xyz")
    finally:
        os.environ["PATH"] = old


def test_segment_args_after_input_keep_original_time(tmp_path):
    opts = ExportOptions(start=5.0, end=12.5, codec="libx265")
    cmd = build_command("ffmpeg", "in.mp4", tmp_path / "f.txt", "out.mp4", opts)
    assert cmd.index("-ss") > cmd.index("-i") and cmd.index("-to") > cmd.index("-i")
    assert cmd[cmd.index("-ss") + 1] == "5.000" and cmd[cmd.index("-to") + 1] == "12.500"
    assert "libx265" in cmd
    assert opts.segment_seconds(20.0) == 7.5
    assert ExportOptions().segment_seconds(20.0) == 20.0


def test_font_chain_overrides_missing_file_warns(tmp_path):
    from srtpx_cover.core.fonts import font_chain_from_settings
    chain, warnings = font_chain_from_settings(tmp_path, ["", "", str(tmp_path / "khong_co.ttf")])
    assert any("font CJK" in w for w in warnings)
