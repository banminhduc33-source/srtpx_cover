"""Co chữ vừa hộp (thuần Python, không Qt). Dùng chung cho preview và xuất.

Quy tắc: phụ đề thường KHÔNG tự xuống dòng. Chỉ giữ nguyên các dòng có sẵn trong chữ gốc, đo chiều rộng
từng dòng bằng px (cỡ chữ x độ rộng glyph) rồi giảm cỡ chữ từng 1px cho tới khi mọi dòng
nằm gọn trong hộp thì dừng. Không có cỡ chữ tối thiểu.

Ngoại lệ: Ô CHE LỚN (hộp cao >= WRAP_MIN_HEIGHT_RATIO chiều cao video, vd bảng chữ nhiều dòng) mà chữ chỉ có
MỘT dòng thì tự xuống dòng theo từ để chữ dàn đầy hộp (xem `wrap_to_box`).

Có keyframe: chữ dính vào hộp (`TextPlan`): cỡ chữ là hàm liên tục của kích thước hộp tại giờ t, nên hộp
phóng to/thu nhỏ thì chữ to/nhỏ theo mượt mà (xuất ffmpeg dùng biểu thức fontsize theo t).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .fonts import LINE_HEIGHT_FACTOR, FontChoice, FontData, choose_font, text_width
from .geometry import Scale, smallest_pixel_size, video_box
from .model import Subtitle


@dataclass(frozen=True)
class TextLayout:
    size: int                 # cỡ chữ (px)
    lines: tuple[str, ...]    # các dòng sẽ vẽ, căn giữa từng dòng
    fits: bool                # False: ngay cả ở 1px vẫn tràn hộp (gần như không xảy ra)

    @property
    def line_height(self) -> float:
        return LINE_HEIGHT_FACTOR * self.size

    @property
    def block_height(self) -> float:
        return self.line_height * len(self.lines)


def _normalize_lines(text: str) -> list[str]:
    """Tách dòng gốc, bỏ khoảng trắng đầu/cuối từng dòng (để căn giữa chính xác)."""
    return [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _fits(lines: list[str], font: FontData, size: float, box_w: float, box_h: float) -> bool:
    if len(lines) * LINE_HEIGHT_FACTOR * size > box_h:
        return False
    return all(text_width(font, ln, size) <= box_w for ln in lines)


def fit_text(text: str, font: FontData, box_w: float, box_h: float) -> TextLayout:
    """Cỡ chữ lớn nhất (số nguyên px, tối thiểu 1) để mọi dòng vừa hộp box_w x box_h, không xuống dòng."""
    lines = _normalize_lines(text)
    # Chặn trên: không thể cao hơn chiều cao hộp chia số dòng (đã tính hệ số 1.2).
    top = max(1, int(box_h / (LINE_HEIGHT_FACTOR * len(lines))))
    for size in range(top, 0, -1):
        if _fits(lines, font, size, box_w, box_h):
            return TextLayout(size, tuple(lines), True)
    return TextLayout(1, tuple(lines), False)


def plan_subtitle_text(
    sub: Subtitle,
    chain: Sequence[FontData],
    scale: Scale,
    margin: float,
    video_size: tuple[int, int],
) -> tuple[FontChoice, TextLayout]:
    """Chọn font rồi co chữ theo hộp NHỎ NHẤT trong các keyframe.

    Chỉ còn dùng để cảnh báo "hộp quá nhỏ"; cỡ chữ thực tế vẽ ra tính theo từng thời điểm
    bằng `layout_at` (hộp phóng to thì chữ phóng to theo).
    """
    choice = choose_font(sub.text, chain)
    bw, bh = smallest_pixel_size(sub, scale, margin, video_size)
    return choice, fit_text(sub.text, choice.font, bw, bh)


# ---------------------------------------------------------------- Tự xuống dòng cho ô che lớn

WRAP_MIN_HEIGHT_RATIO = 0.12   # hộp cao >= 12% chiều cao video mới coi là ô che lớn (phụ đề thường thấp hơn nhiều)
WRAP_MIN_GAIN = 1.15           # chỉ xuống dòng nếu chữ to hơn ít nhất 15% so với để một dòng
WRAP_MAX_LINES = 12

_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_TOKEN_RE = re.compile(rf"[{_CJK}]|[^\s{_CJK}]+")   # mỗi chữ Hán là 1 mảnh; chữ khác tách theo khoảng trắng


def _tokens(text: str) -> list[tuple[str, bool]]:
    """[(mảnh, có_khoảng_trắng_trước)]: chữ Hán ngắt được giữa mọi chữ, còn lại chỉ ngắt ở khoảng trắng."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        out.append((m.group(), m.start() > pos and text[pos:m.start()].isspace()))
        pos = m.end()
    return out


def _greedy(tokens: list[tuple[str, bool]], widths: list[float], space: float, limit: float) -> list[list[int]]:
    """Xếp mảnh vào dòng, dòng nào vượt `limit` (em) thì sang dòng mới. Trả về chỉ số mảnh từng dòng."""
    lines: list[list[int]] = []
    cur: list[int] = []
    w = 0.0
    for i, (_, sp) in enumerate(tokens):
        add = widths[i] + (space if (cur and sp) else 0.0)
        if cur and w + add > limit:
            lines.append(cur)
            cur, w = [i], widths[i]
        else:
            cur.append(i)
            w += add
    if cur:
        lines.append(cur)
    return lines


