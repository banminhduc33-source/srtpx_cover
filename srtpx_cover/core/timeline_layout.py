"""Tính toán bố cục timeline (thuần Python, không Qt): xếp phụ đề vào các làn và chọn vạch chia thước."""
from __future__ import annotations

from typing import Sequence

_EPS = 1e-9
# Bước vạch lớn của thước thời gian (giây) để chọn theo mức zoom.
TICK_STEPS = (0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)


def assign_lanes(spans: Sequence[tuple[float, float]]) -> list[int]:
    """Xếp các đoạn (start, end) vào làn: đoạn không chồng nhau dùng chung làn, chồng nhau thì xuống làn dưới.

    Tham lam theo giờ bắt đầu; trả về số làn (từ 0) cho từng đoạn, đúng thứ tự đầu vào.
    """
    order = sorted(range(len(spans)), key=lambda i: (spans[i][0], spans[i][1], i))
    lane_end: list[float] = []
    out = [0] * len(spans)
    for i in order:
        start, end = spans[i]
        for lane, last in enumerate(lane_end):
            if last <= start + _EPS:
                lane_end[lane] = end
                out[i] = lane
                break
        else:
            lane_end.append(end)
            out[i] = len(lane_end) - 1
    return out


def tick_step(pixels_per_second: float, min_px: float = 80.0) -> float:
    """Bước vạch lớn nhỏ nhất (giây) sao cho hai vạch cách nhau ít nhất `min_px` điểm ảnh."""
    pps = max(pixels_per_second, 1e-6)
    for step in TICK_STEPS:
        if step * pps >= min_px:
            return step
    return TICK_STEPS[-1]


def format_tick(t: float, step: float) -> str:
    """Nhãn vạch: m:ss, hoặc m:ss.d khi bước nhỏ hơn 1 giây."""
    t = max(0.0, t)
    if step < 1:
        minutes, seconds = divmod(t, 60)
        return f"{int(minutes)}:{seconds:04.1f}"
    total = int(round(t))
    return f"{total // 60}:{total % 60:02d}"
