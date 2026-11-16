"""Timeline nhiều lớp kiểu Alight Motion: thước thời gian, làn Video, các làn phụ đề, playhead.

- Mỗi phụ đề là một thanh màu; phụ đề không chồng nhau dùng chung một làn (xem core/timeline_layout.py).
- Bấm thanh = chọn; kéo thân = dời; kéo mép trái/phải = cắt đầu/đuôi (hút vào playhead và mép thanh khác).
- Bấm/kéo trên thước hoặc chỗ trống = tua. Ctrl + lăn chuột = zoom; Shift + lăn = cuộn ngang.
- Widget chỉ VẼ và phát tín hiệu; mọi thay đổi dữ liệu (kèm undo) do cửa sổ chính xử lý.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QFrame

from ..core.timeline_layout import assign_lanes, format_tick, tick_step
from . import theme
from .icons import icon

HEADER_W = 116
RULER_H = 28
LANE_H = 34
LANE_GAP = 6
EDGE_PX = 8
SNAP_PX = 7
DRAG_THRESHOLD_PX = 3
MIN_LEN = 0.05
MIN_PPS, MAX_PPS, DEFAULT_PPS = 4.0, 600.0, 80.0
END_PAD_PX = 140


@dataclass
class _Item:
    row: int
    start: float
    end: float
    text: str
    lane: int = 0


@dataclass
class _Drag:
    row: int
    mode: str            # "move" | "left" | "right"
    x0: float
    start0: float
    end0: float
    moved: bool = False


class TimelineView(QAbstractScrollArea):
    seek_requested = Signal(float)                  # giây
    row_selected = Signal(int)                      # bấm một thanh phụ đề
    range_edited = Signal(int, float, float, bool)  # row, start, end, là_dời (True: giữ độ dài, dời cả keyframe)
    zoom_changed = Signal(float)                    # điểm ảnh / giây

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setMinimumHeight(110)

        self._items: list[_Item] = []
        self._lane_count = 0
        self._duration = 0.0
        self._pps = DEFAULT_PPS
        self._playhead = 0.0
        self._selected: int | None = None
        self._kf_times: tuple[float, ...] = ()
        self._kf_index: int | None = None
        self._drag: _Drag | None = None
        self._override: tuple[int, float, float] | None = None
        self._scrubbing = False

        self.horizontalScrollBar().valueChanged.connect(lambda _v: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.viewport().update())
        self._font = QFont()
        self._font.setPixelSize(12)
        self._font_bold = QFont(self._font)
        self._font_bold.setBold(True)

    # ------------------------------------------------------------------ dữ liệu từ cửa sổ chính
    def set_items(self, items: list[tuple[int, float, float, str]], duration: float) -> None:
        """items = [(row, start, end, text)]; duration = độ dài video (0 nếu chưa mở)."""
        self._items = [_Item(r, s, e, t) for r, s, e, t in items]
        lanes = assign_lanes([(it.start, it.end) for it in self._items])
        for it, lane in zip(self._items, lanes):
            it.lane = lane
        self._lane_count = (max(lanes) + 1) if lanes else 0
        self._duration = max(0.0, duration)
        self._override = None
        self._update_scrollbars()
        self.viewport().update()

    def set_duration(self, duration: float) -> None:
        self._duration = max(0.0, duration)
        self._update_scrollbars()
        self.viewport().update()

    def set_state(self, playhead: float, selected: int | None, kf_times: tuple[float, ...],
                  kf_index: int | None) -> None:
        state = (playhead, selected, kf_times, kf_index)
        if state == (self._playhead, self._selected, self._kf_times, self._kf_index):
            return
        selected_changed = selected != self._selected
        playhead_changed = playhead != self._playhead
        self._playhead, self._selected, self._kf_times, self._kf_index = state
        if selected_changed and selected is not None:
            self._ensure_row_visible(selected)
        if playhead_changed and not self._scrubbing and self._drag is None:
            self._ensure_time_visible(playhead)
        self.viewport().update()

    # ------------------------------------------------------------------ zoom
    @property
    def pixels_per_second(self) -> float:
        return self._pps

    def set_zoom(self, pps: float, anchor_time: float | None = None, anchor_x: float | None = None) -> None:
        pps = min(MAX_PPS, max(MIN_PPS, pps))
        if math.isclose(pps, self._pps):
            return
        if anchor_time is None or anchor_x is None:  # mặc định neo vào playhead nếu đang thấy, không thì giữa khung
            anchor_time = self._playhead
            anchor_x = self._x(anchor_time)
            if not HEADER_W <= anchor_x <= self.viewport().width():
                anchor_x = HEADER_W + (self.viewport().width() - HEADER_W) / 2
                anchor_time = self._time_at(anchor_x)
        self._pps = pps
        self._update_scrollbars()
        self.horizontalScrollBar().setValue(int(HEADER_W + anchor_time * pps - anchor_x))
        self.viewport().update()
        self.zoom_changed.emit(pps)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self._pps * factor)

    # ------------------------------------------------------------------ toạ độ
    def _end_time(self) -> float:
        return max([self._duration] + [it.end for it in self._items])

    def _x(self, t: float) -> float:
        return HEADER_W + t * self._pps - self.horizontalScrollBar().value()

    def _time_at(self, x: float) -> float:
        return max(0.0, (x - HEADER_W + self.horizontalScrollBar().value()) / self._pps)

    def _lane_y(self, lane_slot: int) -> float:
        """Toạ độ y của làn (slot 0 = Video, 1.. = làn phụ đề)."""
        return RULER_H + LANE_GAP + lane_slot * (LANE_H + LANE_GAP) - self.verticalScrollBar().value()

    def _update_scrollbars(self) -> None:
        vp = self.viewport()
        view_w = max(1, vp.width() - HEADER_W)
        content_w = int(self._end_time() * self._pps) + END_PAD_PX
        hb = self.horizontalScrollBar()
        hb.setPageStep(view_w)
        hb.setSingleStep(40)
        hb.setRange(0, max(0, content_w - view_w))
        view_h = max(1, vp.height() - RULER_H)
        content_h = LANE_GAP + (self._lane_count + 1) * (LANE_H + LANE_GAP) + 8
        vb = self.verticalScrollBar()
        vb.setPageStep(view_h)
        vb.setSingleStep(LANE_H)
        vb.setRange(0, max(0, content_h - view_h))

    def _ensure_time_visible(self, t: float) -> None:
        x = self._x(t)
        width = self.viewport().width()
        if HEADER_W + 6 <= x <= width - 24:
            return
        view_w = max(1, width - HEADER_W)
        self.horizontalScrollBar().setValue(int(t * self._pps - view_w * 0.25))

    def _ensure_row_visible(self, row: int) -> None:
        it = next((i for i in self._items if i.row == row), None)
        if it is None:
            return
        y = self._lane_y(it.lane + 1)
        top, bottom = RULER_H, self.viewport().height()
        vb = self.verticalScrollBar()
        if y < top:
            vb.setValue(vb.value() + int(y - top - LANE_GAP))
        elif y + LANE_H > bottom:
            vb.setValue(vb.value() + int(y + LANE_H - bottom + LANE_GAP))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_scrollbars()

    # ------------------------------------------------------------------ vẽ
    def _item_times(self, it: _Item) -> tuple[float, float]:
        if self._override is not None and self._override[0] == it.row:
            return self._override[1], self._override[2]
        return it.start, it.end

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.viewport().width(), self.viewport().height()
        p.fillRect(0, 0, w, h, QColor(theme.BG0))
        self._paint_lanes(p, w, h)
        self._paint_header(p, w, h)
        self._paint_ruler(p, w)
        self._paint_playhead(p, w, h)
        p.end()

    def _paint_lanes(self, p: QPainter, w: int, h: int) -> None:
        p.save()
        p.setClipRect(QRect(HEADER_W, RULER_H, max(0, w - HEADER_W), max(0, h - RULER_H)))
        # sọc nền từng làn
        for slot in range(self._lane_count + 1):
            y = self._lane_y(slot)
            if y + LANE_H < RULER_H or y > h:
                continue
            p.fillRect(QRectF(HEADER_W, y - LANE_GAP / 2, w - HEADER_W, LANE_H + LANE_GAP), QColor(theme.BG1 if slot % 2 == 0 else theme.BG0))
        # vạch dọc mờ theo thước
        step = tick_step(self._pps)
        t_left, t_right = self._time_at(HEADER_W), self._time_at(w)
        grid = QColor(theme.BORDER)
        grid.setAlpha(110)
        p.setPen(QPen(grid, 1))
        k = max(0, int(t_left // step))
        while k * step <= t_right + step:
            x = round(self._x(k * step)) + 0.5
            p.drawLine(QPointF(x, RULER_H), QPointF(x, h))
            k += 1

        # thanh Video (làn 0)
        if self._duration > 0:
            y = self._lane_y(0)
            r = QRectF(self._x(0), y, self._duration * self._pps, LANE_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#34343C"))
            p.drawRoundedRect(r, 7, 7)
            p.setPen(QColor(theme.DIM))
            p.setFont(self._font)
            p.drawText(r.adjusted(12, 0, -8, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), "Video gốc")

        # thanh phụ đề
        fm = QFontMetrics(self._font)
        for it in self._items:
            start, end = self._item_times(it)
            x0, x1 = self._x(start), self._x(end)
            if x1 < HEADER_W - 4 or x0 > w + 4:
                continue
            y = self._lane_y(it.lane + 1)
            if y + LANE_H < RULER_H or y > h:
                continue
            r = QRectF(x0, y, max(3.0, x1 - x0), LANE_H)
            base = QColor(theme.LANE_COLORS[it.lane % len(theme.LANE_COLORS)])
            sel = it.row == self._selected
            p.setBrush(base.lighter(118) if sel else base)
            if sel:
                pen = QPen(QColor("white"), 2)
                p.setPen(pen)
                r_draw = r.adjusted(1, 1, -1, -1)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                r_draw = r
            p.drawRoundedRect(r_draw, 7, 7)
            if r.width() > 26:
                p.setPen(QColor("white"))
                p.setFont(self._font_bold if sel else self._font)
                text = it.text.replace("\r\n", " ").replace("\n", " ")
                p.drawText(r.adjusted(12, 0, -10, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 22)))
            if sel and r.width() > 22:  # tay nắm cắt mép như Alight Motion
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawRoundedRect(QRectF(r.left() + 3, y + 9, 3.5, LANE_H - 18), 1.7, 1.7)
                p.drawRoundedRect(QRectF(r.right() - 6.5, y + 9, 3.5, LANE_H - 18), 1.7, 1.7)
            if sel:
                self._paint_keyframes(p, y)

        if not self._items:
            p.setPen(QColor(theme.DIM))
            p.setFont(self._font)
            area = QRectF(HEADER_W, self._lane_y(1), w - HEADER_W, LANE_H)
            p.drawText(area, int(Qt.AlignmentFlag.AlignCenter), "Chưa có phụ đề — mở file .srtpx hoặc bấm  +  để thêm")
        p.restore()

    def _paint_keyframes(self, p: QPainter, lane_top: float) -> None:
        cy = lane_top + LANE_H - 6
        for i, t in enumerate(self._kf_times):
            cx = self._x(t)
            on = i == self._kf_index
            r = 6.0 if on else 4.5
            poly = QPolygonF([QPointF(cx, cy - r), QPointF(cx + r, cy), QPointF(cx, cy + r), QPointF(cx - r, cy)])
            p.setPen(QPen(QColor(0, 0, 0, 160), 1))
            p.setBrush(QColor("white") if on else QColor(theme.KEYFRAME))
            p.drawPolygon(poly)

    def _paint_header(self, p: QPainter, w: int, h: int) -> None:
        p.save()
        p.setClipRect(QRect(0, RULER_H, HEADER_W, max(0, h - RULER_H)))
        p.fillRect(0, RULER_H, HEADER_W, h - RULER_H, QColor(theme.BG1))
        p.setFont(self._font_bold)
        for slot in range(self._lane_count + 1):
            y = self._lane_y(slot)
            if y + LANE_H < RULER_H or y > h:
                continue
            name = "Video" if slot == 0 else f"Phụ đề {slot}"
            ic = icon("video" if slot == 0 else "layers", theme.DIM, 16)
            ic.paint(p, QRect(12, int(y + (LANE_H - 16) / 2), 16, 16))
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(36, y, HEADER_W - 40, LANE_H), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), name)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(HEADER_W - 1, RULER_H, HEADER_W - 1, h)
        p.restore()

    def _paint_ruler(self, p: QPainter, w: int) -> None:
        p.fillRect(0, 0, w, RULER_H, QColor(theme.BG2))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(0, RULER_H - 1, w, RULER_H - 1)
        p.save()
        p.setClipRect(QRect(HEADER_W, 0, max(0, w - HEADER_W), RULER_H))
        step = tick_step(self._pps)
        t_left, t_right = self._time_at(HEADER_W), self._time_at(w)
        p.setFont(self._font)
        k = max(0, int(t_left // step))
        minor = step / 5.0
        while k * step <= t_right + step:
            t = k * step
            x = round(self._x(t)) + 0.5
            p.setPen(QPen(QColor(theme.DIM), 1))
            p.drawLine(QPointF(x, RULER_H - 11), QPointF(x, RULER_H - 1))
            p.drawText(QPointF(x + 5, RULER_H - 11), format_tick(t, step))
            if minor * self._pps >= 9:
                p.setPen(QPen(QColor(theme.BG4), 1))
                for j in range(1, 5):
                    mx = round(self._x(t + j * minor)) + 0.5
                    p.drawLine(QPointF(mx, RULER_H - 5), QPointF(mx, RULER_H - 1))
            k += 1
        p.restore()
        p.fillRect(0, 0, HEADER_W, RULER_H - 1, QColor(theme.BG2))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(HEADER_W - 1, 0, HEADER_W - 1, RULER_H)
        p.setPen(QColor(theme.DIM))
        p.setFont(self._font_bold)
        p.drawText(QRectF(14, 0, HEADER_W - 16, RULER_H - 1), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), "TIMELINE")

    def _paint_playhead(self, p: QPainter, w: int, h: int) -> None:
        x = self._x(self._playhead)
        if x < HEADER_W - 1 or x > w:
            return
        accent = QColor(theme.ACCENT)
        p.setPen(QPen(accent, 2))
        p.drawLine(QPointF(x, 6), QPointF(x, h))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(x - 6, 2, 12, 12), 3, 3)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 12), QPointF(x + 6, 12), QPointF(x, 19)]))

    # ------------------------------------------------------------------ chuột
    def _hit(self, x: float, y: float) -> tuple[_Item, str] | None:
        if x < HEADER_W or y < RULER_H:
            return None
        for it in reversed(self._items):  # thanh vẽ sau nằm trên
            start, end = self._item_times(it)
            x0, x1 = self._x(start), self._x(end)
            ly = self._lane_y(it.lane + 1)
            if not (x0 - 2 <= x <= max(x1, x0 + 3) + 2 and ly <= y <= ly + LANE_H):
                continue
            edge = min(EDGE_PX, max(2.0, (x1 - x0) / 3))
            if self._selected == it.row and x1 - x0 > 20:
                if x - x0 <= edge:
                    return it, "left"
                if x1 - x <= edge:
                    return it, "right"
            return it, "move"
        return None

    def _snap_targets(self, skip_row: int) -> list[float]:
        targets = [0.0, self._playhead]
        for it in self._items:
            if it.row != skip_row:
                targets.extend((it.start, it.end))
        return targets

    def _snap(self, t: float, targets: list[float]) -> float:
        tol = SNAP_PX / self._pps
        best = min(targets, key=lambda c: abs(c - t), default=None)
        return best if best is not None and abs(best - t) <= tol else t

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        x, y = pos.x(), pos.y()
        if x < HEADER_W:
            return
        hit = self._hit(x, y)
        if hit is not None and y >= RULER_H:
            it, mode = hit
            self._drag = _Drag(it.row, mode, x, it.start, it.end)
            if it.row != self._selected:
                self.row_selected.emit(it.row)
            event.accept()
            return
        self._scrubbing = True
        self.seek_requested.emit(self._time_at(x))
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        x, y = pos.x(), pos.y()
        if self._scrubbing:
            self.seek_requested.emit(self._time_at(max(x, HEADER_W)))
            return
        if self._drag is not None:
            self._update_drag(x)
            return
        hit = self._hit(x, y)
        if hit is None:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        elif hit[1] in ("left", "right"):
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    def _update_drag(self, x: float) -> None:
        d = self._drag
        assert d is not None
        if not d.moved and abs(x - d.x0) < DRAG_THRESHOLD_PX:
            return
        d.moved = True
        dt = (x - d.x0) / self._pps
        targets = self._snap_targets(d.row)
        if d.mode == "move":
            length = d.end0 - d.start0
            start = max(0.0, d.start0 + dt)
            # hút mép trái hoặc mép phải của thanh vào mục tiêu gần nhất (chỉ xét phía thực sự hút được)
            shifts = [snapped - edge for edge in (start, start + length)
                      if (snapped := self._snap(edge, targets)) != edge]
            if shifts:
                start += min(shifts, key=abs)
            start = max(0.0, start)
            new = (start, start + length)
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif d.mode == "left":
            start = self._snap(d.start0 + dt, targets)
            new = (min(max(0.0, start), d.end0 - MIN_LEN), d.end0)
        else:
            end = self._snap(d.end0 + dt, targets)
            new = (d.start0, max(end, d.start0 + MIN_LEN))
        self._override = (d.row, round(new[0], 3), round(new[1], 3))
        self.viewport().update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._scrubbing:
            self._scrubbing = False
            return
        d, self._drag = self._drag, None
        override, self._override = self._override, None
        self.viewport().update()
        if d is None or not d.moved or override is None:
            return
        _row, start, end = override
        if (start, end) != (round(d.start0, 3), round(d.end0, 3)):
            self.range_edited.emit(d.row, start, end, d.mode == "move")

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta()
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            x = event.position().x()
            self.set_zoom(self._pps * (1.18 ** (delta.y() / 120.0)), self._time_at(x), x)
            event.accept()
            return
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        if mods & Qt.KeyboardModifier.ShiftModifier or delta.x() != 0:
            hb.setValue(hb.value() - (delta.x() or delta.y()))
        else:
            vb.setValue(vb.value() - delta.y() // 3)
        event.accept()
