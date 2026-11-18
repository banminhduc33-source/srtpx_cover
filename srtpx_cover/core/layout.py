"""Co chữ vừa hộp (thuần Python, không Qt). Dùng chung cho preview và xuất.

Quy tắc: KHÔNG tự xuống dòng. Chỉ giữ nguyên các dòng có sẵn trong chữ gốc, đo chiều rộng
từng dòng bằng px (cỡ chữ x độ rộng glyph) rồi giảm cỡ chữ từng 1px cho tới khi mọi dòng
nằm gọn trong hộp thì dừng. Không có cỡ chữ tối thiểu.

Có keyframe: chữ dính vào hộp (`TextPlan`): cỡ chữ là hàm liên tục của kích thước hộp tại giờ t, nên hộp
phóng to/thu nhỏ thì chữ to/nhỏ theo mượt mà (xuất ffmpeg dùng biểu thức fontsize theo t).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .fonts import LINE_HEIGHT_FACTOR, FontChoice, FontData, choose_font, fold_for_font, text_width
from .geometry import Scale, smallest_pixel_size
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
    text = fold_for_font(sub.text, chain)
    choice = choose_font(text, chain)
    bw, bh = smallest_pixel_size(sub, scale, margin, video_size)
    return choice, fit_text(text, choice.font, bw, bh)


@dataclass(frozen=True)
class TextPlan:
    """Chữ DÍNH vào hộp: giữ nguyên các dòng gốc, cỡ chữ là hàm LIÊN TỤC (số thực) của kích thước hộp.

    Chiều rộng dòng tỷ lệ thuận với cỡ chữ nên cỡ lớn nhất vừa hộp tính được bằng phép chia, không cần
    thử từng px. Hộp to ra / nhỏ đi bao nhiêu thì chữ to ra / nhỏ đi bấy nhiêu, mượt, không nhảy nấc.
    """

    lines: tuple[str, ...]
    em_width: float            # chiều rộng (px) của dòng dài nhất ở cỡ chữ 1px (đã gồm hệ số an toàn)
    fixed_size: float | None = None   # cỡ chữ cố định (px video thực); có thì bỏ qua việc co chữ theo hộp

    @property
    def n(self) -> int:
        return len(self.lines)

    def size_for(self, box_w: float, box_h: float) -> float:
        """Cỡ chữ lớn nhất (px, số thực, tối thiểu 1) để mọi dòng vừa hộp box_w x box_h.
        Có `fixed_size` thì luôn trả về đúng cỡ đó."""
        if self.fixed_size is not None:
            return self.fixed_size
        s = box_h / (LINE_HEIGHT_FACTOR * self.n)
        if self.em_width > 0:
            s = min(s, box_w / self.em_width)
        return max(1.0, s)

    def fits_at(self, box_w: float, box_h: float) -> bool:
        """False nếu ngay cả ở 1px chữ vẫn tràn hộp (cỡ cố định thì: chữ ở đúng cỡ đó có tràn hộp không)."""
        k = 1.0 if self.fixed_size is None else self.fixed_size
        return (LINE_HEIGHT_FACTOR * self.n * k <= box_h) and (self.em_width * k <= box_w)


def plan_text(sub: Subtitle, chain: Sequence[FontData], scale_y: float = 1.0) -> tuple[FontChoice, TextPlan]:
    """Chọn font và tách dòng cho MỘT phụ đề. Cố định suốt thời gian hiển thị.

    `scale_y` (px video thực / toạ độ #res theo chiều dọc) đổi `sub.font_px` sang px của video thực."""
    text = fold_for_font(sub.text, chain)
    choice = choose_font(text, chain)
    lines = tuple(_normalize_lines(text))
    em = max((text_width(choice.font, ln, 1.0) for ln in lines), default=0.0)
    fixed = None if sub.font_px is None else sub.font_px * scale_y
    return choice, TextPlan(lines, em, fixed)
