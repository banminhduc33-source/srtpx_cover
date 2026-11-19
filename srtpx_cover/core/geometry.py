"""Hình học và nội suy: MỘT nguồn sự thật cho preview và bộ sinh lệnh FFmpeg (không Qt).

Quy ước:
- Hộp #res có mép là toạ độ liên tục; hộp vẽ phủ các điểm ảnh [x1i, x2i) x [y1i, y2i),
  tức drawbox x=x1i, y=y1i, w=x2i-x1i, h=y2i-y1i. Lề an toàn bù cho sai số đo.
- Lề an toàn tính theo điểm ảnh của video THỰC (sau khi scale).
- Thời gian hiển thị là nửa mở: start <= t < end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .model import Box, Subtitle

DEFAULT_MARGIN_PX = 1
MAX_MARGIN_PX = 1.0  # lề tối đa mỗi cạnh; cộng làm tròn ra ngoài (<1px) thì hộp chỉ to hơn chữ gốc dưới 2px
SCALE_MISMATCH_TOLERANCE = 0.01  # lệch tỷ lệ x/y quá 1% thì cảnh báo
_EPS = 1e-9  # tránh ceil(875.0000000001) = 876 do sai số số thực


@dataclass(frozen=True)
class Scale:
    sx: float
    sy: float

    @property
    def is_mismatched(self) -> bool:
        return abs(self.sx / self.sy - 1.0) > SCALE_MISMATCH_TOLERANCE

    def warning(self) -> str | None:
        if not self.is_mismatched:
            return None
        return (
            f"Tỷ lệ scale ngang ({self.sx:.4f}) và dọc ({self.sy:.4f}) lệch nhau quá 1%. "
            "Có thể file .srtpx được đo trên video khác tỷ lệ khung hình."
        )


@dataclass(frozen=True)
class PixelBox:
    """Hộp nguyên theo điểm ảnh video thực, đã làm tròn: phủ [x1, x2) x [y1, y2)."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def w(self) -> int:
        return self.x2 - self.x1

    @property
    def h(self) -> int:
        return self.y2 - self.y1


def compute_scale(res: tuple[int, int] | None, video_size: tuple[int, int]) -> Scale:
    """sx = rộng_thực / rộng_#res, sy = cao_thực / cao_#res. Không có #res thì scale = 1."""
    if res is None:
        return Scale(1.0, 1.0)
    return Scale(video_size[0] / res[0], video_size[1] / res[1])


def is_visible(sub: Subtitle, t: float) -> bool:
    """Nửa mở: start <= t < end (hai block liền nhau không bao giờ chồng nhau)."""
    return sub.start <= t < sub.end


def _pchip_slopes(ts: list[float], vs: list[float]) -> list[float]:
    """Độ dốc tại từng keyframe cho nội suy PCHIP (Fritsch-Carlson): liên tục bậc 1, KHÔNG vọt quá giá trị keyframe."""
    n = len(ts)
    h = [ts[i + 1] - ts[i] for i in range(n - 1)]
    d = [(vs[i + 1] - vs[i]) / h[i] for i in range(n - 1)]
    if n == 2:
        return [d[0], d[0]]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def end(h0: float, h1: float, d0: float, d1: float) -> float:
        e = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if e * d0 <= 0:
            return 0.0
        if d0 * d1 <= 0 and abs(e) > 3 * abs(d0):
            return 3 * d0
        return e

    m[0] = end(h[0], h[1], d[0], d[1])
    m[-1] = end(h[-1], h[-2], d[-1], d[-2])
    return m


def _pchip(ts: list[float], vs: list[float], t: float) -> float:
    ms = _pchip_slopes(ts, vs)
    for i in range(len(ts) - 1):
        if ts[i] <= t <= ts[i + 1]:
            h = ts[i + 1] - ts[i]
            u = (t - ts[i]) / h
            h00, h10 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u
            h01, h11 = -2 * u**3 + 3 * u**2, u**3 - u**2
            return h00 * vs[i] + h10 * h * ms[i] + h01 * vs[i + 1] + h11 * h * ms[i + 1]
    return vs[-1]  # pragma: no cover


def interpolate_box(sub: Subtitle, t: float, smooth: bool = False) -> Box:
    """Hộp (toạ độ #res, chưa scale) tại thời điểm t; nội suy từng giá trị x1,x2,y1,y2.

    smooth=False: tuyến tính giữa hai keyframe (tốc độ đổi đột ngột tại keyframe -> chuyển động "giật").
    smooth=True: PCHIP, tốc độ liên tục qua keyframe, không vọt quá giá trị keyframe (dùng khi xuất video).
    Ngoài khoảng keyframe đầu/cuối thì giữ giá trị keyframe gần nhất.
    """
    kfs = sub.keyframes
    if not kfs:
        assert sub.box is not None, "Phụ đề thiếu cả hộp cố định lẫn keyframe"
        return sub.box
    if t <= kfs[0].time:
        return kfs[0].box
    if t >= kfs[-1].time:
        return kfs[-1].box
    if smooth and len(kfs) >= 2:
        ts = [k.time for k in kfs]
        return Box(
            _pchip(ts, [k.box.x1 for k in kfs], t), _pchip(ts, [k.box.x2 for k in kfs], t),
            _pchip(ts, [k.box.y1 for k in kfs], t), _pchip(ts, [k.box.y2 for k in kfs], t),
        )
    for a, b in zip(kfs, kfs[1:]):
        if a.time <= t <= b.time:
            f = (t - a.time) / (b.time - a.time)
            ba, bb = a.box, b.box
            return Box(
                ba.x1 + (bb.x1 - ba.x1) * f,
                ba.x2 + (bb.x2 - ba.x2) * f,
                ba.y1 + (bb.y1 - ba.y1) * f,
                ba.y2 + (bb.y2 - ba.y2) * f,
            )
    raise AssertionError("không tới được: keyframe đã sắp theo thời gian")  # pragma: no cover


