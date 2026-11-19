"""Mô hình preview: với giờ t, những hộp/chữ nào đang hiển thị và vẽ ở đâu (thuần Python, không Qt).

Dùng đúng hình học (geometry) và bố cục chữ (layout.plan_subtitle_text) như bộ sinh lệnh FFmpeg,
nên hộp ở preview và ở video xuất luôn là một (dung sai 1px của mục 4 chỉ còn là sai số làm tròn của hiển thị).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .fonts import FontData, FontChoice, FontError, FontSpec, describe_font_warnings
from .geometry import PixelBox, compute_scale, is_visible, pixel_box, smallest_pixel_size, video_box, video_polygon
from .layout import LINE_HEIGHT_FACTOR, TextPlan, plan_text
from .model import SrtpxDocument


@dataclass(frozen=True)
class PreviewItem:
    """Một phụ đề đang hiển thị tại một thời điểm, theo toạ độ điểm ảnh của video thực."""

    row: int                    # vị trí trong doc.subtitles (khác `index` là số thứ tự ghi trong file)
    index: int
    box: PixelBox               # hộp đen đã làm tròn (cùng quy tắc với drawbox)
    lines: tuple[str, ...]      # các dòng chữ trắng, căn giữa từng dòng; rỗng nếu không vẽ chữ
    font_size: float
    line_height: float
    font: FontSpec | None
    cx: float                   # tâm hộp (chưa làm tròn) ở thời điểm này, dùng căn giữa chữ
    cy: float
    fits: bool
    polygon: tuple[tuple[float, float], ...] | None = None   # đa giác che (chữ gốc nghiêng), điểm ảnh video thực; None = dùng `box`

    def contains(self, x: float, y: float) -> bool:
        """Điểm (toạ độ video) có nằm trong vùng che không (đa giác nếu có, không thì hộp)."""
        if self.polygon is None:
            return self.box.x1 <= x < self.box.x2 and self.box.y1 <= y < self.box.y2
        inside = False
        pts = self.polygon
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
                inside = not inside
        return inside or (self.box.x1 <= x < self.box.x2 and self.box.y1 <= y < self.box.y2)

    def line_center_y(self, j: int) -> float:
        """Toạ độ y tâm dòng thứ j (khớp công thức drawtext: lệch (j-(n-1)/2) * line_height)."""
        return self.cy + (j - (len(self.lines) - 1) / 2) * self.line_height


class PreviewModel:
    """Giữ font từng phụ đề, cỡ chữ dính theo hộp tại giờ t và trả về các mục hiển thị tại giờ t."""

    def __init__(
        self,
        doc: SrtpxDocument,
        video_size: tuple[int, int],
        chain: Sequence[FontData],
        margin: float,
    ):
        self.doc = doc
        self.video_size = video_size
        self.margin = margin
        self.scale = compute_scale(doc.res, video_size)
        self.warnings: list[str] = []
        if (w := self.scale.warning()):
            self.warnings.append(w)

        self._chain = list(chain)
        self._plans: dict[int, tuple[FontChoice, TextPlan]] = {}
        draw_text = bool(chain)
        if not draw_text:
            self.warnings.append("Chưa có font nào để vẽ chữ (kiểm tra thư mục fonts/). Preview chỉ hiện hộp đen.")
        choices: dict[int, FontChoice] = {}
        overflow: list[int] = []
        for row, sub in enumerate(doc.subtitles):
            if not draw_text or not sub.text.strip():
                continue
            try:
                choice, plan = plan_text(sub, chain, self.scale.sy)
            except FontError as exc:
                self.warnings.append(str(exc))
                continue
            self._plans[row] = (choice, plan)
            choices[sub.index] = choice
            bw, bh = smallest_pixel_size(sub, self.scale, margin, video_size)
            if not plan.fits_at(bw, bh):
                overflow.append(sub.index)
        if overflow:
            self.warnings.append(
                "Hộp quá nhỏ, chữ tràn hộp ngay cả ở 1px ở phụ đề: "
                + ", ".join(map(str, overflow)) + "."
            )
        if (fw := describe_font_warnings(choices)):
            self.warnings.append(fw)

    def update_row(self, row: int) -> None:
        """Tính lại bố cục chữ của MỘT phụ đề (sau khi kéo hộp hoặc sửa chữ), không đụng các phụ đề khác."""
        self._plans.pop(row, None)
        sub = self.doc.subtitles[row]
        if not self._chain or not sub.text.strip():
            return
        try:
            self._plans[row] = plan_text(sub, self._chain, self.scale.sy)
        except FontError:
            pass

    def items_at(self, t: float) -> list[PreviewItem]:
        """Các phụ đề hiển thị tại giờ t (nửa mở: start <= t < end), theo thứ tự trong file (cái sau vẽ đè cái trước)."""
        out: list[PreviewItem] = []
        for row, sub in enumerate(self.doc.subtitles):
            if not is_visible(sub, t):
                continue
            box = pixel_box(sub, t, self.scale, self.margin, self.video_size)
            poly = video_polygon(sub, self.scale, self.margin, self.video_size, t)
            if box.w <= 0 or box.h <= 0:
                continue  # bị kẹp hết ra ngoài khung hình (giống bộ xuất)
            vb = video_box(sub, t, self.scale, self.margin, self.video_size)
            plan = self._plans.get(row)
            if plan is None:
                lines: tuple[str, ...] = ()
                size, lh, font, fits = 0.0, 0.0, None, True
            else:
                choice, tp = plan
                bw, bh = vb.x2 - vb.x1, vb.y2 - vb.y1
                size = tp.size_for(bw, bh)       # chữ dính hộp: cỡ chữ liên tục theo hộp tại giờ t
                lines, lh, font, fits = tp.lines, LINE_HEIGHT_FACTOR * size, choice.font.spec, tp.fits_at(bw, bh)
            out.append(PreviewItem(
                row, sub.index, box, lines, size, lh, font,
                (vb.x1 + vb.x2) / 2, (vb.y1 + vb.y2) / 2, fits,
                tuple(poly) if poly is not None else None,
            ))
        return out
