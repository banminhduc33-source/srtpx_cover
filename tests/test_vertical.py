import subprocess
from pathlib import Path

import pytest

from srtpx_cover.core.ffmpeg_tools import probe_video, run_ffmpeg
from srtpx_cover.core.vertical import (
    VerticalOptions, build_vertical_command, build_vertical_filter, find_logo, logo_size, plan_vertical,
)
from tests.helpers import have_ffmpeg

LOGO = (1200, 330)


def test_landscape_video_fits_width_and_logo_is_20px_below_it():
    g = plan_vertical((768, 432), LOGO)
    assert (g.fg_w, g.fg_h, g.fg_x) == (1080, 608, 0)
    assert g.has_logo and g.logo_y == g.fg_y + g.fg_h + 20
    assert g.logo_x == (1080 - g.logo_w) // 2 and g.logo_y + g.logo_h <= 1920


def test_tall_video_fits_height_and_skips_logo_when_no_room():
    g = plan_vertical((540, 960), LOGO)                  # đã là 9:16: không còn chỗ cho logo
    assert (g.fg_w, g.fg_h, g.fg_y) == (1080, 1920, 0) and not g.has_logo


def test_video_is_shifted_up_if_logo_would_overflow():
    g = plan_vertical((1000, 1000), LOGO)                # vuông: video 1080x1080
    assert g.has_logo and g.logo_y + g.logo_h <= 1920 and g.fg_y >= 0
    assert g.logo_y == g.fg_y + g.fg_h + 20


def test_filter_has_blur_and_35_percent_brightness():
    f = build_vertical_filter(plan_vertical((768, 432), LOGO))
    assert "gblur=" in f and "val*0.35" in f and "[vout]" in f and "overlay=0:" in f


def test_logo_asset_present_and_trimmed():
    logo = find_logo()
    assert logo is not None
    w, h = logo_size(logo)
    assert w == 1200 and h < 400


@pytest.mark.skipif(not have_ffmpeg(), reason="cần ffmpeg")
def test_end_to_end_makes_1080x1920_and_keeps_audio(tmp_path: Path):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=1",
                    "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(src)], check=True)
    info = probe_video(src, "ffprobe")
    logo = find_logo()
    g = plan_vertical(info.size, logo_size(logo))
    fs = tmp_path / "v.txt"; fs.write_text(build_vertical_filter(g))
    out = tmp_path / "out.mp4"
    run_ffmpeg(build_vertical_command("ffmpeg", src, logo, fs, out, g, VerticalOptions(preset="ultrafast")), info.duration)
    o = probe_video(out, "ffprobe")
    assert o.size == (1080, 1920) and abs(o.duration - info.duration) < 0.1
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                             capture_output=True, text=True).stdout
    assert "audio" in streams
