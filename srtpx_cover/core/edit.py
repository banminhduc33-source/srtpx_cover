"""Các phép sửa phụ đề thuần Python (không Qt): kéo hộp, sao chép, đánh số lại, đọc giờ gõ tay."""
from __future__ import annotations

import re
from typing import Sequence

from .model import Box, Keyframe, Subtitle

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
MIN_BOX_SIZE = 1.0  # độ rộng/cao tối thiểu của hộp (theo #res)
DEFAULT_NEW_DURATION = 2.0
DEFAULT_NEW_TEXT = "Phụ đề mới"

_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")
_SECONDS_RE = re.compile(r"^\d+(?:\.\d+)?$")


def clone_subtitle(sub: Subtitle) -> Subtitle:
    """Bản sao độc lập (Box/Keyframe bất biến nên chỉ cần sao danh sách keyframe)."""
    return Subtitle(sub.index, sub.start, sub.end, sub.text, sub.box, list(sub.keyframes), polygon=sub.polygon)


def clone_subtitles(subs: Sequence[Subtitle]) -> list[Subtitle]:
    return [clone_subtitle(s) for s in subs]


def renumber(subs: Sequence[Subtitle]) -> None:
    """Đánh lại số thứ tự 1..n theo vị trí trong danh sách."""
    for i, s in enumerate(subs, start=1):
        s.index = i


def parse_time_text(text: str) -> float | None:
    """Đọc giờ gõ tay: 'hh:mm:ss,mmm', 'mm:ss.mmm' hoặc số giây. Sai định dạng -> None."""
    s = text.strip().replace(",", ".")
    if _SECONDS_RE.match(s):
        return float(s)
    m = _CLOCK_RE.match(s)
    if not m:
        return None
    h, mi, sec = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + float(sec)


def valid_times(start: float, end: float) -> bool:
    return start >= 0 and end > start


def default_box(res_size: tuple[float, float]) -> Box:
    """Hộp mặc định cho phụ đề mới: dải ngang ở khoảng 1/5 dưới khung hình."""
    w, h = res_size
    return Box(round(w * 0.3, 1), round(w * 0.7, 1), round(h * 0.82, 1), round(h * 0.9, 1))


def insert_position(subs: Sequence[Subtitle], t: float) -> int:
    """Vị trí chèn để danh sách vẫn sắp theo giờ bắt đầu: sau mọi phụ đề có start <= t."""
    return sum(1 for s in subs if s.start <= t)


def apply_drag(box: Box, mode: str, dx: float, dy: float, res_size: tuple[float, float],
               min_size: float = MIN_BOX_SIZE) -> Box:
    """Hộp mới sau khi kéo (dx, dy theo #res). mode: 'move' hoặc một trong HANDLES.

    Kẹp trong khung [0, W] x [0, H]; đổi kích thước không cho nhỏ hơn min_size.
    """
    W, H = res_size
    x1, x2, y1, y2 = box.x1, box.x2, box.y1, box.y2
    if mode == "move":
        dx = min(max(dx, -x1), W - x2)
        dy = min(max(dy, -y1), H - y2)
        x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
    else:
        if "w" in mode:
            x1 = min(max(x1 + dx, 0.0), x2 - min_size)
        if "e" in mode:
            x2 = max(min(x2 + dx, W), x1 + min_size)
        if "n" in mode:
            y1 = min(max(y1 + dy, 0.0), y2 - min_size)
        if "s" in mode:
            y2 = max(min(y2 + dy, H), y1 + min_size)
    return Box(round(x1, 1), round(x2, 1), round(y1, 1), round(y2, 1))


# ---------------------------------------------------------------- Keyframe (mốc 5)
KF_EPS = 1e-3  # hai giờ cách nhau dưới 1 ms coi là cùng một keyframe
KF_GAP = 1e-3  # khoảng cách tối thiểu giữa hai keyframe khi kéo chấm


def find_keyframe(sub: Subtitle, t: float, eps: float = KF_EPS) -> int | None:
    """Chỉ số keyframe có giờ trùng t (sai số eps); None nếu không có."""
    for i, k in enumerate(sub.keyframes):
        if abs(k.time - t) <= eps:
            return i
    return None


