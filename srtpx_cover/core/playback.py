"""Tính toán thời gian/khung hình cho phát video và tua (thuần Python, không Qt).

QMediaPlayer chỉ tua theo mili giây và không chính xác từng khung (chấp nhận sai ±1 khung ở preview),
nên mọi phép đổi giữa giờ <-> chỉ số khung nằm ở đây để test độc lập.
"""
from __future__ import annotations

import math

from .geometry import frame_time

# Dung sai khi đổi giờ -> chỉ số khung. Backend phát có thể báo lệch dưới 1 ms so với giờ đã đặt;
# 2 ms rất nhỏ so với độ dài một khung (>= 8 ms ở 120 fps) nên không làm nhảy sang khung kế.
FRAME_TOLERANCE_S = 0.002


def format_clock(t: float) -> str:
    """Giây -> 'MM:SS.mmm' (hoặc 'H:MM:SS.mmm' từ 1 giờ trở lên); làm tròn mili giây, không âm."""
    ms_total = max(0, round(t * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def frame_at(t: float, fps_num: int, fps_den: int) -> int:
    """Chỉ số khung đang hiển thị tại giờ t (khung k hiển thị trong [giờ(k), giờ(k+1)))."""
    return max(0, math.floor((t + FRAME_TOLERANCE_S) * fps_num / fps_den))


def last_frame_index(duration: float, fps_num: int, fps_den: int) -> int:
    """Chỉ số khung cuối cùng (>= 0) của video dài `duration` giây."""
    return max(0, math.ceil(duration * fps_num / fps_den - 1e-6) - 1)


def position_ms_for_frame(k: int, fps_num: int, fps_den: int) -> int:
    """Vị trí (ms, số nguyên) để đặt cho QMediaPlayer khi muốn tới khung k: làm tròn LÊN để không rơi vào khung trước."""
    return max(0, math.ceil(frame_time(k, fps_num, fps_den) * 1000 - 1e-6))


def clamp_frame(k: int, duration: float, fps_num: int, fps_den: int) -> int:
    return min(max(0, k), last_frame_index(duration, fps_num, fps_den))