def wrap_to_box(text: str, font: FontData, box_w: float, box_h: float) -> list[str]:
    """Xuống dòng (theo từ / từng chữ Hán) thành n dòng sao cho cỡ chữ cuối cùng LỚN NHẤT trong hộp box_w x box_h.

    Trả về [text] (không xuống dòng) nếu để một dòng đã là tốt nhất hoặc lợi dưới WRAP_MIN_GAIN."""
    flat = " ".join(text.split())
    toks = _tokens(flat)
    if len(toks) < 2 or box_w <= 0 or box_h <= 0:
        return [flat or text.strip()]
    widths = [text_width(font, t, 1.0) for t, _ in toks]
    space = text_width(font, " ", 1.0)

    def render(groups: list[list[int]]) -> list[str]:
        res = []
        for g in groups:
            s = ""
            for k, i in enumerate(g):
                s += (" " if (k and toks[i][1]) else "") + toks[i][0]
            res.append(s)
        return res

    def size_of(lines: list[str]) -> float:
        em = max((text_width(font, ln, 1.0) for ln in lines), default=0.0)
        s = box_h / (LINE_HEIGHT_FACTOR * len(lines))
        return min(s, box_w / em) if em > 0 else s

    single = [flat]
    best_lines, best_size = single, size_of(single)
    base_size = best_size
    lo0 = max(widths)
    hi0 = sum(widths) + space * len(toks)
    for n in range(2, min(len(toks), WRAP_MAX_LINES) + 1):
        lo, hi = lo0, hi0               # giới hạn nhỏ nhất mà xếp được <= n dòng (tìm nhị phân)
        for _ in range(40):
            mid = (lo + hi) / 2
            if len(_greedy(toks, widths, space, mid)) <= n:
                hi = mid
            else:
                lo = mid
        lines = render(_greedy(toks, widths, space, hi))
        sz = size_of(lines)
        if sz > best_size * 1.0001:
            best_lines, best_size = lines, sz
    if best_lines is not single and best_size >= WRAP_MIN_GAIN * base_size:
        return best_lines
    return single


def wrap_reference(
    sub: Subtitle, scale: Scale, margin: float, video_size: tuple[int, int]
) -> tuple[float, float]:
    """(rộng, cao) px video của hộp LỚN NHẤT (theo diện tích) trong các keyframe / hộp cố định."""
    times = [k.time for k in sub.keyframes] or [sub.start]
    boxes = [video_box(sub, t, scale, margin, video_size) for t in times]
    b = max(boxes, key=lambda x: (x.x2 - x.x1) * (x.y2 - x.y1))
    return b.x2 - b.x1, b.y2 - b.y1


@dataclass(frozen=True)
class TextPlan:
    """Chữ DÍNH vào hộp: giữ nguyên các dòng gốc, cỡ chữ là hàm LIÊN TỤC (số thực) của kích thước hộp.

    Chiều rộng dòng tỷ lệ thuận với cỡ chữ nên cỡ lớn nhất vừa hộp tính được bằng phép chia, không cần
    thử từng px. Hộp to ra / nhỏ đi bao nhiêu thì chữ to ra / nhỏ đi bấy nhiêu, mượt, không nhảy nấc.
    """

    lines: tuple[str, ...]
    em_width: float            # chiều rộng (px) của dòng dài nhất ở cỡ chữ 1px (đã gồm hệ số an toàn)

    @property
    def n(self) -> int:
        return len(self.lines)

    def size_for(self, box_w: float, box_h: float) -> float:
        """Cỡ chữ lớn nhất (px, số thực, tối thiểu 1) để mọi dòng vừa hộp box_w x box_h."""
        s = box_h / (LINE_HEIGHT_FACTOR * self.n)
        if self.em_width > 0:
            s = min(s, box_w / self.em_width)
        return max(1.0, s)

    def fits_at(self, box_w: float, box_h: float) -> bool:
        """False nếu ngay cả ở 1px chữ vẫn tràn hộp."""
        return (LINE_HEIGHT_FACTOR * self.n <= box_h) and (self.em_width <= box_w)


def plan_text(
    sub: Subtitle,
    chain: Sequence[FontData],
    scale: Scale | None = None,
    margin: float = 0.0,
    video_size: tuple[int, int] | None = None,
) -> tuple[FontChoice, TextPlan]:
    """Chọn font và tách dòng cho MỘT phụ đề. Cố định suốt thời gian hiển thị.

    Nếu truyền `scale` và `video_size`: ô che lớn (cao >= WRAP_MIN_HEIGHT_RATIO chiều cao video) mà chữ chỉ
    có một dòng thì xuống dòng cho vừa hộp lớn nhất (các dòng giữ nguyên khi hộp co giãn theo keyframe)."""
    choice = choose_font(sub.text, chain)
    lines = tuple(_normalize_lines(sub.text))
    if scale is not None and video_size is not None and len(lines) == 1 and lines[0]:
        bw, bh = wrap_reference(sub, scale, margin, video_size)
        if bh >= WRAP_MIN_HEIGHT_RATIO * video_size[1]:
            lines = tuple(wrap_to_box(lines[0], choice.font, bw, bh))
    em = max((text_width(choice.font, ln, 1.0) for ln in lines), default=0.0)
    return choice, TextPlan(lines, em)
