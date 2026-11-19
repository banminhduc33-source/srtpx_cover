"""Test hộp che phụ đề phụ lấy màu nền: vành đai, chọn phụ đề phụ, lấy màu từ video thật, vẽ bằng màu đó."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from srtpx_cover.core.animlayer import AnimLayer
from srtpx_cover.core.bgcolor import hex_color, ring_color, sample_rect, sample_secondary_colors, secondary_indices
from srtpx_cover.core.export import ExportOptions, build_filter_script
from srtpx_cover.core.ffmpeg_tools import VideoInfo
from srtpx_cover.core.geometry import compute_scale
from srtpx_cover.core.model import Box, SrtpxDocument, Subtitle

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
need_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="cần ffmpeg/ffprobe")
BG = (0x33, 0x66, 0x99)


def _doc() -> SrtpxDocument:
    """3 câu cùng vị trí ở dưới (phụ đề chính) + 1 nhãn ở chỗ khác (phụ đề phụ, #2)."""
    main, other = Box(100, 400, 500, 550), Box(500, 700, 150, 190)
    return SrtpxDocument(res=(768, 576), subtitles=[
        Subtitle(1, 0.0, 1.0, "Câu một", box=main), Subtitle(2, 0.0, 1.0, "Nhãn chữ", box=other),
        Subtitle(3, 1.0, 2.0, "Câu hai", box=main), Subtitle(4, 2.0, 3.0, "Câu ba", box=main)])


def test_secondary_are_those_outside_main_group():
    assert secondary_indices(_doc()) == {2}
    single = SrtpxDocument(res=(768, 576), subtitles=[Subtitle(1, 0.0, 1.0, "Một mình", box=Box(100, 400, 500, 550))])
    assert secondary_indices(single) is None          # không có nhóm chính: không lấy màu


def test_ring_color_uses_pixels_outside_box_only():
    img = Image.new("RGB", (60, 30), BG)
    for x in range(10, 50):                            # "chữ gốc" trắng nằm TRONG hộp: không được tính
        for y in range(8, 22):
            img.putpixel((x, y), (255, 255, 255))
    assert ring_color(img, (10, 8, 50, 22)) == BG
    img.putpixel((3, 3), (0, 0, 0))                    # một điểm nhiễu ở vành đai không kéo lệch trung vị
    assert ring_color(img, (10, 8, 50, 22)) == BG
    assert ring_color(Image.new("RGB", (10, 10), BG), (0, 0, 10, 10)) is None   # không có vành đai


def test_sample_rect_is_clamped_to_frame():
    sub = Subtitle(1, 0.0, 1.0, "x", box=Box(-50, 100, 500, 700))
    rect = sample_rect(sub, 0.5, compute_scale((768, 576), (768, 576)), 0.0, (768, 576))
    assert rect == (0, 500, 100, 576)
    out = Subtitle(2, 0.0, 1.0, "y", box=Box(900, 950, 10, 20))
    assert sample_rect(out, 0.5, compute_scale((768, 576), (768, 576)), 0.0, (768, 576)) is None


def test_hex_color():
    assert hex_color(BG) == "0x336699" and hex_color((0, 0, 0)) == "0x000000"


@need_ffmpeg
def test_colors_come_from_real_video_around_the_box(tmp_path):
    video = tmp_path / "bg.mp4"
    # nền xanh phẳng; một mảng trắng ngay TRONG hộp của #2 mô phỏng chữ gốc
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x336699:s=768x576:r=25:d=3",
                    "-vf", "drawbox=x=510:y=155:w=180:h=30:color=white:t=fill", "-c:v", "libx264", "-qp", "0",
                    "-pix_fmt", "yuv444p", str(video)], check=True)
    info = VideoInfo(768, 576, 25, 1, 3.0)
    colors, warnings = sample_secondary_colors(_doc(), video, info, 0.0, FFMPEG)
    assert warnings == [] and set(colors) == {2}
    assert all(abs(a - b) <= 4 for a, b in zip(colors[2], BG)), colors[2]   # sai số đổi không gian màu


def test_filter_script_draws_secondary_box_with_its_color_and_others_black(tmp_path):
    doc, info = _doc(), VideoInfo(768, 576, 25, 1, 3.0)
    opts = ExportOptions(draw_text=False, margin=0.0, box_colors={2: BG})
    script = build_filter_script(doc, info, [], opts, tmp_path)
    lines = [l for l in script.text.splitlines() if l.startswith("drawbox")]
    assert sum("color=0x336699" in l for l in lines) == 1 and sum("color=black" in l for l in lines) == 3
    plain = build_filter_script(doc, info, [], ExportOptions(draw_text=False, margin=0.0), tmp_path)
    assert "0x336699" not in plain.text and plain.text.count("color=black") == 4   # không truyền màu: như cũ


def test_animlayer_paints_box_with_given_color(tmp_path):
    lay = AnimLayer(directory=tmp_path / "a", font_path="", face_index=0, lines=(), k0=0, k1=0, ox=0, oy=0, cw=20, ch=20,
                    geometry=lambda k: (2.0, 18.0, 2.0, 18.0, 1.0), color=BG)
    lay.render()
    px = Image.open(tmp_path / "a" / "f00000.png").convert("RGBA")
    assert px.getpixel((10, 10)) == (*BG, 255) and px.getpixel((0, 0))[3] == 0