def video_box(
    sub: Subtitle,
    t: float,
    scale: Scale,
    margin: float,
    video_size: tuple[int, int],
    smooth: bool = False,
) -> Box:
    """Hộp theo điểm ảnh video thực (số thực): nội suy -> scale -> cộng lề -> kẹp trong khung hình."""
    margin = min(max(margin, 0.0), MAX_MARGIN_PX)
    b = interpolate_box(sub, t, smooth)
    w, h = video_size
    return Box(
        min(max(b.x1 * scale.sx - margin, 0.0), float(w)),
        min(max(b.x2 * scale.sx + margin, 0.0), float(w)),
        min(max(b.y1 * scale.sy - margin, 0.0), float(h)),
        min(max(b.y2 * scale.sy + margin, 0.0), float(h)),
    )


def pixel_box(
    sub: Subtitle,
    t: float,
    scale: Scale,
    margin: float,
    video_size: tuple[int, int],
) -> PixelBox:
    """Như video_box nhưng làm tròn: x1,y1 xuống; x2,y2 lên (che dư hơn che thiếu)."""
    b = video_box(sub, t, scale, margin, video_size)
    return PixelBox(
        math.floor(b.x1 + _EPS),
        math.floor(b.y1 + _EPS),
        math.ceil(b.x2 - _EPS),
        math.ceil(b.y2 - _EPS),
    )


# ---------------------------------------------------------------- Đa giác (chữ nghiêng)

