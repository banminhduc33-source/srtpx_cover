"""Đa giác che chữ nghiêng: đọc/ghi .srtpx, hình học, sửa hộp, nhóm chính và xuất video thật (pixel)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from srtpx_cover.cli import main as cli_main
from srtpx_cover.core.edit import add_keyframe, clone_subtitle, map_polygon, set_box_at
from srtpx_cover.core.geometry import compute_scale, expand_polygon, polygon_bounds, video_polygon
from srtpx_cover.core.maincap import apply_main_captions
from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle
from srtpx_cover.core.parser import parse_srtpx, write_srtpx
from tests.helpers import have_ffmpeg

POLY = ((100.0, 140.0), (300.0, 100.0), (305.0, 120.0), (105.0, 160.0))
TEXT = (
    "#res 640x480\r\n\r\n1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
    "Độ rộng: 100px - 305px\r\nVị trí: 100px - 160px\r\n"
    "Đa giác: 100,140 300,100 305,120 105,160\r\n"
)


def test_parse_and_roundtrip_polygon():
    r = parse_srtpx(TEXT)
    assert not r.warnings
    sub = r.document.subtitles[0]
    assert sub.polygon == POLY and sub.box == Box(100, 305, 100, 160)
    out = write_srtpx(r.document)
    assert "Đa giác: 100,140 300,100 305,120 105,160" in out
    again = parse_srtpx(out)
    assert not again.warnings and again.document == r.document


def test_polygon_line_is_optional_and_old_files_unchanged():
    r = parse_srtpx(TEXT.replace("Đa giác: 100,140 300,100 305,120 105,160\r\n", ""))
    assert r.document.subtitles[0].polygon is None
    assert "Đa giác" not in write_srtpx(r.document)


@pytest.mark.parametrize("bad", ["100,140 300,100 305,120", "a b c d", "0,0 0,0 0,0 0,0"])
def test_bad_polygon_is_ignored_with_warning_and_box_kept(bad):
    r = parse_srtpx(TEXT.replace("100,140 300,100 305,120 105,160", bad))
    assert r.warnings and not any(w.block_skipped for w in r.warnings)
    sub = r.document.subtitles[0]
    assert sub.polygon is None and sub.box == Box(100, 305, 100, 160)


def test_polygon_is_ignored_when_subtitle_has_keyframes():
    r = parse_srtpx(TEXT + "KF 1.00s: 100 305 100 160\r\n")
    sub = r.document.subtitles[0]
    assert sub.keyframes and sub.polygon is None
    assert any("Đa giác" in str(w) for w in r.warnings)


def test_validate_rejects_polygon_with_keyframes_or_wrong_corner_count():
    from srtpx_cover.core.model import Keyframe
    with pytest.raises(ValueError):
        Subtitle(1, 0, 1, "a", None, [Keyframe(0, Box(0, 1, 0, 1))], polygon=POLY).validate()
    with pytest.raises(ValueError):
        Subtitle(1, 0, 1, "a", Box(0, 1, 0, 1), polygon=POLY[:3]).validate()


def test_expand_polygon_is_outward_for_both_windings():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    for pts in (sq, sq[::-1]):
        xs = [p[0] for p in expand_polygon(pts, 1)]
        ys = [p[1] for p in expand_polygon(pts, 1)]
        assert (min(xs), max(xs), min(ys), max(ys)) == (-1, 11, -1, 11)
    assert expand_polygon(sq, 0) == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_video_polygon_scales_from_res():
    sub = Subtitle(1, 0, 1, "a", Box(0, 1, 0, 1), polygon=((0, 0), (10, 0), (10, 10), (0, 10)))
    pts = video_polygon(sub, compute_scale((100, 100), (200, 400)), 0.0, (200, 400))
    assert polygon_bounds(pts) == (0.0, 20.0, 0.0, 40.0)
    assert video_polygon(Subtitle(1, 0, 1, "a", Box(0, 1, 0, 1)), compute_scale(None, (10, 10)), 1.0) is None


def test_dragging_box_moves_and_scales_polygon():
    sub = Subtitle(1, 0, 1, "a", Box(100, 305, 100, 160), polygon=POLY)
    set_box_at(sub, 0.0, Box(110, 315, 120, 180))                  # dời (+10, +20)
    assert sub.polygon == tuple((x + 10, y + 20) for x, y in POLY)
    assert map_polygon(POLY, Box(100, 300, 100, 160), Box(100, 500, 100, 160))[1] == (500.0, 100.0)   # giãn ngang x2
    clone = clone_subtitle(sub)
    assert clone.polygon == sub.polygon
    add_keyframe(sub, 0.5, Box(0, 10, 0, 10))                       # dùng keyframe thì không còn đa giác
    assert sub.polygon is None and sub.keyframes


def test_polygon_subtitles_are_left_out_of_main_caption_group():
    subs = [Subtitle(i, i, i + 1, f"cau {i}", Box(100, 300, 400, 440)) for i in range(1, 5)]
    tilted = Subtitle(9, 6, 7, "nghieng", Box(100, 300, 400, 440), polygon=POLY)
    doc = SrtpxDocument(res=(640, 480), subtitles=subs + [tilted])
    out, info = apply_main_captions(doc)
    assert info.applied
    kept = [s for s in out.subtitles if s.polygon is not None]
    assert len(kept) == 1 and kept[0] is tilted and kept[0].text == "nghieng"


# ---------------------------------------------------------------- xuất thật

def test_export_draws_black_polygon_not_bounding_box():
    if not have_ffmpeg():
        pytest.skip("không có ffmpeg/ffprobe")
    w, h, fps = 640, 480, 30
    d = Path(tempfile.mkdtemp(prefix="srtpx_poly_"))
    src, srtpx, out = d / "src.mp4", d / "p.srtpx", d / "out.mp4"
    p = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=white:size={w}x{h}:rate={fps}:duration=3", "-c:v", "libx264", "-crf", "0",
                        "-pix_fmt", "yuv420p", str(src)], capture_output=True)
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    srtpx.write_bytes(TEXT.replace("Hello", "").replace("\r\n\r\n1", "\r\n\r\n1", 1).encode("utf-8"))
    assert cli_main([str(src), str(srtpx), str(out), "--crf", "0", "--preset", "ultrafast", "--no-vertical",
                     "--no-text"]) == 0
    k = int(1.5 * fps)
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(out), "-vf",
                          f"select='eq(n\\,{k})',extractplanes=y", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "gray",
                          "-"], capture_output=True).stdout
    y = np.frombuffer(raw, np.uint8).reshape(h, w).astype(int)
    black = y <= 20
    # tâm đa giác đen; góc hộp bao (100,100) ở ngoài đa giác nên vẫn trắng; xa đa giác không đổi
    assert black[130, 200] and black[140, 150]
    assert not black[102, 108] and not black[158, 300]
    assert not black[300, 300] and not black[10, 10]
    # diện tích đen ~ diện tích hình bình hành (|200*20 + 40*5| = 4200, cộng lề 1px ~ 4400), xa nhỏ hơn hộp bao (205 x 60 = 12300)
    assert 4000 < black.sum() < 5000


def test_polygon_with_text_goes_through_layer_and_renders_black_polygon_plus_white_text(tmp_path):
    """Dùng font giả (không cần font thật): phụ đề đa giác có chữ -> một lớp PNG, hộp đen là đa giác, chữ trắng nằm trong hộp thẳng."""
    from PIL import Image

    from srtpx_cover.core.export import ExportOptions, build_filter_script, write_textfiles
    from srtpx_cover.core.ffmpeg_tools import VideoInfo
    from tests.helpers import fake_chain

    chain = list(fake_chain(tmp_path))
    video = VideoInfo(640, 480, 30, 1, 3.0)
    doc = SrtpxDocument(res=(640, 480), subtitles=[
        Subtitle(1, 1.0, 2.0, "Hello", Box(100, 305, 100, 160), polygon=POLY),
        Subtitle(2, 1.0, 2.0, "", Box(400, 500, 300, 340)),
    ])
    script = build_filter_script(doc, video, chain, ExportOptions(), tmp_path / "work")
    assert script.complex and len(script.layers) == 1 and script.layers[0].polygon is not None
    assert script.text.count("overlay=") == 1 and "drawbox=x=399" in script.text   # hộp thường vẫn là drawbox
    write_textfiles(script)
    lay = script.layers[0]
    im = Image.open(lay.directory / "f00000.png").convert("RGBA")
    px = lambda x, y: im.getpixel((x - lay.ox, y - lay.oy))
    assert px(110, 140) == (0, 0, 0, 255)       # trong đa giác: đen đục
    assert px(102, 104)[3] == 0                                            # góc hộp bao nhưng ngoài đa giác: trong suốt
    white = [(x, y) for x in range(lay.ox, lay.ox + lay.cw) for y in range(lay.oy, lay.oy + lay.ch) if px(x, y)[:3] == (255, 255, 255) and px(x, y)[3] > 200]
    assert white, "không thấy chữ trắng"
    assert all(100 <= x <= 305 and 100 <= y <= 160 for x, y in white)      # chữ nằm trong hộp thẳng
