"""Màu hộp che của phụ đề phụ = màu nền quanh hộp (thay cho hộp đen). Thuần Python + ffmpeg, không Qt.

Phụ đề chính (nhóm cùng vị trí đông nhất, xem maincap.py) vẫn dùng hộp đen. Mỗi phụ đề phụ thì:
1. Lấy khung hình video tại giữa khoảng hiển thị của nó (ffmpeg, chỉ cắt đúng vùng cần).
2. Lấy các điểm ảnh trong một vành đai RING_PX điểm ảnh NGAY NGOÀI hộp che (bên trong hộp là chữ gốc nên không dùng).
3. Màu hộp = trung vị từng kênh R, G, B của vành đai (trung vị ít bị kéo lệch bởi viền chữ hoặc nhiễu nén).

Mỗi phụ đề chỉ một màu phẳng: nền chuyển sắc hoặc nền có hoa văn vẫn chỉ che bằng màu trung vị.
Không lấy được màu (lỗi ffmpeg, hộp nằm ngoài khung, vành đai rỗng) thì phụ đề đó giữ hộp đen và có cảnh báo.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from statistics import median_low
from typing import Callable

from PIL import Image

from .ffmpeg_tools import FfmpegCancelled, FfmpegError, VideoInfo, _creationflags
from .geometry import compute_scale, has_keyframe_polygon, pixel_box, polygon_bounds, video_polygon
from .maincap import main_group_indices
from .model import SrtpxDocument, Subtitle

RGB = tuple[int, int, int]
RING_PX = 4                       # bề dày vành đai lấy màu, ngay ngoài hộp che
MAX_WARNINGS = 5                  # tối đa số cảnh báo lỗi từng phụ đề (còn lại gộp thành một dòng)


def secondary_indices(doc: SrtpxDocument) -> set[int] | None:
    """`Subtitle.index` của các phụ đề phụ (không thuộc nhóm phụ đề chính); None nếu không tìm được nhóm chính."""
    main = main_group_indices(doc)
    if main is None:
        return None
    return {s.index for s in doc.subtitles if s.index not in main}


def hex_color(rgb: RGB) -> str:
    """Màu cho filter ffmpeg: 0xRRGGBB."""
    return "0x%02x%02x%02x" % rgb


def sample_rect(sub: Subtitle, t: float, scale, margin: float, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Hình chữ nhật (x1, y1, x2, y2, điểm ảnh video) mà hộp che phủ tại giờ t; None nếu rỗng (nằm ngoài khung hình).

    Phụ đề có đa giác (chữ nghiêng) thì lấy hộp bao của hộp thẳng và đa giác."""
    b = pixel_box(sub, t, scale, margin, size)
    x1, y1, x2, y2 = b.x1, b.y1, b.x2, b.y2
    poly = video_polygon(sub, scale, margin, size, t) if (sub.polygon is not None or has_keyframe_polygon(sub)) else None
    if poly:
        px1, px2, py1, py2 = polygon_bounds(poly)
        x1, y1 = min(x1, int(px1 // 1)), min(y1, int(py1 // 1))
        x2, y2 = max(x2, -int(-px2 // 1)), max(y2, -int(-py2 // 1))
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(size[0], x2), min(size[1], y2)
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def ring_color(img: Image.Image, inner: tuple[int, int, int, int]) -> RGB | None:
    """Trung vị màu các điểm ảnh của `img` nằm ngoài hình chữ nhật `inner` (x1, y1, x2, y2 theo toạ độ ảnh); None nếu rỗng."""
    w, h = img.size
    ix1, iy1, ix2, iy2 = inner
    strips = ((0, 0, w, iy1), (0, iy2, w, h), (0, iy1, ix1, iy2), (ix2, iy1, w, iy2))
    rs: list[int] = []
    gs: list[int] = []
    bs: list[int] = []
    for a, b, c, d in strips:
        if c <= a or d <= b:
            continue
        raw = img.crop((a, b, c, d)).tobytes()     # RGB xen kẽ
        rs.extend(raw[0::3])
        gs.extend(raw[1::3])
        bs.extend(raw[2::3])
    if not rs:
        return None
    return int(median_low(rs)), int(median_low(gs)), int(median_low(bs))


def _grab(ffmpeg: str, video: str | Path, t: float, rect: tuple[int, int, int, int]) -> Image.Image:
    """Cắt vùng `rect` của khung hình tại giờ t thành ảnh RGB."""
    x1, y1, x2, y2 = rect
    w, h = x2 - x1, y2 - y1
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
           "-frames:v", "1", "-vf", f"format=rgb24,crop={w}:{h}:{x1}:{y1}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=_creationflags())
    except OSError as exc:
        raise FfmpegError(f"Không chạy được ffmpeg: {exc}") from exc
    if p.returncode != 0 or len(p.stdout) != w * h * 3:
        raise FfmpegError(p.stderr.decode("utf-8", "replace").strip()[-200:] or "không lấy được khung hình")
    return Image.frombytes("RGB", (w, h), p.stdout)


def sample_secondary_colors(
    doc: SrtpxDocument,
    video_path: str | Path,
    video: VideoInfo,
    margin: float,
    ffmpeg: str,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[dict[int, RGB], list[str]]:
    """({Subtitle.index: màu hộp} cho các phụ đề phụ lấy được màu, cảnh báo).

    `margin` là lề an toàn của lần xuất (hộp che đã nới ra chừng đó), để vành đai nằm ngoài hộp thật sự được vẽ.
    Không có phụ đề phụ hoặc không có nhóm chính thì trả về ({}, [])."""
    sec = secondary_indices(doc)
    if not sec:
        return {}, []
    scale = compute_scale(doc.res, video.size)
    subs = [s for s in doc.subtitles if s.index in sec]
    colors: dict[int, RGB] = {}
    errors: list[str] = []
    for i, sub in enumerate(subs):
        if cancel is not None and cancel.is_set():
            raise FfmpegCancelled("Đã hủy.")
        t = min(max(0.0, (sub.start + sub.end) / 2), max(0.0, video.duration - 0.05))
        rect = sample_rect(sub, t, scale, margin, video.size)
        if rect is not None:
            x1, y1, x2, y2 = rect
            ex1, ey1 = max(0, x1 - RING_PX), max(0, y1 - RING_PX)
            ex2, ey2 = min(video.width, x2 + RING_PX), min(video.height, y2 + RING_PX)
            try:
                img = _grab(ffmpeg, video_path, t, (ex1, ey1, ex2, ey2))
            except FfmpegError as exc:
                errors.append(f"#{sub.index}: {exc}")
            else:
                col = ring_color(img, (x1 - ex1, y1 - ey1, x2 - ex1, y2 - ey1))
                if col is not None:
                    colors[sub.index] = col
        if on_progress:
            on_progress((i + 1) / len(subs))
    warnings: list[str] = []
    if errors:
        warnings = [f"Không lấy được màu nền cho phụ đề {e} (giữ hộp đen)." for e in errors[:MAX_WARNINGS]]
        if len(errors) > MAX_WARNINGS:
            warnings.append(f"... và {len(errors) - MAX_WARNINGS} phụ đề phụ khác không lấy được màu nền.")
    return colors, warnings
