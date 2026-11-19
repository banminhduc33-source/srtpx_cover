"""Đa giác theo keyframe (dòng KP): chữ nghiêng/xoay + chuyển động -> 1 hộp đa giác nội suy giữa các keyframe."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from srtpx_cover.cli import main as cli_main
from srtpx_cover.core.edit import copy_keyframes, set_box_at
from srtpx_cover.core.geometry import compute_scale, interpolate_polygon, video_polygon
from srtpx_cover.core.model import Box, Keyframe, SrtpxDocument, Subtitle
from srtpx_cover.core.parser import parse_srtpx, write_srtpx
from tests.helpers import have_ffmpeg

P0 = ((100.0, 100.0), (500.0, 180.0), (500.0, 260.0), (100.0, 180.0))
P1 = tuple((x + 100.0, y + 40.0) for x, y in P0)


def _sub():
    return Subtitle(1, 0.0, 2.0, "abc", None, [Keyframe(0.0, Box(100, 500, 100, 260), P0),
                                              Keyframe(1.0, Box(200, 600, 140, 300), P1),
                                              Keyframe(2.0, Box(200, 600, 140, 300))])   # KF cuối không có đa giác


def test_roundtrip_and_old_readers_ignore_kp():
    doc = SrtpxDocument((640, 360), [_sub()])
    text = write_srtpx(doc)
    assert text.count("KP ") == 2 and "KP 0.00s: 100,100 500,180 500,260 100,180" in text
    res = parse_srtpx(text)
    assert not res.warnings and res.document.subtitles[0].keyframes == _sub().keyframes
    assert write_srtpx(res.document) == text
    assert "KF 1.00s: 200 600 140 300" in text                                  # KF vẫn là hộp thẳng cho bản cũ


def test_bad_or_orphan_kp_is_warned_not_fatal():
    text = write_srtpx(SrtpxDocument((640, 360), [_sub()]))
    orphan = parse_srtpx(text.replace("KP 1.00s", "KP 1.50s"))
    assert any("KP" in str(w) for w in orphan.warnings) and orphan.document.subtitles[0].keyframes[1].polygon is None
    bad = parse_srtpx(text.replace("KP 0.00s: 100,100 500,180 500,260 100,180", "KP 0.00s: 1,2 3,4"))
    assert any("KP" in str(w) for w in bad.warnings) and bad.document.subtitles[0].keyframes[0].polygon is None


def test_interpolation_linear_and_box_fallback_for_missing_polygon():
    s = _sub()
    mid = interpolate_polygon(s, 0.5)
    assert mid == tuple((x + 50.0, y + 20.0) for x, y in P0)
    # đoạn 1->2: KF cuối không có đa giác -> dùng 4 góc của hộp thẳng
    end = interpolate_polygon(s, 2.0)
    assert end == ((200, 140), (600, 140), (600, 300), (200, 300))
    assert interpolate_polygon(Subtitle(1, 0, 1, "x", None, [Keyframe(0, Box(1, 2, 1, 2)), Keyframe(1, Box(3, 4, 3, 4))]), 0.5) is None
    vp = video_polygon(s, compute_scale((640, 360), (1280, 720)), 0.0, (1280, 720), 0.0)
    assert vp[0] == (200.0, 200.0)                                              # scale x2


def test_dragging_keyframe_box_moves_its_polygon_and_copy_keeps_it():
    s = _sub()
    set_box_at(s, 0.0, Box(110, 510, 110, 270))                                 # dời hộp +10,+10
    assert s.keyframes[0].polygon == tuple((x + 10.0, y + 10.0) for x, y in P0)
    d = Subtitle(2, 5.0, 7.0, "z", Box(0, 1, 0, 1), [])
    assert copy_keyframes(s, d) and d.keyframes[1].polygon == P1


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=240)


@pytest.mark.skipif(not have_ffmpeg(), reason="không có ffmpeg/ffprobe")
def test_export_follows_interpolated_polygon_not_bounding_box():
    d = Path(tempfile.mkdtemp(prefix="srtpx_kp_"))
    src, out, srt = d / "src.mp4", d / "out.mp4", d / "k.srtpx"
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
              "color=c=gray:size=640x360:rate=30:duration=2", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", str(src)])
    assert p.returncode == 0, p.stderr
    srt.write_bytes(write_srtpx(SrtpxDocument((640, 360), [_sub()])).encode("utf-8"))
    assert cli_main([str(src), str(srt), str(out), "--crf", "0", "--preset", "ultrafast", "--no-vertical",
                     "--margin", "0", "--no-text"]) == 0
    def luma(n):
        q = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(out), "-vf",
                  f"select='eq(n,{n})',extractplanes=y", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        return np.frombuffer(q.stdout, np.uint8).reshape(360, 640)
    a = luma(0)                                    # đúng KF 0: P0 (mép trên dốc 100 -> 180)
    assert a[150, 110] < 30 and a[200, 490] < 30   # trong đa giác
    assert a[105, 490] > 100 and a[255, 110] > 100  # trong hộp bao nhưng ngoài đa giác -> vẫn xám
    b = luma(30)                                   # đúng KF 1: P1 = P0 dời (+100,+40)
    assert b[190, 210] < 30 and b[240, 590] < 30
    assert b[145, 590] > 100 and b[295, 210] > 100
    assert a[150, 110] < 30 and b[150, 110] > 100  # góc trái-trên cũ không còn bị che ở KF 1
