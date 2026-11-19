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
    fixed_size: float | None = None   # cỡ chữ TỐI ĐA (px video thực); vẫn co theo hộp nếu hộp nhỏ hơn không đủ chứa

    @property
    def n(self) -> int:
        return len(self.lines)

    def size_for(self, box_w: float, box_h: float) -> float:
        """Cỡ chữ lớn nhất (px, số thực, tối thiểu 1) để mọi dòng vừa hộp box_w x box_h.
        Có `fixed_size` thì không vượt quá cỡ đó (hộp rộng thì đúng cỡ đó, hộp nhỏ thì co theo hộp)."""
        s = box_h / (LINE_HEIGHT_FACTOR * self.n)
        if self.em_width > 0:
            s = min(s, box_w / self.em_width)
        if self.fixed_size is not None:
            s = min(s, self.fixed_size)
        return max(1.0, s)

    def fits_at(self, box_w: float, box_h: float) -> bool:
        """False nếu ngay cả ở 1px chữ vẫn tràn hộp."""
        return (LINE_HEIGHT_FACTOR * self.n <= box_h) and (self.em_width <= box_w)


MAX_WRAP_LINES = 12


def wrap_lines(line: str, font: FontData, box_w: float, box_h: float, max_lines: int = MAX_WRAP_LINES) -> tuple[str, ...]:
    """Ngắt MỘT dòng chữ thành nhiều dòng (chỉ ngắt ở khoảng trắng) để cỡ chữ vừa hộp box_w x box_h là LỚN NHẤT.

    Thử từng số dòng k = 1..max_lines: với mỗi k chia các chữ thành k dòng sao cho dòng rộng nhất hẹp nhất (hòa: các dòng đều
    nhau hơn), cỡ chữ = min(box_h / (1,2 x k), box_w / độ rộng dòng rộng nhất). Chọn k cho cỡ chữ lớn nhất; hòa thì ít dòng hơn.
    Một chữ duy nhất không ngắt được (không cắt giữa chữ) nên trả lại nguyên dòng."""
    words = line.split()
    n = len(words)
    if n < 2 or box_w <= 0 or box_h <= 0:
        return (line,)
    space = text_width(font, " ", 1.0)
    pre = [0.0]
    for w in words:
        pre.append(pre[-1] + text_width(font, w, 1.0))

    def width(i: int, j: int) -> float:                  # độ rộng (em) của chữ i..j-1 nối bằng khoảng trắng; text_width tuyến tính nên cộng được
        return pre[j] - pre[i] + (j - i - 1) * space

    inf = (float("inf"), float("inf"))
    best_size, best_cuts = -1.0, None
    for k in range(1, min(n, max(1, max_lines)) + 1):
        dp = [[inf] * (n + 1) for _ in range(k + 1)]     # dp[kk][j] = (độ rộng lớn nhất, tổng bình phương) tốt nhất chia j chữ đầu thành kk dòng
        cut = [[0] * (n + 1) for _ in range(k + 1)]
        dp[0][0] = (0.0, 0.0)
        for kk in range(1, k + 1):
            for j in range(kk, n + 1):
                for i in range(kk - 1, j):
                    prev = dp[kk - 1][i]
                    if prev == inf:
                        continue
                    w = width(i, j)
                    cand = (max(prev[0], w), prev[1] + w * w)
                    if cand < dp[kk][j]:
                        dp[kk][j], cut[kk][j] = cand, i
        max_w = dp[k][n][0]
        size = box_h / (LINE_HEIGHT_FACTOR * k)
        if max_w > 0:
            size = min(size, box_w / max_w)
        if size > best_size + 1e-9:                       # chỉ lấy khi lớn hơn hẳn: hòa thì giữ số dòng ít hơn
            bounds, j = [], n
            for kk in range(k, 0, -1):
                bounds.append((cut[kk][j], j))
                j = cut[kk][j]
            best_size, best_cuts = size, bounds[::-1]
    assert best_cuts is not None
    return tuple(" ".join(words[i:j]) for i, j in best_cuts)


def _rest_box(sub: Subtitle):
    """Hộp lúc nằm yên: keyframe cuối nếu có, không thì hộp cố định."""
    return sub.keyframes[-1].box if sub.keyframes else sub.box


def plan_text(sub: Subtitle, chain: Sequence[FontData], scale_y: float = 1.0) -> tuple[FontChoice, TextPlan]:
    """Chọn font và tách dòng cho MỘT phụ đề. Cố định suốt thời gian hiển thị.

    `scale_y` (px video thực / toạ độ #res theo chiều dọc) đổi `sub.font_px` (cỡ tối đa) sang px của video thực.

    Phụ đề có `wrap` (phụ đề phụ khi xuất), chỉ một dòng, hộp lúc nằm yên CAO HƠN RỘNG: cho chữ xuống dòng (`wrap_lines`) để chữ
    to hơn thay vì co nhỏ cho vừa chiều ngang hẹp. Còn lại giữ nguyên các dòng có sẵn trong chữ gốc."""
    text = fold_for_font(sub.text, chain)
    choice = choose_font(text, chain)
    lines = tuple(_normalize_lines(text))
    rest = _rest_box(sub)
    if sub.wrap and len(lines) == 1 and rest is not None and rest.height > rest.width > 0:
        lines = wrap_lines(lines[0], choice.font, rest.width * scale_y, rest.height * scale_y)
    em = max((text_width(choice.font, ln, 1.0) for ln in lines), default=0.0)
    fixed = None if sub.font_px is None else sub.font_px * scale_y
    return choice, TextPlan(lines, em, fixed)
