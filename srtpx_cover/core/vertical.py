"""Dựng khung 9:16 từ video đã xuất (thuần Python, không Qt).

Từ video vừa xuất (đã che phụ đề) tạo video dọc 1080x1920:
  - NỀN: bản sao của video phóng to phủ kín khung 9:16 (cắt phần thừa), làm mờ, hạ độ sáng còn `brightness`;
  - GIỮA: video gốc giữ nguyên tỷ lệ, vừa chiều ngang khung;
  - DƯỚI: logo cách đáy video đúng `gap` px.
Âm thanh được giữ nguyên (-c:a copy).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

LOGO_NAME = "logo_genshinviet.png"


@dataclass(frozen=True)
class VerticalOptions:
    width: int = 1080
    height: int = 1920
    blur_sigma: float = 10.0        # độ mờ nền, đo ở 1/4 độ phân giải (~40px ở khung đầy đủ)
    brightness: float = 0.35        # độ sáng nền còn lại (1.0 = giữ nguyên)
    gap: int = 20                   # khoảng cách từ đáy video tới logo (px)
    logo_width_ratio: float = 0.65  # bề rộng logo / bề rộng khung
    center_y: float = 0.395         # vị trí tâm video theo chiều dọc (0..1), theo ảnh mẫu
    crf: int = 18
    preset: str = "veryfast"
    threads: int = 2
    codec: str = "libx264"


@dataclass(frozen=True)
class VerticalGeometry:
    fg_w: int
    fg_h: int
    fg_x: int
    fg_y: int
    logo_w: int
    logo_h: int
    logo_x: int
    logo_y: int
    has_logo: bool


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def plan_vertical(video_size: tuple[int, int], logo_size: tuple[int, int] | None,
                  opts: VerticalOptions = VerticalOptions()) -> VerticalGeometry:
    """Toạ độ video giữa và logo trong khung 9:16. Logo không đủ chỗ phía dưới thì bỏ (has_logo=False)."""
    W, H = opts.width, opts.height
    vw, vh = video_size
    fg_w = W
    fg_h = _even(W * vh / vw)
    if fg_h > H:                                   # video cao hơn 9:16: vừa theo chiều cao
        fg_h = H
        fg_w = _even(H * vw / vh)
    fg_x = (W - fg_w) // 2
    fg_y = int(round(opts.center_y * H - fg_h / 2))

    logo_w = logo_h = logo_x = logo_y = 0
    has_logo = False
    if logo_size is not None and logo_size[0] > 0:
        logo_w = _even(opts.logo_width_ratio * W)
        logo_h = _even(logo_w * logo_size[1] / logo_size[0])
        need_bottom = fg_y + fg_h + opts.gap + logo_h
        if need_bottom > H:                        # không đủ chỗ: nhích video lên cho vừa logo
            fg_y -= need_bottom - H
        if fg_y >= 0:
            has_logo = True
            logo_x = (W - logo_w) // 2
            logo_y = fg_y + fg_h + opts.gap
        else:                                      # video quá cao, không có chỗ cho logo
            fg_y = int(round(opts.center_y * H - fg_h / 2))
    fg_y = max(0, min(fg_y, H - fg_h))
    return VerticalGeometry(fg_w, fg_h, fg_x, fg_y, logo_w, logo_h, logo_x, logo_y, has_logo)


def build_vertical_filter(geom: VerticalGeometry, opts: VerticalOptions = VerticalOptions()) -> str:
    """filter_complex: đầu vào 0 = video đã xuất, 1 = logo (chỉ khi has_logo). Đầu ra [vout]."""
    W, H = opts.width, opts.height
    qw, qh = W // 4, H // 4
    k = _num(opts.brightness)
    parts = [
        "[0:v]split=2[a][b]",
        (f"[a]scale={qw}:{qh}:force_original_aspect_ratio=increase,crop={qw}:{qh},"
         f"gblur=sigma={_num(opts.blur_sigma)},scale={W}:{H},format=gbrp,"
         f"lutrgb=r=val*{k}:g=val*{k}:b=val*{k},format=yuv420p[bg]"),
        f"[b]scale={geom.fg_w}:{geom.fg_h}:flags=lanczos,format=yuv420p[fg]",
    ]
    if geom.has_logo:
        parts += [
            f"[1:v]scale={geom.logo_w}:{geom.logo_h}:flags=lanczos,format=rgba[lg]",
            f"[bg][fg]overlay={geom.fg_x}:{geom.fg_y}:format=auto[t]",
            f"[t][lg]overlay={geom.logo_x}:{geom.logo_y}:format=auto,format=yuv420p[vout]",
        ]
    else:
        parts.append(f"[bg][fg]overlay={geom.fg_x}:{geom.fg_y}:format=auto,format=yuv420p[vout]")
    return ";\n".join(parts)


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def build_vertical_command(
    ffmpeg: str,
    src: str | Path,
    logo: str | Path | None,
    filter_script: Path,
    output: str | Path,
    geom: VerticalGeometry,
    opts: VerticalOptions = VerticalOptions(),
    ffmpeg_major: int | None = None,
) -> list[str]:
    new = ffmpeg_major is not None and ffmpeg_major >= 7
    cmd = [ffmpeg, "-y", "-i", str(src)]
    if geom.has_logo and logo is not None:
        cmd += ["-i", str(logo)]
    cmd += [
        "-/filter_complex" if new else "-filter_complex_script", str(filter_script),
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", opts.codec, "-crf", str(opts.crf), "-preset", opts.preset,
        "-threads", str(opts.threads), "-pix_fmt", "yuv420p",
        "-c:a", "copy", str(output),
    ]
    return cmd


def find_logo() -> Path | None:
    """assets/logo_genshinviet.png cạnh .exe (bản đóng gói) hoặc ở thư mục gốc dự án."""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent)
    dirs.append(Path(__file__).resolve().parents[2])
    for d in dirs:
        p = d / "assets" / LOGO_NAME
        if p.is_file():
            return p
    return None


def logo_size(path: Path) -> tuple[int, int] | None:
    """Đọc kích thước PNG từ header (không cần Pillow)."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except OSError:
        return None
