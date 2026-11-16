"""Dải keyframe dưới thanh tua: mỗi keyframe của phụ đề đang chọn là một chấm; bấm chọn, kéo để đổi giờ."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import theme

_HIT_PX = 9
_PAD = 10


class KeyframeStrip(QWidget):
    selected = Signal(int)          # bấm vào chấm i
    seek_requested = Signal(float)  # bấm vào chỗ trống -> tua tới giờ đó
    drag_started = Signal(int)
    dragged = Signal(int, float)    # chấm i, giờ mới (giây)
    drag_finished = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._times: list[float] = []
        self._duration = 0.0
        self._sel: int | None = None
        self._current = 0.0
        self._span: tuple[float, float] | None = None
        self._drag: int | None = None
        self.setToolTip("Dải keyframe của phụ đề đang chọn: bấm chấm để chọn, kéo để đổi giờ, Delete để xóa.")

    def set_state(self, times: list[float], duration: float, selected: int | None, current: float,
                  span: tuple[float, float] | None) -> None:
        state = (list(times), duration, selected, current, span)
        if state == (self._times, self._duration, self._sel, self._current, self._span):
            return
        self._times, self._duration, self._sel, self._current, self._span = state
        self.update()

    def _x(self, t: float) -> float:
        usable = max(1, self.width() - 2 * _PAD)
        return _PAD + (min(max(t, 0.0), self._duration) / self._duration * usable if self._duration > 0 else 0)

    def _t(self, x: float) -> float:
        usable = max(1, self.width() - 2 * _PAD)
        return min(max((x - _PAD) / usable, 0.0), 1.0) * self._duration

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2
        p.fillRect(self.rect(), QColor(theme.BG1))
        p.setPen(QPen(QColor(theme.BG4), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(_PAD, mid), QPointF(self.width() - _PAD, mid))
        if self._span is not None and self._duration > 0:  # đoạn phụ đề đang chọn đang hiển thị
            a, b = self._x(self._span[0]), self._x(self._span[1])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 61, 127, 110))
            p.drawRoundedRect(QRectF(a, mid - 3, max(2.0, b - a), 6), 3, 3)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        cx = self._x(self._current)
        p.drawLine(QPointF(cx, 3), QPointF(cx, self.height() - 3))
        for i, t in enumerate(self._times):
            on = i == self._sel
            r = 7.0 if on else 5.5
            x = self._x(t)
            poly = QPolygonF([QPointF(x, mid - r), QPointF(x + r, mid), QPointF(x, mid + r), QPointF(x - r, mid)])
            p.setPen(QPen(QColor(0, 0, 0, 170), 1))
            p.setBrush(QBrush(QColor("white") if on else QColor(theme.KEYFRAME)))
            p.drawPolygon(poly)

    def _hit(self, x: float) -> int | None:
        best, dist = None, _HIT_PX + 1.0
        for i, t in enumerate(self._times):
            d = abs(self._x(t) - x)
            if d < dist:
                best, dist = i, d
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        i = self._hit(x)
        if i is None:
            self.seek_requested.emit(self._t(x))
            return
        self._drag = i
        self.selected.emit(i)
        self.drag_started.emit(i)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self.dragged.emit(self._drag, self._t(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            i, self._drag = self._drag, None
            self.drag_finished.emit(i)