def expand_polygon(pts: Sequence[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    """Nới đa giác lồi ra ngoài đều d điểm ảnh (mỗi cạnh dời ra d, góc nối bằng giao điểm hai cạnh). d <= 0: giữ nguyên."""
    pts = [(float(x), float(y)) for x, y in pts]
    if d <= 0 or len(pts) < 3:
        return pts
    area2 = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    if abs(area2) < 1e-9:
        return pts
    sign = 1.0 if area2 > 0 else -1.0          # chiều quay; pháp tuyến ngoài = (dy, -dx) khi area2 > 0 (trục y hướng xuống)
    n = len(pts)

    def normal(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        return sign * dy / length, -sign * dx / length

    out: list[tuple[float, float]] = []
    for i in range(n):
        n1 = normal(pts[i - 1], pts[i])
        n2 = normal(pts[i], pts[(i + 1) % n])
        k = 1.0 + n1[0] * n2[0] + n1[1] * n2[1]
        if k < 1e-3:                           # hai cạnh gần như quay đầu: dời theo một pháp tuyến cho khỏi vọt xa
            out.append((pts[i][0] + d * n2[0], pts[i][1] + d * n2[1]))
        else:
            out.append((pts[i][0] + d * (n1[0] + n2[0]) / k, pts[i][1] + d * (n1[1] + n2[1]) / k))
    return out


def _box_corners(b: Box) -> tuple[tuple[float, float], ...]:
    return ((b.x1, b.y1), (b.x2, b.y1), (b.x2, b.y2), (b.x1, b.y2))


def has_keyframe_polygon(sub: Subtitle) -> bool:
    return any(k.polygon is not None for k in sub.keyframes)


def interpolate_polygon(sub: Subtitle, t: float, smooth: bool = False) -> tuple[tuple[float, float], ...] | None:
    """Đa giác (toạ độ #res) tại giờ t: nội suy 8 toạ độ góc giữa các keyframe (keyframe không có đa giác dùng 4 góc của hộp thẳng).
    None nếu không keyframe nào có đa giác. Ngoài khoảng keyframe thì giữ keyframe gần nhất."""
    kfs = sub.keyframes
    if not kfs or not has_keyframe_polygon(sub):
        return None
    polys = [k.polygon if k.polygon is not None else _box_corners(k.box) for k in kfs]
    if t <= kfs[0].time:
        return tuple(polys[0])
    if t >= kfs[-1].time:
        return tuple(polys[-1])
    ts = [k.time for k in kfs]
    if smooth and len(kfs) >= 2:
        return tuple((_pchip(ts, [p[i][0] for p in polys], t), _pchip(ts, [p[i][1] for p in polys], t)) for i in range(4))
    for a, b, pa, pb in zip(kfs, kfs[1:], polys, polys[1:]):
        if a.time <= t <= b.time:
            f = (t - a.time) / (b.time - a.time)
            return tuple((pa[i][0] + (pb[i][0] - pa[i][0]) * f, pa[i][1] + (pb[i][1] - pa[i][1]) * f) for i in range(4))
    raise AssertionError("không tới được: keyframe đã sắp theo thời gian")  # pragma: no cover


def video_polygon(
    sub: Subtitle,
    scale: Scale,
    margin: float,
    video_size: tuple[int, int] | None = None,
    t: float | None = None,
    smooth: bool = False,
) -> list[tuple[float, float]] | None:
    """Đa giác che (số thực, điểm ảnh video thực): scale từ #res rồi nới ra `margin`. None nếu phụ đề không có đa giác.

    Hộp cố định: đa giác của phụ đề. Có keyframe và `t`: đa giác nội suy tại t (xem interpolate_polygon).
    Không kẹp vào khung hình: phần ngoài khung bị cắt tự nhiên khi vẽ lên video."""
    poly = sub.polygon
    if poly is None and t is not None:
        poly = interpolate_polygon(sub, t, smooth)
    if poly is None:
        return None
    margin = min(max(margin, 0.0), MAX_MARGIN_PX)
    return expand_polygon([(x * scale.sx, y * scale.sy) for x, y in poly], margin)


def polygon_bounds(pts: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    """(x1, x2, y1, y2) hộp bao thẳng của đa giác."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


@dataclass(frozen=True)
class PolygonFrame:
    """Khung chữ của một đa giác 4 góc (thứ tự trái-trên, phải-trên, phải-dưới, trái-dưới): chữ đặt ở tâm, xoay theo `angle`."""

    cx: float
    cy: float
    w: float        # độ dài cạnh ngang (trung bình cạnh trên và cạnh dưới)
    h: float        # độ dài cạnh dọc (trung bình cạnh trái và cạnh phải)
    angle: float    # radian, theo chiều kim đồng hồ trên màn hình (y hướng xuống), trong (-pi/2, pi/2]


def polygon_frame(pts: Sequence[tuple[float, float]]) -> PolygonFrame:
    """Tâm, cỡ và góc nghiêng của đa giác che: chữ nghiêng thì chữ trắng phải xoay cùng góc đó và co vừa CHÍNH đa giác (không phải
    hộp bao thẳng, vốn rộng hơn nhiều khi nghiêng). Góc lấy từ hai cạnh ngang cộng lại, đưa về (-90, 90] độ để chữ không bị lộn ngược."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    dx, dy = (x1 - x0) + (x2 - x3), (y1 - y0) + (y2 - y3)
    angle = math.atan2(dy, dx) if (dx or dy) else 0.0
    if angle > math.pi / 2 + 1e-9:
        angle -= math.pi
    elif angle <= -math.pi / 2 + 1e-9:
        angle += math.pi
    return PolygonFrame(
        (x0 + x1 + x2 + x3) / 4, (y0 + y1 + y2 + y3) / 4,
        (math.hypot(x1 - x0, y1 - y0) + math.hypot(x2 - x3, y2 - y3)) / 2,
        (math.hypot(x3 - x0, y3 - y0) + math.hypot(x2 - x1, y2 - y1)) / 2,
        angle,
    )


# ---------------------------------------------------------------- Khung hình (mốc 2)

def frame_time(k: int, fps_num: int, fps_den: int) -> float:
    """Giờ (giây) của khung hình thứ k (đếm từ 0) với fps = fps_num/fps_den."""
    return k * fps_den / fps_num


def visible_frame_range(sub: Subtitle, fps_num: int, fps_den: int) -> tuple[int, int] | None:
    """Khoảng chỉ số khung (đầu, cuối, gồm cả hai đầu) mà phụ đề hiển thị; None nếu không khung nào.

    Khung k hiển thị khi start <= giờ(k) < end (nửa mở, khớp is_visible). Sai số số thực 1e-9
    được bỏ qua để mốc như 10.2s không bị lệch một khung.
    """
    def first_at_or_after(t: float) -> int:
        k = max(0, math.ceil(t * fps_num / fps_den - 1e-6))
        while k > 0 and frame_time(k - 1, fps_num, fps_den) >= t - 1e-9:
            k -= 1
        while frame_time(k, fps_num, fps_den) < t - 1e-9:
            k += 1
        return k

    first = first_at_or_after(sub.start)
    last = first_at_or_after(sub.end) - 1  # khung cuối cùng có giờ < end
    return (first, last) if last >= first else None


def smallest_pixel_size(
    sub: Subtitle, scale: Scale, margin: float, video_size: tuple[int, int]
) -> tuple[int, int]:
    """(rộng, cao) nhỏ nhất của hộp vẽ trong các keyframe (hoặc hộp cố định); dùng để cỡ chữ cố định."""
    times = [k.time for k in sub.keyframes] or [sub.start]
    boxes = [pixel_box(sub, t, scale, margin, video_size) for t in times]
    return min(b.w for b in boxes), min(b.h for b in boxes)
