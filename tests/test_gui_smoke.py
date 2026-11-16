"""Test khói cho giao diện (Mốc 3). Tự bỏ qua nếu máy chưa cài PySide6; chạy không cần màn hình (offscreen)."""
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from srtpx_cover.core.parser import parse_srtpx  # noqa: E402
from srtpx_cover.core.preview import PreviewModel  # noqa: E402
from srtpx_cover.gui.subtitle_table import SubtitleTableModel  # noqa: E402
from srtpx_cover.gui.video_view import FontRegistry, VideoView  # noqa: E402
from tests.helpers import fake_chain  # noqa: E402

APP = QApplication.instance() or QApplication([])
SAMPLE = Path(__file__).parent / "data" / "sample.srtpx"
CHAIN = list(fake_chain(Path(tempfile.mkdtemp())))


def _doc():
    return parse_srtpx(SAMPLE.read_text(encoding="utf-8")).document


def test_table_model_shows_five_rows_with_srt_times():
    m = SubtitleTableModel()
    assert m.rowCount() == 0
    m.set_document(_doc())
    assert (m.rowCount(), m.columnCount()) == (5, 4)
    assert m.index(0, 1).data() == "00:00:00,300"
    assert m.index(0, 2).data() == "00:00:09,500"
    assert m.index(1, 3).data() == "米提亚先生"
    assert m.index(0, 0).data(Qt.ItemDataRole.TextAlignmentRole) is not None


def test_overlay_draws_black_box_and_text_and_clears():
    doc = _doc()
    model = PreviewModel(doc, (768, 576), CHAIN, 3.0)
    view = VideoView(FontRegistry())
    view.set_video_size(768, 576)
    view.set_overlay(model.items_at(10.0), None)           # block 2: 1 hộp + 1 dòng chữ
    assert len(view._overlay) == 2
    view.set_overlay(model.items_at(10.0), 1)              # chọn hộp -> thêm viền sáng
    assert len(view._overlay) == 3
    view.set_overlay([], None)
    assert view._overlay == []
