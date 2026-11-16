"""Icon phẳng một màu vẽ bằng QPainter (không cần file ảnh), toạ độ 24x24, nét 2px bo tròn như Alight Motion."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

NORMAL = "#E6E6EA"
DISABLED = "#55555C"
ACCENT = "#FF3D7F"


def _poly(*pts: float) -> QPolygonF:
    return QPolygonF([QPointF(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)])


def _line(p: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _fill(p: QPainter, color: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)


def _stroke(p: QPainter) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)


def _play(p, c):
    _fill(p, c); p.drawPolygon(_poly(8, 5, 19, 12, 8, 19))


def _pause(p, c):
    _fill(p, c); p.drawRoundedRect(QRectF(6, 5, 4, 14), 1.2, 1.2); p.drawRoundedRect(QRectF(14, 5, 4, 14), 1.2, 1.2)


def _prev(p, c):
    _fill(p, c); p.drawRoundedRect(QRectF(5, 5, 2.4, 14), 1, 1); p.drawPolygon(_poly(19, 5, 19, 19, 9, 12))


def _next(p, c):
    _fill(p, c); p.drawRoundedRect(QRectF(16.6, 5, 2.4, 14), 1, 1); p.drawPolygon(_poly(5, 5, 5, 19, 15, 12))


def _plus(p, c):
    _line(p, 12, 5, 12, 19); _line(p, 5, 12, 19, 12)


def _minus(p, c):
    _line(p, 5, 12, 19, 12)


def _diamond(p, c):
    _fill(p, c); p.drawPolygon(_poly(12, 3.5, 20.5, 12, 12, 20.5, 3.5, 12))


def _kf_add(p, c):
    _stroke(p); p.drawPolygon(_poly(12, 3.5, 20.5, 12, 12, 20.5, 3.5, 12)); _line(p, 12, 9, 12, 15); _line(p, 9, 12, 15, 12)


def _kf_del(p, c):
    _stroke(p); p.drawPolygon(_poly(12, 3.5, 20.5, 12, 12, 20.5, 3.5, 12)); _line(p, 9, 12, 15, 12)


def _kf_copy(p, c):
    _stroke(p); p.drawPolygon(_poly(8.5, 5, 14, 11, 8.5, 17, 3, 11))
    _line(p, 15, 18, 21, 18)
    h = QPainterPath(); h.moveTo(18.5, 15.5); h.lineTo(21, 18); h.lineTo(18.5, 20.5)
    p.drawPath(h)


def _copy(p, c):
    _stroke(p)
    path = QPainterPath(); path.moveTo(14, 5); path.lineTo(5, 5); path.lineTo(5, 14)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(9, 9, 10, 10), 2, 2)


def _trash(p, c):
    _stroke(p)
    _line(p, 5, 7, 19, 7)
    h = QPainterPath(); h.moveTo(10, 7); h.lineTo(10, 4.5); h.lineTo(14, 4.5); h.lineTo(14, 7)
    p.drawPath(h)
    b = QPainterPath(); b.moveTo(7, 7); b.lineTo(8, 19.5); b.lineTo(16, 19.5); b.lineTo(17, 7)
    p.drawPath(b)


def _undo(p, c):
    _stroke(p)
    a = QPainterPath(); a.moveTo(5.5, 11); a.lineTo(13, 11); a.cubicTo(17, 11, 19.5, 13, 19.5, 16.5)
    p.drawPath(a)
    h = QPainterPath(); h.moveTo(9.5, 6.5); h.lineTo(5.2, 11); h.lineTo(9.5, 15.5)
    p.drawPath(h)


def _redo(p, c):
    p.translate(24, 0); p.scale(-1, 1); _undo(p, c)


def _folder(p, c):
    _stroke(p); p.drawPolygon(_poly(3.5, 6, 9, 6, 11, 9, 20.5, 9, 20.5, 19, 3.5, 19))


def _file(p, c):
    _stroke(p); p.drawRoundedRect(QRectF(6, 3.5, 12, 17), 2, 2)
    _line(p, 9, 9, 15, 9); _line(p, 9, 13, 15, 13); _line(p, 9, 17, 12.5, 17)


def _save(p, c):
    _stroke(p); p.drawRoundedRect(QRectF(4, 4, 16, 16), 2.5, 2.5)
    p.drawRect(QRectF(8, 4, 8, 5)); p.drawRect(QRectF(8, 13, 8, 7))


def _export(p, c):
    _stroke(p)
    _line(p, 12, 15, 12, 4)
    h = QPainterPath(); h.moveTo(8, 8); h.lineTo(12, 4); h.lineTo(16, 8)
    p.drawPath(h)
    t = QPainterPath(); t.moveTo(5, 13); t.lineTo(5, 19.5); t.lineTo(19, 19.5); t.lineTo(19, 13)
    p.drawPath(t)


def _settings(p, c):
    _line(p, 4, 7, 20, 7); _line(p, 4, 12, 20, 12); _line(p, 4, 17, 20, 17)
    _fill(p, c)
    for x, y in ((9, 7), (15, 12), (8, 17)):
        p.drawEllipse(QPointF(x, y), 2.6, 2.6)


def _menu(p, c):
    _line(p, 4.5, 6.5, 19.5, 6.5); _line(p, 4.5, 12, 19.5, 12); _line(p, 4.5, 17.5, 19.5, 17.5)


def _layers(p, c):
    _stroke(p)
    p.drawPolygon(_poly(12, 4, 21, 9, 12, 14, 3, 9))
    a = QPainterPath(); a.moveTo(3, 13); a.lineTo(12, 18); a.lineTo(21, 13)
    p.drawPath(a)


def _video(p, c):
    _stroke(p); p.drawRoundedRect(QRectF(3.5, 6, 17, 12), 2.5, 2.5)
    _fill(p, c); p.drawPolygon(_poly(10.5, 9.5, 15, 12, 10.5, 14.5))


_DRAW: dict[str, Callable[[QPainter, QColor], None]] = {
    "play": _play, "pause": _pause, "prev": _prev, "next": _next, "plus": _plus, "minus": _minus,
    "diamond": _diamond, "kf_add": _kf_add, "kf_del": _kf_del, "kf_copy": _kf_copy, "copy": _copy, "trash": _trash,
    "undo": _undo, "redo": _redo, "folder": _folder, "file": _file, "save": _save, "export": _export,
    "settings": _settings, "menu": _menu, "layers": _layers, "video": _video,
}


def _pixmap(name: str, color: str, size: int) -> QPixmap:
    scale = 2  # vẽ gấp đôi rồi đặt devicePixelRatio để nét sắc trên màn hình độ phân giải cao
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    c = QColor(color)
    pen = QPen(c, 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _DRAW[name](p, c)
    p.end()
    return pm


_cache: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str = NORMAL, size: int = 22) -> QIcon:
    """Icon theo tên; có sẵn trạng thái vô hiệu (xám) tự động."""
    key = (name, color, size)
    if key not in _cache:
        ic = QIcon()
        ic.addPixmap(_pixmap(name, color, size), QIcon.Mode.Normal)
        ic.addPixmap(_pixmap(name, DISABLED, size), QIcon.Mode.Disabled)
        _cache[key] = ic
    return _cache[key]
