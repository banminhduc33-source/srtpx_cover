"""Test pixel (mục 9): xuất video thật bằng ffmpeg rồi so từng khung với hàm hình học.

Bỏ qua (skip) nếu máy không có ffmpeg/ffprobe hoặc không có font CJK.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from srtpx_cover.cli import main as cli_main
from srtpx_cover.core.ffmpeg_tools import probe_video
from srtpx_cover.core.geometry import compute_scale, is_visible, pixel_box
from srtpx_cover.core.parser import read_srtpx
from tests.helpers import have_ffmpeg, real_font_chain

SAMPLE = Path(__file__).parent / "data" / "sample.srtpx"
FPS = 30
MARGIN = 1
BLACK = 16  # Y của màu đen trong yuv420p giới hạn dải
# Giữa mỗi block, đúng ranh giới hai block liền nhau (284|285 chỉ block 1|2, 305|306 -> 2|3, 335|336 -> 3|4),
# và ngoài mọi block (0, 8, 357, 400, 487, 509). Khung 487 (16.2333s) là khung đầu tiên sau block 5.
FRAMES = [0, 8, 9, 150, 283, 284, 285, 290, 305, 306, 320, 335, 336, 350, 356, 357, 400, 461, 462, 474, 486, 487, 509]


def _need_tools():
    if not have_ffmpeg():
        pytest.skip("không có ffmpeg/ffprobe")
    chain = real_font_chain()
    if chain is None:
        pytest.skip("không có font CJK trên máy này")
    return chain


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=240)


@functools.lru_cache(maxsize=None)
def _source(w: int, h: int) -> Path:
    d = Path(tempfile.mkdtemp(prefix="srtpx_test_"))
    out = d / "src.mp4"
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
              f"testsrc=size={w}x{h}:rate={FPS}:duration=17", "-c:v", "libx264", "-crf", "0",
              "-pix_fmt", "yuv420p", str(out)])
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    return out


def _luma(video: Path, size: tuple[int, int]) -> np.ndarray:
    sel = "+".join(f"eq(n\\,{k})" for k in FRAMES)
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf",
              f"select='{sel}',extractplanes=y", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    return np.frombuffer(p.stdout, np.uint8).reshape(len(FRAMES), size[1], size[0])


def _export(size: tuple[int, int], text: bool) -> tuple[Path, Path]:
    chain = _need_tools()
    src = _source(*size)
    out = Path(tempfile.mkdtemp(prefix="srtpx_out_")) / "out.mp4"
    argv = [str(src), str(SAMPLE), str(out), "--crf", "0", "--preset", "ultrafast", "--no-vertical"]
    for spec in chain:
        argv += ["--font", f"{spec.path}#{spec.face_index}"]
    if not text:
        argv.append("--no-text")
    assert cli_main(argv) == 0
    return src, out


def _check(src: Path, out: Path, size: tuple[int, int], text: bool) -> None:
    doc = read_srtpx(SAMPLE).document
    scale = compute_scale(doc.res, size)
    ref, got = _luma(src, size), _luma(out, size)
    w, h = size
    seen_boxes = 0
    for i, k in enumerate(FRAMES):
        t = k / FPS
        boxes = [pixel_box(s, t, scale, MARGIN, size) for s in doc.subtitles if is_visible(s, t)]
        seen_boxes += len(boxes)
        inner = np.zeros((h, w), bool)    # trong hộp, thu vào 1px
        near = np.zeros((h, w), bool)     # hộp nở ra 1px (dung sai mục 4)
        for b in boxes:
            inner[b.y1 + 1:b.y2 - 1, b.x1 + 1:b.x2 - 1] = True
            near[max(b.y1 - 1, 0):b.y2 + 1, max(b.x1 - 1, 0):b.x2 + 1] = True
        outside = ~near
        assert (got[i][outside] == ref[i][outside]).all(), f"khung {k}: vùng ngoài hộp bị đổi"
        if not text:
            assert (np.abs(got[i][inner].astype(int) - BLACK) <= 1).all(), f"khung {k}: hộp chưa đen hoàn toàn"
        else:
            for b in boxes:
                region = got[i][b.y1:b.y2, b.x1:b.x2]
                assert region.max() >= 200, f"khung {k}: không thấy chữ trắng trong hộp"
        if not boxes:
            assert (got[i] == ref[i]).all(), f"khung {k}: ngoài mọi block mà vẫn bị đổi"
    assert seen_boxes > 15


def test_boxes_are_pure_black_and_match_geometry_768x576():
    size = (768, 576)
    src, out = _export(size, text=False)
    _check(src, out, size, text=False)


def test_text_is_white_inside_boxes_and_nothing_leaks_outside():
    size = (768, 576)
    src, out = _export(size, text=True)
    _check(src, out, size, text=True)


def test_boxes_scale_with_video_resolution():
    size = (384, 288)   # #res 768x576 -> scale 0.5
    src, out = _export(size, text=False)
    _check(src, out, size, text=False)


def test_resolution_fps_and_duration_are_preserved():
    _need_tools()
    src, out = _export((768, 576), text=False)
    info = probe_video(out, shutil.which("ffprobe"))
    assert (info.width, info.height, info.fps_num, info.fps_den) == (768, 576, 30, 1)
    assert abs(info.duration - 17.0) < 0.1


def test_probe_video():
    _need_tools()
    info = probe_video(_source(768, 576), shutil.which("ffprobe"))
    assert info.size == (768, 576) and info.fps == 30 and abs(info.duration - 17) < 0.1


def test_ffmpeg_accepts_awkward_font_and_text_paths():
    chain = _need_tools()
    from srtpx_cover.core.export import escape_path
    names = ["thu muc co dau cach", "it's", "a,b;[c]=d"] + ([] if os.name == "nt" else ["a:b", "we:ird'dir"])
    for name in names:
        d = Path(tempfile.mkdtemp()) / name
        d.mkdir()
        font = d / "f.ttf"
        shutil.copy(chain[0].path, font)
        (d / "t.txt").write_bytes(b"Hello")
        vf = (f"drawtext=fontfile={escape_path(font)}:textfile={escape_path(d / 't.txt')}:expansion=none:"
              "fontsize=30:fontcolor=white:x=5:y=5")
        p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                  "color=black:s=200x60:d=0.1:r=10", "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        assert p.returncode == 0, f"{name}: {p.stderr.decode(errors='replace')[:200]}"
        assert max(p.stdout) >= 200, f"{name}: không vẽ được chữ"


def _text_centre_steps(video: Path, size: tuple[int, int], k_from: int, k_to: int, box_of) -> np.ndarray:
    w, h = size
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", "extractplanes=y",
              "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    frames = np.frombuffer(p.stdout, np.uint8).reshape(-1, h, w)
    cs = []
    for k in range(k_from, k_to):
        x1, x2, y1, y2 = box_of(k)
        reg = np.clip(frames[k][y1:y2, x1:x2].astype(float) - 16, 0, None)
        ys, xs = np.mgrid[y1:y2, x1:x2]
        cs.append(((reg * xs).sum() / reg.sum(), (reg * ys).sum() / reg.sum()))
    return np.diff(np.array(cs), axis=0)


@pytest.mark.parametrize("kind", ["ntsc", "vfr"])
def test_moving_text_is_smooth_on_ntsc_and_variable_frame_rate_sources(kind, tmp_path):
    """Chữ di chuyển/co giãn không được khựng khi nguồn là 29,97 fps hoặc VFR (PTS không đều, như video điện thoại)."""
    from srtpx_cover.core.geometry import video_box
    chain = _need_tools()
    size = (768, 576)
    src = tmp_path / "src.mp4"
    if kind == "ntsc":
        args = ["-i", "testsrc=size=768x576:rate=30000/1001:duration=7", "-c:v", "libx264"]
    else:
        args = ["-i", "testsrc=size=768x576:rate=30:duration=7", "-vf", "setpts='PTS+(random(0)-0.5)*0.012/TB'",
                "-fps_mode", "passthrough", "-video_track_timescale", "90000", "-c:v", "libx264"]
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", *args,
              "-crf", "0", "-pix_fmt", "yuv420p", str(src)])
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    sp = tmp_path / "fast.srtpx"
    sp.write_text("#res 768x576\n\n1\n00:00:00,500 --> 00:00:06,000\nXin chao cac ban\nHom nay troi dep\n"
                  "KF 0.50s: 100 400 100 170\nKF 5.50s: 300 700 300 400\n", encoding="utf-8")
    out = tmp_path / "out.mp4"
    argv = [str(src), str(sp), str(out), "--crf", "0", "--preset", "ultrafast", "--no-vertical"]
    for spec in chain:
        argv += ["--font", f"{spec.path}#{spec.face_index}"]
    assert cli_main(argv) == 0
    doc = read_srtpx(sp).document
    sc = compute_scale(doc.res, size)
    fps = 30000 / 1001 if kind == "ntsc" else 30.0

    def box_of(k):
        vb = video_box(doc.subtitles[0], k / fps, sc, MARGIN, size)
        return [int(round(v)) for v in (vb.x1 + 6, vb.x2 - 6, vb.y1 + 6, vb.y2 - 6)]

    d = _text_centre_steps(out, size, int(0.7 * fps), int(5.3 * fps), box_of)
    for axis in (0, 1):
        dev = np.abs(d[:, axis] - np.median(d[:, axis]))
        assert dev.max() < 0.6, f"trục {axis}: chữ khựng (lệch bước tối đa {dev.max():.2f}px)"