def map_polygon(polygon: Sequence[tuple[float, float]], old: Box, new: Box) -> tuple[tuple[float, float], ...]:
    """Đa giác sau khi hộp đi kèm đổi từ `old` sang `new`: mỗi toạ độ được dời/co giãn theo đúng phép biến đổi hộp."""
    sx = new.width / old.width if old.width > 1e-9 else 1.0
    sy = new.height / old.height if old.height > 1e-9 else 1.0
    return tuple((round(new.x1 + (x - old.x1) * sx, 3), round(new.y1 + (y - old.y1) * sy, 3)) for x, y in polygon)


def set_box_at(sub: Subtitle, t: float, box: Box) -> None:
    """Đặt hộp tại giờ t (dùng khi kéo hộp trên preview).

    Chưa có keyframe: sửa hộp cố định. Đã có keyframe: sửa keyframe tại t, nếu chưa có thì tạo mới.
    """
    if not sub.keyframes:
        if sub.polygon is not None and sub.box is not None:
            sub.polygon = map_polygon(sub.polygon, sub.box, box)   # đa giác đi theo hộp (dời / co giãn)
        sub.box = box
        return
    i = find_keyframe(sub, t)
    if i is not None:
        old = sub.keyframes[i]
        poly = map_polygon(old.polygon, old.box, box) if old.polygon is not None else None   # đa giác đi theo hộp
        sub.keyframes[i] = Keyframe(old.time, box, poly)
        return
    sub.keyframes.append(Keyframe(t, box))
    sub.keyframes.sort(key=lambda k: k.time)


def add_keyframe(sub: Subtitle, t: float, box: Box) -> int:
    """Thêm keyframe tại t với hộp `box` (phụ đề hộp cố định sẽ chuyển sang dạng keyframe). Trả về chỉ số."""
    if not sub.keyframes:
        sub.box = None
        sub.polygon = None          # keyframe và đa giác không đi cùng nhau
        sub.keyframes = [Keyframe(t, box)]
        return 0
    set_box_at(sub, t, box)
    i = find_keyframe(sub, t)
    assert i is not None
    return i


def move_keyframe(sub: Subtitle, i: int, new_time: float, duration: float | None = None) -> float:
    """Đổi giờ keyframe i; giờ bị kẹp giữa hai keyframe kề bên (thứ tự không đổi) và trong [0, duration]."""
    kfs = sub.keyframes
    lo = kfs[i - 1].time + KF_GAP if i > 0 else 0.0
    hi = kfs[i + 1].time - KF_GAP if i + 1 < len(kfs) else (duration if duration is not None else float("inf"))
    t = round(min(max(new_time, lo), max(lo, hi)), 3)
    kfs[i] = Keyframe(t, kfs[i].box)
    return t


def delete_keyframe(sub: Subtitle, i: int) -> None:
    """Xóa keyframe i. Xóa keyframe cuối cùng thì chuyển về hộp cố định (giữ hộp của keyframe đó)."""
    if len(sub.keyframes) == 1:
        sub.box = sub.keyframes[0].box
        sub.keyframes = []
    else:
        del sub.keyframes[i]


def copy_keyframes(src: Subtitle, dst: Subtitle) -> bool:
    """Chép keyframe của src sang dst (thay keyframe/hộp cũ của dst).

    Giờ keyframe dời theo chênh lệch giờ bắt đầu (dst.start - src.start) để chuyển động giữ nguyên
    so với đầu phụ đề; giờ âm được kẹp về 0, giờ trùng nhau thì giữ cái đầu. False nếu src không có keyframe.
    """
    if not src.keyframes:
        return False
    off = dst.start - src.start
    out: list[Keyframe] = []
    for k in src.keyframes:
        t = round(max(0.0, k.time + off), 3)
        if out and t <= out[-1].time:
            continue
        out.append(Keyframe(t, k.box, k.polygon))
    dst.box = None
    dst.polygon = None
    dst.keyframes = out
    return True
