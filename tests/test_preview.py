import re
import tempfile
from pathlib import Path

import pytest

from srtpx_cover.core.export import ExportOptions, build_filter_script
from srtpx_cover.core.ffmpeg_tools import VideoInfo
from srtpx_cover.core.geometry import compute_scale, frame_time, pixel_box
from srtpx_cover.core.parser import parse_srtpx
from srtpx_cover.core.preview import PreviewModel
from tests.helpers import fake_chain, layer_at

SAMPLE = Path(__file__).parent / "data" / "sample.srtpx"
CHAIN = list(fake_chain(Path(tempfile.mkdtemp())))
VIDEO = VideoInfo(768, 576, 30, 1, 20.0)


def _eval_expr(expr: str, t: float) -> float:
    py = expr.replace("if(", "_if(").replace("lt(", "_lt(").replace("max(", "_max(").replace("min(", "_min(")
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b,
                     "_max": max, "_min": min, "t": t})


def _export_size(script_text: str, index: int, t: float) -> float:
    """Cỡ chữ (px) của phụ đề `index` trong script xuất tại giờ t.

    Chữ cỡ cố định: biểu thức fontsize của drawtext. Chữ co giãn/di chuyển theo hộp: vẽ ở cỡ s0 rồi perspective ánh xạ
    theo cỡ chữ liên tục, nên cỡ lấy ra từ độ rộng ảnh sau ánh xạ (layer_at).
    """
    chunk = next(c for c in re.split(r";\n|,\n", script_text) if "drawtext" in c and f"sub{index:04d}_" in c)
    if (m := re.search(r"fontsize='(.*?)':fontcolor", chunk)):
        return _eval_expr(m.group(1), t)
    return layer_at(script_text, index, t)[0]


def _doc():
    return parse_srtpx(SAMPLE.read_text(encoding="utf-8")).document


def _model(video_size=(768, 576), margin=1.0, chain=CHAIN):
    return PreviewModel(_doc(), video_size, chain, margin)


def test_nothing_visible_outside_blocks():
    m = _model()
    assert m.items_at(0.0) == []
    assert m.items_at(13.0) == []       # giữa block 4 và 5
    assert m.items_at(17.0) == []       # sau block 5


def test_half_open_boundaries_never_overlap():
    m = _model()
    assert [i.index for i in m.items_at(9.5)] == [2]     # block 1 kết thúc đúng 9.5 -> không còn
    assert [i.index for i in m.items_at(9.4999)] == [1]
    assert [i.index for i in m.items_at(10.2)] == [3]
    assert [i.index for i in m.items_at(11.2)] == [4]


def test_box_matches_geometry_function():
    doc = _doc()
    m = PreviewModel(doc, (768, 576), CHAIN, 1.0)
    scale = compute_scale(doc.res, (768, 576))
    for t in (0.3, 0.6, 5.0, 9.4, 10.0, 15.9, 16.2):
        for it in m.items_at(t):
            assert it.box == pixel_box(doc.subtitles[it.row], t, scale, 1.0, (768, 576))


def test_scales_with_real_video_size():
    # video thực gấp đôi #res: hộp phải gấp đôi (lề 0 để so cho dễ)
    m = _model(video_size=(1536, 1152), margin=0.0)
    it = m.items_at(10.0)[0]          # block 2: x 491..632, y 102..137
    assert (it.box.x1, it.box.x2, it.box.y1, it.box.y2) == (982, 1264, 204, 274)


def test_text_centered_in_box_and_multiline_offsets():
    m = _model(margin=0.0)
    it = m.items_at(10.0)[0]
    assert it.lines and it.font is not None
    assert it.cx == (491 + 632) / 2 and it.cy == (102 + 137) / 2
    assert it.line_center_y(0) == it.cy  # một dòng thì nằm đúng tâm

    two = PreviewModel(parse_srtpx(
        "#res 100x100\n\n1\n00:00:00,000 --> 00:00:01,000\nab\ncd\nĐộ rộng: 0px - 100px\nVị trí: 0px - 80px\n"
    ).document, (100, 100), CHAIN, 0.0).items_at(0.5)[0]
    assert two.lines == ("ab", "cd")
    assert two.line_center_y(0) == two.cy - two.line_height / 2
    assert two.line_center_y(1) == two.cy + two.line_height / 2


