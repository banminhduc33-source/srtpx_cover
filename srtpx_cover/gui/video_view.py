"""Vùng preview: QGraphicsView + QGraphicsVideoItem, hộp đen và chữ trắng vẽ đè bằng QGraphicsItem.

Toạ độ scene = điểm ảnh của video THỰC (video item được đặt size bằng độ phân giải thật), nên hộp từ
core/preview.py đặt thẳng vào scene, và view tự scale khi cửa sổ co giãn.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QSlider,
    QStyle,
)

from ..core.edit import HANDLES
from ..core.fonts import FontSpec
from ..core.preview import PreviewItem

SELECT_COLOR = QColor(255, 61, 127)  # hồng accent, giống khung chọn của Alight Motion
_Z_BASE = 10            # trên video (z = 0)
_Z_SELECTION = 1_000_000
_Z_HANDLE = 1_000_001
HANDLE_PX = 8           # cạnh tay nắm (điểm ảnh màn hình)
HIT_TOLERANCE_PX = 7    # vùng bấm trúng tay nắm (điểm ảnh màn hình)
_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


class FontRegistry:
    """Nạp file font vào Qt (QFontDatabase.addApplicationFont) để preview dùng đúng font như khi xuất."""

    def __init__(self) -> None:
        self._family: dict[tuple[str, int], str | None] = {}

    def family(self, spec: FontSpec) -> str | None:
        key = (spec.path, spec.face_index)
        if key not in self._family:
            font_id = QFontDatabase.addApplicationFont(spec.path)
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families:
                # file TTC có nhiều face: face_index chọn họ font tương ứng (msyh.ttc: 0 = Microsoft YaHei)
                self._family[key] = families[min(spec.face_index, len(families) - 1)]
            else:
                self._family[key] = None
        return self._family[key]


class VideoView(QGraphicsView):
    row_clicked = Signal(int)                     # bấm vào hộp khác ở preview -> chọn phụ đề đó
    drag_started = Signal(int)                    # row
    drag_moved = Signal(int, str, float, float)   # row, chế độ ('move' hoặc tay nắm), dx, dy (điểm ảnh video)
    drag_finished = Signal(int)                   # row

    def __init__(self, fonts: FontRegistry, parent=None) -> None:
        scene = QGraphicsScene()
        super().__init__(scene, parent)
        self._scene = scene
        self._fonts = fonts
        self.video_item = QGraphicsVideoItem()
        self.video_item.setZValue(0)
        scene.addItem(self.video_item)

        self.setBackgroundBrush(QBrush(QColor(13, 13, 15)))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Không bật Antialiasing: hộp đen phải có mép dứt khoát theo điểm ảnh; chỉ làm mượt chữ và video.
        self.setRenderHints(QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._overlay: list[QGraphicsItem] = []
        self._overlay_key: object = None
        self._last_args: tuple[list[PreviewItem], int | None, bool] | None = None
        self._items: list[PreviewItem] = []
        self._selected_row: int | None = None
        self._editable = False
        self._drag: tuple[int, str, QPointF] | None = None
        self.setMouseTracking(True)

    # -- video
    def set_video_size(self, width: int, height: int) -> None:
        self.video_item.setSize(QSizeF(width, height))
        self._scene.setSceneRect(QRectF(0, 0, width, height))
        self.clear_overlay()
        self.fit()

    def fit(self) -> None:
        rect = self._scene.sceneRect()
        if rect.width() > 0 and rect.height() > 0:
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802 (tên do Qt quy định)
        super().resizeEvent(event)
        self.fit()
        self._redraw()  # kích thước tay nắm phụ thuộc mức zoom

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.fit()

    # -- lớp phủ
    def clear_overlay(self) -> None:
        for item in self._overlay:
            self._scene.removeItem(item)
        self._overlay = []
        self._overlay_key = None

    def _redraw(self) -> None:
        if self._last_args is not None:
            self._overlay_key = None
            self.set_overlay(*self._last_args)

    def set_overlay(self, items: list[PreviewItem], selected_row: int | None, editable: bool = False) -> None:
        """Vẽ các hộp đen + chữ trắng của `items`; hộp của `selected_row` (nếu đang hiển thị) có viền sáng.

        Bỏ qua nếu không có gì đổi so với lần trước (gọi liên tục khi đang phát video).
        """
        self._last_args = (items, selected_row, editable)
        self._items, self._selected_row, self._editable = items, selected_row, editable
        key = (tuple(items), selected_row, editable, round(self._zoom(), 3))
        if key == self._overlay_key:
            return
        self.clear_overlay()
        self._overlay_key = key
        for n, it in enumerate(items):
            z = _Z_BASE + 2 * n  # phụ đề sau vẽ đè phụ đề trước, giống thứ tự filter khi xuất
            box = QGraphicsRectItem(QRectF(it.box.x1, it.box.y1, it.box.w, it.box.h))
            box.setBrush(QBrush(Qt.GlobalColor.black))   # đục 100%
            box.setPen(QPen(Qt.PenStyle.NoPen))
            box.setZValue(z)
            self._add(box)
            self._add_text(it, z + 1)
            if it.row == selected_row:
                outline = QGraphicsRectItem(QRectF(it.box.x1, it.box.y1, it.box.w, it.box.h))
                pen = QPen(SELECT_COLOR)
                pen.setWidth(2)
                pen.setCosmetic(True)  # dày 2px màn hình bất kể mức zoom
                outline.setPen(pen)
                outline.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                outline.setZValue(_Z_SELECTION)
                self._add(outline)
                if editable:
                    self._add_handles(it)

    def _zoom(self) -> float:
        return self.transform().m11() or 1.0

    @staticmethod
    def _handle_points(it: PreviewItem) -> dict[str, tuple[float, float]]:
        b = it.box
        xs = {"w": b.x1, "e": b.x2, "": (b.x1 + b.x2) / 2}
        ys = {"n": b.y1, "s": b.y2, "": (b.y1 + b.y2) / 2}
        return {h: (xs[h[-1] if h[-1] in "we" else ""], ys[h[0] if h[0] in "ns" else ""]) for h in HANDLES}

    def _add_handles(self, it: PreviewItem) -> None:
        half = HANDLE_PX / 2 / self._zoom()  # tay nắm luôn cỡ cố định trên màn hình
        for x, y in self._handle_points(it).values():
            r = QGraphicsRectItem(QRectF(x - half, y - half, 2 * half, 2 * half))
            r.setBrush(QBrush(SELECT_COLOR))
            pen = QPen(Qt.GlobalColor.black)
            pen.setCosmetic(True)
            r.setPen(pen)
            r.setZValue(_Z_HANDLE)
            self._add(r)

    # -- chuột: kéo hộp đang chọn (thân = dời, tay nắm = đổi kích thước), bấm hộp khác = chọn
    def _selected_item(self) -> PreviewItem | None:
        return next((i for i in self._items if i.row == self._selected_row), None)

    def _hit_mode(self, p: QPointF) -> str | None:
        it = self._selected_item()
        if it is None or not self._editable:
            return None
        tol = HIT_TOLERANCE_PX / self._zoom()
        for name, (x, y) in self._handle_points(it).items():
            if abs(p.x() - x) <= tol and abs(p.y() - y) <= tol:
                return name
        b = it.box
        return "move" if b.x1 <= p.x() < b.x2 and b.y1 <= p.y() < b.y2 else None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            p = self.mapToScene(event.position().toPoint())
            mode = self._hit_mode(p)
            if mode is not None and self._selected_row is not None:
                self._drag = (self._selected_row, mode, p)
                self.drag_started.emit(self._selected_row)
                event.accept()
                return
            for it in reversed(self._items):  # hộp vẽ sau nằm trên
                b = it.box
                if b.x1 <= p.x() < b.x2 and b.y1 <= p.y() < b.y2:
                    self.row_clicked.emit(it.row)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        p = self.mapToScene(event.position().toPoint())
        if self._drag is not None:
            row, mode, start = self._drag
            self.drag_moved.emit(row, mode, p.x() - start.x(), p.y() - start.y())
            event.accept()
            return
        mode = self._hit_mode(p)
        self.viewport().setCursor(_CURSORS[mode] if mode else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            row = self._drag[0]
            self._drag = None
            self.drag_finished.emit(row)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _add(self, item: QGraphicsItem) -> None:
        self._scene.addItem(item)
        self._overlay.append(item)

    def _add_text(self, it: PreviewItem, z: float) -> None:
        if not it.lines or it.font is None:
            return
        font = QFont()
        family = self._fonts.family(it.font)
        if family:
            font.setFamily(family)
        base = 100                          # vẽ ở cỡ cố định rồi phóng bằng setScale để cỡ chữ liên tục (số thực)
        font.setPixelSize(base)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)  # hinting ép nét theo px nguyên -> chữ nhảy khi phóng số thực
        k = it.font_size / base
        for j, line in enumerate(it.lines):
            if not line:
                continue
            text = QGraphicsSimpleTextItem(line)
            text.setFont(font)
            text.setBrush(QBrush(Qt.GlobalColor.white))
            br = text.boundingRect()
            text.setScale(k)
            text.setPos(it.cx - br.width() * k / 2, it.line_center_y(j) - br.height() * k / 2)
            text.setZValue(z)
            self._add(text)


class JumpSlider(QSlider):
    """Thanh tua: bấm vào đâu thì nhảy tới đó (QSlider mặc định chỉ nhảy từng bước) và kéo được."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _jump(self, event) -> None:
        value = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(event.position().x()), max(1, self.width()))
        self.setValue(value)
        self.sliderMoved.emit(value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._jump(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self._jump(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self._jump(event)
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
