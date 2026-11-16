"""Bảng danh sách phụ đề: QAbstractTableModel + QTableView (không tạo widget riêng cho từng dòng)."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit

from ..core.edit import parse_time_text
from ..core.model import SrtpxDocument, Subtitle
from ..core.parser import format_time

HEADERS = ("#", "Bắt đầu", "Kết thúc", "Nội dung")
COL_INDEX, COL_START, COL_END, COL_TEXT = range(4)

_NO_PARENT = QModelIndex()


class SubtitleTableModel(QAbstractTableModel):
    """Giờ bắt đầu/kết thúc sửa được trong bảng; model KHÔNG tự đổi dữ liệu mà phát `time_edited`
    để cửa sổ chính đẩy lệnh vào QUndoStack (nội dung chữ sửa ở ô soạn thảo bên dưới bảng)."""

    time_edited = Signal(int, int, float)  # row, cột (COL_START/COL_END), giây

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: SrtpxDocument | None = None

    def set_document(self, doc: SrtpxDocument | None) -> None:
        self.beginResetModel()
        self._doc = doc
        self.endResetModel()

    def subtitle_at(self, row: int) -> Subtitle | None:
        if self._doc is None or not 0 <= row < len(self._doc.subtitles):
            return None
        return self._doc.subtitles[row]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _NO_PARENT) -> int:
        if parent.isValid() or self._doc is None:
            return 0
        return len(self._doc.subtitles)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        sub = self.subtitle_at(index.row()) if index.isValid() else None
        if sub is None:
            return None
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_INDEX:
                return str(sub.index)
            if col == COL_START:
                return format_time(sub.start)
            if col == COL_END:
                return format_time(sub.end)
            return sub.text.replace("\r\n", "\n").replace("\n", " / ")  # bảng chỉ hiện một dòng
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_TEXT:
            return sub.text
        if role == Qt.ItemDataRole.TextAlignmentRole and col != COL_TEXT:
            return int(Qt.AlignmentFlag.AlignCenter.value)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.isValid() and index.column() in (COL_START, COL_END):
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex | QPersistentModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or index.column() not in (COL_START, COL_END):
            return False
        seconds = parse_time_text(str(value))
        if seconds is None:
            return False
        self.time_edited.emit(index.row(), index.column(), seconds)
        return False  # dữ liệu đổi qua lệnh undo, rồi bảng được nạp lại

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return None


class CommitPlainTextEdit(QPlainTextEdit):
    """Ô soạn thảo nhiều dòng; phát `committed` khi mất focus (một lần sửa = một bước undo)."""

    committed = Signal()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.committed.emit()
