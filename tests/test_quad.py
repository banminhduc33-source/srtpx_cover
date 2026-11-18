"""Hộp 4 góc (chữ nghiêng/chạy chéo): đọc/ghi, nội suy, kéo hộp, preview và xuất video thật bằng ffmpeg."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from srtpx_cover.cli import main as cli_main
from srtpx_cover.core.edit import apply_drag
from srtpx_cover.core.geometry import compute_scale, interpolate_box, quad_dims, video_box
from srtpx_cover.core.model import Box, Keyframe, SrtpxDocument, Subtitle
from srtpx_cover.core.parser import parse_srtpx, write_srtpx
from tests.helpers import ASCII_NO_SPACE, have_ffmpeg, make_font

Q = (275.0, 1025.0, 825.0, 1000.0, 825.0, 1100.0, 275.0, 1125.0)   # ảnh editor: A, B, C, D


def test_from_quad_bbox_and_dims():
    b = Box.from_quad(Q)
    assert (b.x1, b.x2, b.y1, b.y2) == (275, 825, 1000, 1125) and b.quad == Q
    cx, cy, w, h, ang = quad_dims(Q)
    assert (cx, cy) == (550, 1062.5) and abs(w - 550.57) < 0.05 and h == pytest.approx(100.0, abs=0.5) and -3 < ang < -2
    with pytest.raises(ValueError):
        Box.from_quad((1, 2, 3))


def test_fixed_quad_roundtrip_and_old_readers_keep_bbox():
    doc = SrtpxDocument((1440, 1080), [Subtitle(1, 0.0, 2.0, "《退学但被保送》", Box.from_quad(Q), [])])
    text = write_srtpx(doc)
    assert "Độ rộng: 275px - 825px" in text and "Vị trí: 1000px - 1125px" in text      # phần mềm cũ vẫn che bằng hộp bao
    assert "Góc: 275 1025 825 1000 825 1100 275 1125" in text
    res = parse_srtpx(text)
    assert not res.warnings and res.document.subtitles[0].box == Box.from_quad(Q)
    assert write_srtpx(res.document) == text


def test_keyframe_quads_roundtrip():
    q2 = (300.0, 900.0, 840.0, 880.0, 840.0, 980.0, 300.0, 1000.0)
    sub = Subtitle(1, 0.0, 2.0, "abc", None, [Keyframe(0.0, Box.from_quad(Q)), Keyframe(1.0, Box.from_quad(q2)),
                                              Keyframe(2.0, Box(100, 200, 100, 150))])   # KF hộp chữ nhật xen kẽ vẫn được
    text = write_srtpx(SrtpxDocument((1440, 1080), [sub]))
    assert text.count("KG ") == 2
    res = parse_srtpx(text)
    assert not res.warnings and res.document.subtitles[0].keyframes == sub.keyframes
    # KG không có KF cùng giờ -> cảnh báo, không hỏng
    bad = parse_srtpx(text.replace("KG 1.00s", "KG 1.50s"))
    assert any("KG" in str(w) for w in bad.warnings) and bad.document.subtitles[0].keyframes[1].box.quad is None


def test_malformed_goc_line_is_warned_not_fatal():
    txt = "#res 100x100\n\n1\n00:00:00,000 --> 00:00:01,000\nabc\nĐộ rộng: 1px - 50px\nVị trí: 1px - 20px\nGóc: 1 2 3\n"
    res = parse_srtpx(txt)
    assert len(res.document.subtitles) == 1 and res.document.subtitles[0].box.quad is None and res.warnings


def test_interpolation_of_quads_keeps_corners_moving_linearly():
    q2 = tuple(v + 100 for v in Q)
    sub = Subtitle(1, 0, 2, "x", None, [Keyframe(0.0, Box.from_quad(Q)), Keyframe(1.0, Box.from_quad(q2))])
    mid = interpolate_box(sub, 0.5, smooth=False)
    assert mid.quad == tuple(v + 50 for v in Q) and (mid.x1, mid.y2) == (325, 1175)


def test_video_box_scales_and_pushes_corners_out():
    b = Subtitle(1, 0, 1, "x", Box.from_quad((10, 20, 110, 10, 110, 60, 10, 70)), [])
    vb = video_box(b, 0.5, compute_scale((100, 100), (200, 200)), 1.0, (200, 200))
    assert vb.quad is not None and vb.quad[0] == 19 and vb.quad[1] == 39       # góc trên-trái (10,20)*2 rồi đẩy ra 1px
    assert vb.x2 == 221 or vb.x2 == 200                                         # bị kẹp trong khung 200 px


def test_dragging_quad_moves_and_resizes_all_corners():
    b = Box.from_quad(Q)
    m = apply_drag(b, "move", 10, -20, (1440, 1920))
    assert m.quad == tuple(round(v + (10 if i % 2 == 0 else -20), 1) for i, v in enumerate(Q))
    r = apply_drag(b, "e", 55, 0, (1440, 1920))                                  # kéo mép phải ra 10% rộng
    assert r.x2 == 880 and r.quad[2] == 880 and r.quad[0] == 275 and r.quad[1] == Q[1]


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=240)


@pytest.mark.skipif(not have_ffmpeg(), reason="không có ffmpeg/ffprobe")
def test_export_draws_polygon_not_bounding_box_and_rotates_text():
    d = Path(tempfile.mkdtemp(prefix="srtpx_quad_"))
    src, out, srt = d / "src.mp4", d / "out.mp4", d / "q.srtpx"
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
              "color=c=gray:size=640x360:rate=30:duration=2", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", str(src)])
    assert p.returncode == 0, p.stderr
    # hình bình hành: mép trái x=100, mép phải x=500; mép trên dốc từ y=100 (trái) xuống y=180 (phải); cao 80
    quad = (100, 100, 500, 180, 500, 260, 100, 180)
    text = write_srtpx(SrtpxDocument((640, 360), [Subtitle(1, 0.0, 2.0, "AB", Box.from_quad(quad), [])]))
    srt.write_bytes(text.encode("utf-8"))
    font = make_font(d / "f.ttf", ASCII_NO_SPACE)
    for with_text in (False, True):
        argv = [str(src), str(srt), str(out), "--crf", "0", "--preset", "ultrafast", "--no-vertical", "--margin", "0",
                "--font", f"{font.path}#{font.face_index}"]
        if not with_text:
            argv.append("--no-text")
        assert cli_main(argv) == 0
        p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(out), "-vf", "select='eq(n,10)',extractplanes=y",
                  "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        y = np.frombuffer(p.stdout, np.uint8).reshape(360, 640)
        # trong đa giác (cả hai đầu dốc) -> đen (hoặc chữ trắng khi có chữ: chỉ kiểm điểm gần mép)
        assert y[150, 110] < 30 and y[200, 490] < 30 and y[110, 110] < 30     # sát mép trái, sát mép phải, sát góc trên-trái
        # trong HỘP BAO nhưng NGOÀI đa giác -> vẫn xám (hộp chữ nhật sẽ làm đen cả hai góc này)
        assert y[105, 490] > 100 and y[255, 110] > 100, (y[105, 490], y[255, 110])
        if with_text:
            assert (y[150:230, 200:420] > 200).any()                         # có chữ trắng bên trong