def test_keyframe_box_moves_over_time():
    m = _model(margin=0.0)
    a = m.items_at(0.6)[0].box
    b = m.items_at(9.4 - 1 / 30)[0].box
    assert a.x1 == 242 and a.x2 == 525
    assert b.x1 > a.x1 and b.y2 > a.y2


def test_empty_text_gives_box_without_lines():
    m = PreviewModel(parse_srtpx(
        "1\n00:00:00,000 --> 00:00:01,000\n   \nĐộ rộng: 10px - 50px\nVị trí: 10px - 30px\n"
    ).document, (100, 100), CHAIN, 0.0)
    it = m.items_at(0.5)[0]
    assert it.lines == () and it.font is None and it.box.w == 40


def test_no_fonts_gives_boxes_and_a_warning():
    m = _model(chain=[])
    assert m.items_at(10.0)[0].lines == ()
    assert any("font" in w.lower() for w in m.warnings)


def test_scale_mismatch_warning():
    assert any("lệch" in w for w in _model(video_size=(768, 1024)).warnings)
    assert not any("lệch" in w for w in _model().warnings)


def test_preview_layout_equals_export_layout():
    """Preview và bộ xuất dùng cùng font và cỡ chữ."""
    doc = _doc()
    m = PreviewModel(doc, VIDEO.size, CHAIN, 1.0)
    script = build_filter_script(doc, VIDEO, CHAIN, ExportOptions(margin=1.0), Path("/tmp/x"))
    texts = set(script.textfiles.values())
    for sub in doc.subtitles:
        t = (sub.start + sub.end) / 2
        t = frame_time(round(t * 30), 30, 1)
        for it in m.items_at(t):
            if it.index != sub.index:
                continue
            assert it.lines
            for line in it.lines:
                if line:
                    assert line in texts
            assert _export_size(script.text, sub.index, t) == pytest.approx(it.font_size, abs=0.6)   # cùng cỡ chữ (sai số làm tròn hộp)
            assert it.font is not None and it.font.path.replace("\\", "/").split("/")[-1] in script.text


_GROW = (
    "#res 1000x1000\n\n1\n00:00:00,000 --> 00:00:02,000\nabcdefghij\n"
    "Độ rộng: 100px - 200px\nVị trí: 100px - 140px\n"
    "KF 0.00s: 100 200 100 140\n"
    "KF 2.00s: 100 600 100 220\n"
)


def test_text_grows_with_keyframe_box():
    """Keyframe đầu nhỏ, hộp phóng to dần thì cỡ chữ phải phóng to theo (không kẹt ở cỡ nhỏ nhất)."""
    from srtpx_cover.core.parser import parse_srtpx as parse
    doc = parse(_GROW).document
    assert len(doc.subtitles[0].keyframes) == 2
    m = PreviewModel(doc, (1000, 1000), CHAIN, 0.0)
    sizes = [m.items_at(t)[0].font_size for t in (0.0, 0.5, 1.0, 1.5, 1.9)]
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0] * 2
    # xuất: biểu thức fontsize cũng tăng liên tục theo t và khớp preview
    script = build_filter_script(doc, VideoInfo(1000, 1000, 30, 1, 5.0), CHAIN, ExportOptions(margin=0.0), Path("/tmp/x"))
    for t in (0.0, 0.5, 1.0, 1.5, 1.9):
        assert _export_size(script.text, 1, t) == pytest.approx(m.items_at(t)[0].font_size, abs=0.6)
    assert _export_size(script.text, 1, 1.9) > _export_size(script.text, 1, 0.0) * 4
