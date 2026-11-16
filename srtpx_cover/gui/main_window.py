"""Cửa sổ chính (Mốc 6: hoàn thiện). Mở video, nạp .srtpx, preview, kéo hộp, sửa chữ/giờ, thêm/xóa/nhân đôi, keyframe, lưu, undo/redo."""
from __future__ import annotations

import logging
import math
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QFrame,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.dub import prepare_dub_audio, select_dub_subtitles
from ..core.tts import TtsError, ensure_clips, tiktok_synthesizer
from ..core.export import ExportOptions, build_command, build_filter_script, write_textfiles
from ..core.ffmpeg_tools import (
    FfmpegError, VideoInfo, build_proxy_command, ffmpeg_major_version, find_tool, probe_video,
)
from ..core.fonts import FontData, FontError, font_chain_from_settings
from ..core.edit import (
    DEFAULT_NEW_DURATION, DEFAULT_NEW_TEXT, add_keyframe, apply_drag, clone_subtitle, clone_subtitles,
    copy_keyframes, default_box, delete_keyframe, find_keyframe, insert_position, move_keyframe, renumber,
    set_box_at, valid_times,
)
from ..core.geometry import DEFAULT_MARGIN_PX, frame_time, interpolate_box, visible_frame_range
from ..core.model import Box, Keyframe, SrtpxDocument, Subtitle
from ..core.parser import SrtpxError, read_srtpx, save_srtpx
from ..core.playback import (
    clamp_frame,
    format_clock,
    frame_at,
    position_ms_for_frame,
)
from ..core.preview import PreviewModel
from .dialogs import (
    ExportDialog, SettingsDialog, dub_enabled, dub_options, dub_sessionid, dub_voice, run_task_with_progress,
    run_with_progress, settings_overrides,
)
from . import theme
from .icons import icon
from .keyframe_strip import KeyframeStrip
from .subtitle_table import COL_END, COL_INDEX, COL_START, COL_TEXT, CommitPlainTextEdit, SubtitleTableModel
from .timeline import DEFAULT_PPS, MAX_PPS, MIN_PPS, TimelineView
from .video_view import FontRegistry, JumpSlider, VideoView

log = logging.getLogger(__name__)


class SnapshotCommand(QUndoCommand):
    """Lệnh hoàn tác dựa trên ảnh chụp danh sách phụ đề trước/sau (vài trăm phụ đề vẫn nhẹ).

    Một lần kéo chuột = một lệnh (đẩy khi nhả chuột), nên không cần mergeWith.
    """

    def __init__(self, window: "MainWindow", text: str, before: list[Subtitle], after: list[Subtitle],
                 row_before: int | None, row_after: int | None) -> None:
        super().__init__(text)
        self._w, self._before, self._after = window, before, after
        self._rb, self._ra = row_before, row_after

    def redo(self) -> None:
        self._w.apply_snapshot(self._after, self._ra)

    def undo(self) -> None:
        self._w.apply_snapshot(self._before, self._rb)

# Khi đóng gói PyInstaller (onedir), fonts/ và ffmpeg nằm cạnh file .exe.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent
VIDEO_FILTER = "Video (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts);;Tất cả các file (*.*)"
SRTPX_FILTER = "File phụ đề .srtpx (*.srtpx);;Tất cả các file (*.*)"
MAX_WARNING_LINES = 25
TICK_MS = 20  # nhịp cập nhật hộp khi đang phát (QMediaPlayer chỉ báo vị trí mỗi ~100 ms)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        self.fonts = FontRegistry()

        self.video_path: str | None = None
        self.video_info: VideoInfo | None = None
        self.srtpx_path: str | None = None
        self.doc: SrtpxDocument | None = None
        self.preview: PreviewModel | None = None
        self.selected_row: int | None = None
        self._chain: list[FontData] | None = None
        # Khung đã tua tới bằng tay (mũi tên / bấm dòng): QMediaPlayer không tua chính xác từng khung và
        # có thể báo vị trí lệch, nên khi đang tạm dừng ta tin khung này để hộp hiện đúng khung đã chọn.
        self._cursor_frame: int | None = None
        self._player_error_shown = False
        self.undo_stack = QUndoStack(self)
        self._drag_before: list[Subtitle] | None = None
        self._drag_box: Box | None = None
        self._drag_time = 0.0
        self._drag_state: tuple[Box | None, list] | None = None
        self.kf_index: int | None = None  # keyframe đang chọn của phụ đề đang chọn
        self._kf_before: list[Subtitle] | None = None
        self._kf_orig: list | None = None
        self._proxy_dir: str | None = None

        self._build_player()
        self._build_ui()
        self._build_actions()
        self._restore_state()
        self._update_status()
        self._update_title()
        self.undo_stack.cleanChanged.connect(lambda _c: self._update_title())

    # ------------------------------------------------------------------ dựng giao diện
    def _build_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._refresh)
        self.player.positionChanged.connect(lambda _ms: self._refresh())
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.errorOccurred.connect(self._on_player_error)

    def _tool_button(self, name: str, tip: str, size: int = 20, object_name: str | None = None) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(name))
        b.setIconSize(QSize(size, size))
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # để phím cách/mũi tên không bị nút giữ lại
        if object_name:
            b.setObjectName(object_name)
        return b

    @staticmethod
    def _vsep() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 22)
        line.setStyleSheet(f"background: {theme.BORDER}; border: none;")
        return line

    def _build_empty_state(self) -> QWidget:
        """Màn hình chào khi chưa mở video (thay cho khung đen trống)."""
        w = QWidget()
        w.setObjectName("emptyState")
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        pic = QLabel()
        pic.setPixmap(icon("video", theme.DIM, 56).pixmap(56, 56))
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Bắt đầu dự án")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(
            "Làm theo 3 bước:\n\n"
            "1.  Bấm  “Mở video”  và chọn video có phụ đề cứng\n"
            "2.  Bấm  “Mở .srtpx”  và chọn file phụ đề tương ứng\n"
            "3.  Kéo hộp đen trên video cho khớp chữ, rồi bấm  “Xuất”  (nút hồng góc phải)"
        )
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        open_video = QPushButton("1. Mở video")
        open_video.setObjectName("primary")
        open_video.clicked.connect(lambda: self.open_video())
        open_srtpx = QPushButton("2. Mở .srtpx")
        open_srtpx.clicked.connect(lambda: self.open_srtpx())
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        buttons.addWidget(open_video)
        buttons.addWidget(open_srtpx)
        buttons.addStretch(1)
        lay.addStretch(1)
        lay.addWidget(pic)
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addSpacing(8)
        lay.addLayout(buttons)
        lay.addStretch(1)
        return w

    def _build_ui(self) -> None:
        self.view = VideoView(self.fonts)
        self.player.setVideoOutput(self.view.video_item)

        # ---- vùng preview (giữa) + thanh tua mảnh
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self._build_empty_state())
        self.preview_stack.addWidget(self.view)

        self.slider = JumpSlider()
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        scrub = QHBoxLayout()
        scrub.setContentsMargins(18, 8, 18, 12)
        scrub.addWidget(self.slider)

        preview_col = QWidget()
        preview_lay = QVBoxLayout(preview_col)
        preview_lay.setContentsMargins(0, 0, 0, 0)
        preview_lay.setSpacing(0)
        preview_lay.addWidget(self.preview_stack, 1)
        preview_lay.addLayout(scrub)

        # ---- panel lớp phụ đề (phải)
        self.table_model = SubtitleTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.DoubleClicked | QTableView.EditTrigger.EditKeyPressed)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(36)
        header = self.table.horizontalHeader()
        for col in (COL_INDEX, COL_START, COL_END):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.clicked.connect(lambda idx: self._select_row(idx.row()))
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _prev: self._select_row(cur.row()))

        self.text_edit = CommitPlainTextEdit()
        self.text_edit.setPlaceholderText("Chọn một phụ đề để sửa nội dung (bấm ra ngoài để áp dụng)")
        self.text_edit.setEnabled(False)
        self.text_edit.setMaximumHeight(120)
        self.text_edit.committed.connect(self._commit_text_edit)
        self.table_model.time_edited.connect(self._on_time_edited)
        self.view.row_clicked.connect(lambda r: self._select_row(r, seek=False))
        self.view.drag_started.connect(self._on_drag_started)
        self.view.drag_moved.connect(self._on_drag_moved)
        self.view.drag_finished.connect(self._on_drag_finished)

        layers_title = QLabel("LỚP PHỤ ĐỀ")
        layers_title.setObjectName("sectionTitle")
        text_title = QLabel("NỘI DUNG")
        text_title.setObjectName("sectionTitle")
        inspector = QWidget()
        inspector.setObjectName("panel")
        inspector.setMinimumWidth(280)
        right_layout = QVBoxLayout(inspector)
        right_layout.setContentsMargins(14, 12, 14, 14)
        right_layout.setSpacing(8)
        right_layout.addWidget(layers_title)
        right_layout.addWidget(self.table, 1)
        right_layout.addWidget(text_title)
        right_layout.addWidget(self.text_edit)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(preview_col)
        self.splitter.addWidget(inspector)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)

        # ---- timeline (dưới)
        self.prev_button = self._tool_button("prev", "Lùi một khung (←)", 22)
        self.play_button = self._tool_button("play", "Phát / tạm dừng (Space)", 22, "playButton")
        self.play_button.setIcon(icon("play", "#FFFFFF"))
        self.play_button.setFixedSize(40, 40)
        self.next_button = self._tool_button("next", "Tiến một khung (→)", 22)
        self.play_button.clicked.connect(self.toggle_play)
        self.prev_button.clicked.connect(lambda: self.step_frame(-1))
        self.next_button.clicked.connect(lambda: self.step_frame(+1))
        self.time_label = QLabel("00:00.000")
        self.time_label.setObjectName("timeLabel")
        self.time_total = QLabel("/ 00:00.000")
        self.time_total.setObjectName("timeTotal")

        self.kf_strip = KeyframeStrip()
        self.kf_strip.selected.connect(self._on_kf_selected)
        self.kf_strip.seek_requested.connect(self._on_strip_seek)
        self.kf_strip.drag_started.connect(self._on_kf_drag_started)
        self.kf_strip.dragged.connect(self._on_kf_dragged)
        self.kf_strip.drag_finished.connect(self._on_kf_drag_finished)
        self.add_kf_button = self._tool_button("kf_add", "Thêm keyframe tại vị trí playhead", 22, "kfButton")
        self.del_kf_button = self._tool_button("kf_del", "Xóa keyframe đang chọn", 22, "kfButton")
        self.copy_kf_button = self._tool_button("kf_copy", "Copy keyframe sang phụ đề khác", 22, "kfButton")
        self.add_kf_button.clicked.connect(self.add_keyframe_here)
        self.del_kf_button.clicked.connect(self.delete_keyframe_selected)
        self.copy_kf_button.clicked.connect(self.copy_keyframes_to_other)

        self.timeline = TimelineView()
        self.timeline.seek_requested.connect(self._seek_time)
        self.timeline.row_selected.connect(lambda r: self._select_row(r, seek=False))
        self.timeline.range_edited.connect(self._on_range_edited)
        self.timeline.zoom_changed.connect(self._on_zoom_changed)
        self.zoom_out_button = self._tool_button("minus", "Thu nhỏ timeline (Ctrl + lăn chuột)", 16)
        self.zoom_in_button = self._tool_button("plus", "Phóng to timeline (Ctrl + lăn chuột)", 16)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setObjectName("zoomSlider")
        self.zoom_slider.setRange(0, 100)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(self._slider_from_pps(DEFAULT_PPS))
        self.zoom_slider.blockSignals(False)
        self.zoom_slider.valueChanged.connect(lambda v: self.timeline.set_zoom(self._pps_from_slider(v)))
        self.zoom_out_button.clicked.connect(lambda: self.timeline.zoom_by(1 / 1.4))
        self.zoom_in_button.clicked.connect(lambda: self.timeline.zoom_by(1.4))

        # nút thêm/nhân đôi/xóa phụ đề được đổ vào đây ở _build_actions (cần các QAction)
        self.edit_tools_layout = QHBoxLayout()
        self.edit_tools_layout.setSpacing(2)

        transport = QHBoxLayout()
        transport.setSpacing(4)
        transport.addWidget(self.prev_button)
        transport.addWidget(self.play_button)
        transport.addWidget(self.next_button)
        transport.addSpacing(10)
        transport.addWidget(self.time_label)
        transport.addWidget(self.time_total)
        transport.addStretch(1)
        transport.addLayout(self.edit_tools_layout)
        transport.addSpacing(6)
        transport.addWidget(self._vsep())
        transport.addSpacing(6)
        for b in (self.add_kf_button, self.del_kf_button, self.copy_kf_button):
            transport.addWidget(b)
        transport.addStretch(1)
        transport.addWidget(self.zoom_out_button)
        transport.addWidget(self.zoom_slider)
        transport.addWidget(self.zoom_in_button)

        kf_title = QLabel("KEYFRAME")
        kf_title.setObjectName("sectionTitle")
        kf_title.setFixedWidth(88)
        kf_row = QHBoxLayout()
        kf_row.setContentsMargins(0, 0, 0, 0)
        kf_row.addWidget(kf_title)
        kf_row.addWidget(self.kf_strip, 1)

        timeline_panel = QWidget()
        timeline_panel.setObjectName("panel")
        tl = QVBoxLayout(timeline_panel)
        tl.setContentsMargins(12, 8, 12, 8)
        tl.setSpacing(6)
        tl.addLayout(transport)
        tl.addWidget(self.timeline, 1)
        tl.addLayout(kf_row)

        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.addWidget(self.splitter)
        self.vsplit.addWidget(timeline_panel)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 2)
        self.vsplit.setChildrenCollapsible(False)
        self.setCentralWidget(self.vsplit)

        self.status_video = QLabel("Chưa mở video")
        self.status_doc = QLabel("Chưa mở file .srtpx")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addWidget(self.status_video, 1)
        self.statusBar().addPermanentWidget(self.status_doc)

    @staticmethod
    def _pps_from_slider(v: int) -> float:
        return MIN_PPS * (MAX_PPS / MIN_PPS) ** (v / 100.0)

    @staticmethod
    def _slider_from_pps(pps: float) -> int:
        return round(100 * math.log(max(pps, MIN_PPS) / MIN_PPS) / math.log(MAX_PPS / MIN_PPS))

    def _on_zoom_changed(self, pps: float) -> None:
        self.zoom_slider.blockSignals(True)  # đồng bộ thanh trượt, không kích hoạt zoom lần nữa
        self.zoom_slider.setValue(self._slider_from_pps(pps))
        self.zoom_slider.blockSignals(False)

    def _build_actions(self) -> None:
        def make(text: str, icon_name: str, slot, shortcut: str | None = None, tip: str | None = None) -> QAction:
            act = QAction(text, self)
            act.setIcon(icon(icon_name))
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.setToolTip(tip or (f"{text.rstrip('…')} ({shortcut})" if shortcut else text.rstrip("…")))
            act.triggered.connect(slot)
            return act

        self.open_video_action = make("Mở video…", "folder", self.open_video, "Ctrl+O")
        self.open_srtpx_action = make("Mở .srtpx…", "file", self.open_srtpx, "Ctrl+Shift+O")
        self.save_action = make("Lưu", "save", self.save, "Ctrl+S")
        self.save_as_action = make("Lưu thành…", "save", self.save_as, "Ctrl+Shift+S")
        self.undo_action = self.undo_stack.createUndoAction(self, "Hoàn tác")
        self.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self.undo_action.setIcon(icon("undo"))
        self.redo_action = self.undo_stack.createRedoAction(self, "Làm lại")
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.redo_action.setIcon(icon("redo"))
        self.add_action = make("Thêm phụ đề", "plus", self.add_subtitle, "Ctrl+N")
        self.dup_action = make("Nhân đôi phụ đề", "copy", self.duplicate_subtitle, "Ctrl+D")
        self.del_action = make("Xóa phụ đề", "trash", self.delete_selected)
        self.del_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.del_action.setToolTip("Xóa phụ đề / keyframe đang chọn (Delete)")
        self.export_action = make("Xuất video…", "export", self.export_video, "Ctrl+E")
        self.dub_action = make("Tạo giọng đọc…", "play", self.generate_dub, tip="Lọc phụ đề và tạo giọng đọc TikTok TTS")
        self.settings_action = make("Cài đặt…", "settings", self.open_settings, tip="Cài đặt")
        quit_action = QAction("Thoát", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)

        # Thanh menu bị ẩn nên gắn mọi action vào cửa sổ để phím tắt luôn hoạt động.
        for act in (self.open_video_action, self.open_srtpx_action, self.save_action, self.save_as_action,
                    self.undo_action, self.redo_action, self.add_action, self.dup_action, self.del_action,
                    self.export_action, self.dub_action, self.settings_action, quit_action):
            self.addAction(act)

        menu = QMenu(self)
        for act in (self.open_video_action, self.open_srtpx_action, None, self.save_action, self.save_as_action,
                    None, self.undo_action, self.redo_action, None, self.add_action, self.dup_action,
                    self.del_action, None, self.export_action, self.dub_action, self.settings_action, None, quit_action):
            if act is None:
                menu.addSeparator()
            else:
                menu.addAction(act)

        # ---- thanh trên cùng: menu · tên dự án ··· mở · hoàn tác · lưu · cài đặt · [Xuất]
        bar = QToolBar("Thanh trên cùng")
        bar.setObjectName("topbar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setIconSize(QSize(22, 22))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)  # có chữ cho dễ hiểu
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

        menu_button = self._tool_button("menu", "Menu", 22)
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_button.setMenu(menu)
        bar.addWidget(menu_button)

        self.title_label = QLabel("Dự án mới")
        self.title_label.setObjectName("projectTitle")
        self.sub_label = QLabel("Chưa mở video")
        self.sub_label.setObjectName("projectSub")
        title_box = QWidget()
        title_lay = QVBoxLayout(title_box)
        title_lay.setContentsMargins(8, 0, 8, 0)
        title_lay.setSpacing(0)
        title_lay.addWidget(self.title_label)
        title_lay.addWidget(self.sub_label)
        bar.addWidget(title_box)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        for act in (self.open_video_action, self.open_srtpx_action):
            bar.addAction(act)
        bar.addSeparator()
        for act in (self.undo_action, self.redo_action):
            bar.addAction(act)
        bar.addSeparator()
        for act in (self.save_action, self.settings_action):
            bar.addAction(act)

        gap = QWidget()
        gap.setFixedWidth(8)
        bar.addWidget(gap)
        self.export_button = QToolButton()
        self.export_button.setObjectName("exportButton")
        self.export_button.setText("Xuất")
        self.export_button.setIcon(icon("export", "#FFFFFF"))
        self.export_button.setIconSize(QSize(18, 18))
        self.export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.export_button.setToolTip("Xuất video (Ctrl+E)")
        self.export_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.export_button.clicked.connect(lambda: self.export_video())
        bar.addWidget(self.export_button)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

        # ---- nút phụ đề cạnh timeline
        for act in (self.add_action, self.dup_action, self.del_action):
            b = QToolButton()
            b.setDefaultAction(act)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setIconSize(QSize(20, 20))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.edit_tools_layout.addWidget(b)

        # Phím tắt cấp cửa sổ: bảng phụ đề đang giữ focus vẫn dùng được cách/mũi tên trái-phải.
        for key, slot in (
            (Qt.Key.Key_Space, self.toggle_play),
            (Qt.Key.Key_Left, lambda: self.step_frame(-1)),
            (Qt.Key.Key_Right, lambda: self.step_frame(+1)),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

    # ------------------------------------------------------------------ cài đặt cửa sổ
    def _restore_state(self) -> None:
        self.resize(1360, 820)
        self.setMinimumSize(980, 640)
        self.splitter.setSizes([960, 340])
        self.vsplit.setSizes([520, 300])
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        for key, widget in (("window/hsplit", self.splitter), ("window/vsplit", self.vsplit)):
            state = self.settings.value(key)
            if state is not None:
                widget.restoreState(state)

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._maybe_save():
            event.ignore()
            return
        self.player.stop()
        self._cleanup_proxy()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/hsplit", self.splitter.saveState())
        self.settings.setValue("window/vsplit", self.vsplit.saveState())
        super().closeEvent(event)

    def _setting_float(self, key: str, default: float) -> float:
        try:
            return float(self.settings.value(key, default))
        except (TypeError, ValueError):
            return float(default)

    # ------------------------------------------------------------------ hộp thoại
    def _error(self, title: str, message: str) -> None:
        log.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def _show_warnings(self, title: str, lines: list[str]) -> None:
        if not lines:
            return
        shown = lines[:MAX_WARNING_LINES]
        text = "\n".join(shown)
        if len(lines) > len(shown):
            text += f"\n… và {len(lines) - len(shown)} cảnh báo khác."
        log.warning("%s: %s", title, " | ".join(lines))
        QMessageBox.warning(self, title, text)

    # ------------------------------------------------------------------ mở file
    def open_video(self) -> None:
        start = str(self.settings.value("dirs/video", ""))
        path, _ = QFileDialog.getOpenFileName(self, "Mở video", start, VIDEO_FILTER)
        if path:
            self.settings.setValue("dirs/video", str(Path(path).parent))
            self.load_video(path)

    def open_srtpx(self) -> None:
        start = str(self.settings.value("dirs/srtpx", self.settings.value("dirs/video", "")))
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Mở file .srtpx", start, SRTPX_FILTER)
        if path:
            self.settings.setValue("dirs/srtpx", str(Path(path).parent))
            self.load_srtpx(path)

    def load_video(self, path: str) -> None:
        try:
            info = probe_video(path, find_tool("ffprobe"))
        except FfmpegError as exc:
            self._error("Không mở được video", str(exc))
            return
        self.player.stop()
        self.timer.stop()
        self._cleanup_proxy()
        self.video_path, self.video_info = path, info
        self.preview = None  # mô hình cũ ứng với video cũ
        self._cursor_frame = None
        self._player_error_shown = False
        self.preview_stack.setCurrentWidget(self.view)
        self.view.set_video_size(*info.size)
        self.slider.setRange(0, int(info.duration * 1000))
        self._sync_timeline()
        source = self._make_proxy(path, info)
        self.player.setSource(QUrl.fromLocalFile(source))
        self.player.pause()  # hiện khung đầu tiên, không tự phát
        warnings = self._rebuild_preview()
        self._update_status()
        self._update_title()
        self._refresh()
        self._show_warnings("Cảnh báo về hiển thị phụ đề", warnings)

    def load_srtpx(self, path: str) -> None:
        try:
            result = read_srtpx(path)
        except SrtpxError as exc:
            self._error("Không đọc được file .srtpx", str(exc))
            return
        self.srtpx_path, self.doc = path, result.document
        self.selected_row = None
        self.kf_index = None
        self.undo_stack.clear()
        self.table_model.set_document(self.doc)
        self._sync_timeline()
        self._sync_text_panel()
        warnings = [str(w) for w in result.warnings] + self._rebuild_preview()
        self._update_status()
        self._update_title()
        self._refresh()
        self._show_warnings("Cảnh báo khi đọc / hiển thị phụ đề", warnings)
        if dub_enabled(self.settings) and dub_sessionid(self.settings):
            self.generate_dub(auto=True)  # lọc phụ đề, tạo giọng từng câu (có cache)

    def _ensure_chain(self) -> tuple[list[FontData], list[str]]:
        """Nạp chuỗi font mặc định một lần; trả về (chuỗi, cảnh báo — chỉ có ở lần đầu)."""
        if self._chain is not None:
            return self._chain, []
        specs, warnings = font_chain_from_settings(APP_DIR / "fonts", settings_overrides(self.settings))
        chain: list[FontData] = []
        for spec in specs:
            try:
                chain.append(FontData(spec))
            except FontError as exc:
                warnings.append(str(exc))
        self._chain = chain
        return chain, warnings

    def _rebuild_preview(self) -> list[str]:
        """Tính lại mô hình preview khi đã có đủ video và .srtpx; trả về các cảnh báo cần hiện."""
        self.preview = None
        if self.doc is None or self.video_info is None:
            return []
        chain, warnings = self._ensure_chain()
        self.preview = PreviewModel(
            self.doc, self.video_info.size, chain,
            margin=self._setting_float("export/margin", DEFAULT_MARGIN_PX),
        )
        return warnings + self.preview.warnings

    # ------------------------------------------------------------------ proxy 480p
    def _cleanup_proxy(self) -> None:
        if self._proxy_dir:
            shutil.rmtree(self._proxy_dir, ignore_errors=True)
            self._proxy_dir = None

    def _make_proxy(self, path: str, info: VideoInfo) -> str:
        """Trả về file để preview: proxy 480p nếu bật và tạo được, ngược lại là video gốc.

        Toạ độ vẫn theo video gốc vì khung nhìn dùng kích thước video gốc; proxy chỉ thay nguồn hình.
        """
        if str(self.settings.value("proxy/enabled", "false")).lower() not in ("true", "1") or info.height <= 480:
            return path
        try:
            ffmpeg = find_tool("ffmpeg")
        except FfmpegError as exc:
            self._error("Không tạo được proxy", str(exc))
            return path
        tmp = tempfile.mkdtemp(prefix="srtpx_proxy_")
        dst = str(Path(tmp) / "proxy.mp4")
        status, msg = run_with_progress(self, "Tạo proxy 480p", "Đang tạo bản xem nhanh 480p…",
                                        build_proxy_command(ffmpeg, path, dst), info.duration, dst)
        if status != "ok":
            shutil.rmtree(tmp, ignore_errors=True)
            if status == "error":
                self._error("Không tạo được proxy", msg + "\n\nSẽ dùng video gốc để xem.")
            return path
        self._proxy_dir = tmp
        return dst

    # ------------------------------------------------------------------ cài đặt và xuất video
    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            self._chain = None  # font có thể đã đổi
            warnings = self._rebuild_preview()
            self._refresh()
            self._show_warnings("Cảnh báo sau khi đổi cài đặt", warnings)

    # ------------------------------------------------------------------ lồng tiếng
    def _dub_cache_dir(self) -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        return Path(base or tempfile.gettempdir()) / "tts_cache"

    def generate_dub(self, auto: bool = False) -> None:
        """Lọc các phụ đề cần lồng và tạo giọng từng câu (câu đã có trong cache thì bỏ qua)."""
        if self.doc is None:
            QMessageBox.information(self, "Lồng tiếng", "Hãy mở file .srtpx trước.")
            return
        sid = dub_sessionid(self.settings)
        if not sid:
            if not auto:
                QMessageBox.information(self, "Lồng tiếng", "Chưa có sessionid TikTok. Vào Cài đặt > Lồng tiếng để nhập.")
            return
        opts = dub_options(self.settings)
        subs = select_dub_subtitles(self.doc, opts.include_comments)
        if not subs:
            return
        voice, cache = dub_voice(self.settings), self._dub_cache_dir()
        try:
            synth = tiktok_synthesizer(sid)
        except TtsError as exc:
            self._error("Lồng tiếng", str(exc))
            return
        status, msg, val = run_task_with_progress(
            self, "Tạo giọng đọc", f"Đang tạo giọng cho {len(subs)} phụ đề…",
            lambda progress, cancel: ensure_clips(subs, voice, cache, synth, progress, cancel))
        if status == "error":
            self._error("Không tạo được giọng đọc", msg)
        elif status == "ok":
            clips, errors = val
            if errors:
                self._show_warnings("Một số câu chưa tạo được giọng", errors)
            skipped = len(self.doc.subtitles) - len(subs)
            self.statusBar().showMessage(
                f"Đã có giọng cho {len(clips)}/{len(subs)} phụ đề"
                + (f" ({skipped} chú thích/trùng giờ/trống được bỏ qua)" if skipped else ""), 8000)

    def export_video(self) -> None:
        if self.video_path is None or self.video_info is None or self.doc is None:
            QMessageBox.information(self, "Xuất video", "Hãy mở cả video và file .srtpx trước khi xuất.")
            return
        info = self.video_info
        default = str(Path(self.video_path).with_name(Path(self.video_path).stem + "_che.mp4"))
        vertical_on = str(self.settings.value("export/vertical", "true")).lower() in ("true", "1")
        dlg = ExportDialog(info.duration, str(self.settings.value("dirs/export", default)), self, vertical_on)
        if not dlg.exec():
            return
        out = dlg.output_path()
        if Path(out).resolve() == Path(self.video_path).resolve():
            QMessageBox.warning(self, "Xuất video", "Không được ghi đè lên video gốc. Hãy chọn tên khác.")
            return
        self.settings.setValue("dirs/export", out)
        vertical = dlg.vertical()
        self.settings.setValue("export/vertical", vertical)
        a, b = dlg.segment()
        s = self.settings
        opts = ExportOptions(
            margin=self._setting_float("export/margin", DEFAULT_MARGIN_PX),
            crf=int(self._setting_float("export/crf", 18)), preset=str(s.value("export/preset", "veryfast")),
            threads=int(self._setting_float("export/threads", 2)), codec=str(s.value("export/codec", "libx264")),
            start=a, end=b,
        )
        workdir = Path(tempfile.mkdtemp(prefix="srtpx_"))
        try:
            ffmpeg = find_tool("ffmpeg")
            chain, font_warnings = self._ensure_chain()
            script = build_filter_script(self.doc, info, chain, opts, workdir)
            warnings = font_warnings + script.warnings
            if warnings:
                text = "\n".join(warnings[:MAX_WARNING_LINES]) + "\n\nVẫn tiếp tục xuất?"
                log.warning("Cảnh báo trước khi xuất: %s", " | ".join(warnings))
                if QMessageBox.question(self, "Cảnh báo trước khi xuất", text) != QMessageBox.StandardButton.Yes:
                    return
            write_textfiles(script)
            script_path = workdir / "filter.txt"
            script_path.write_bytes(script.text.encode("utf-8"))
            extra = script.extra_inputs(info.fps_num, info.fps_den)
            audio_map = None
            if dub_enabled(s):
                mixed = self._prepare_dub_for_export(info, workdir, ffmpeg)
                if mixed is False:
                    return  # người dùng hủy
                if mixed is not None:
                    audio_map = f"{1 + len(script.layers)}:a"
                    extra = [*extra, "-i", str(mixed)]
            stage1 = str(workdir / "che.mp4") if vertical else out
            cmd = build_command(ffmpeg, self.video_path, script_path, stage1, opts, ffmpeg_major_version(ffmpeg), script.complex,
                                 extra, audio_map)
            status, msg = run_with_progress(self, "Xuất video", "Đang xuất video…" + (" (1/2)" if vertical else ""), cmd,
                                            opts.segment_seconds(info.duration), stage1)
            if status == "ok" and vertical:
                from ..cli import vertical_command
                cmd2 = vertical_command(ffmpeg, stage1, out, workdir, opts, probe_video(stage1, find_tool("ffprobe")))
                status, msg = run_with_progress(self, "Xuất video", "Đang dựng khung 9:16… (2/2)", cmd2,
                                                opts.segment_seconds(info.duration), out)
        except (FfmpegError, FontError, OSError) as exc:
            self._error("Không xuất được video", str(exc))
            return
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if status == "ok":
            QMessageBox.information(self, "Xuất video", f"Đã xuất xong:\n{out}")
        elif status == "error":
            self._error("Không xuất được video", msg)
        else:
            QMessageBox.information(self, "Xuất video", "Đã hủy và xóa file dở.")

    def _prepare_dub_for_export(self, info: VideoInfo, workdir: Path, ffmpeg: str):
        """Dựng âm thanh đã lồng tiếng. Trả về đường dẫn file, None = xuất không lồng tiếng, False = hủy xuất."""
        assert self.doc is not None and self.video_path is not None
        sid = dub_sessionid(self.settings)
        no_dub = QMessageBox.StandardButton.Yes

        def ask(text: str):
            if QMessageBox.question(self, "Lồng tiếng", text + "\n\nXuất video KHÔNG lồng tiếng?") == no_dub:
                return None
            return False

        if not sid:
            self.statusBar().showMessage("Chưa có sessionid TikTok nên xuất không lồng tiếng (Cài đặt > Lồng tiếng).", 10000)
            return None
        try:
            synth = tiktok_synthesizer(sid)
            ffprobe = find_tool("ffprobe")
        except (TtsError, FfmpegError) as exc:
            return ask(str(exc))
        opts, voice, cache, doc, video = (dub_options(self.settings), dub_voice(self.settings),
                                          self._dub_cache_dir(), self.doc, self.video_path)
        status, msg, val = run_task_with_progress(
            self, "Lồng tiếng", "Đang tạo giọng và dựng âm thanh…",
            lambda progress, cancel: prepare_dub_audio(
                doc, video, info.duration, voice, synth, cache, workdir, opts, ffmpeg, ffprobe, progress, cancel))
        if status == "cancel":
            return False
        if status == "error":
            return ask(msg)
        mixed, report, warnings = val
        if warnings:
            text = "\n".join(warnings[:MAX_WARNING_LINES]) + "\n\n" + report.summary() + " Vẫn tiếp tục xuất?"
            if QMessageBox.question(self, "Cảnh báo lồng tiếng", text) != QMessageBox.StandardButton.Yes:
                return False
        self.statusBar().showMessage(report.summary(), 10000)
        return mixed

    # ------------------------------------------------------------------ phát và tua
    def toggle_play(self) -> None:
        if self.video_info is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self._cursor_frame = None  # từ giờ vị trí do trình phát quyết định
            self.player.play()

    def step_frame(self, delta: int) -> None:
        """Tua từng khung bằng mũi tên: position = chỉ số khung / fps (sai ±1 khung ở preview là chấp nhận được)."""
        info = self.video_info
        if info is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        k = clamp_frame(self._current_frame() + delta, info.duration, info.fps_num, info.fps_den)
        self.seek_frame(k)

    def seek_frame(self, k: int) -> None:
        info = self.video_info
        if info is None:
            return
        self._cursor_frame = k
        self.player.setPosition(position_ms_for_frame(k, info.fps_num, info.fps_den))
        self._refresh()

    def _on_slider_moved(self, ms: int) -> None:
        self._cursor_frame = None
        self.player.setPosition(ms)
        self._refresh()

    def _current_frame(self) -> int:
        info = self.video_info
        assert info is not None
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self._cursor_frame is not None and not playing:
            return self._cursor_frame
        return frame_at(self.player.position() / 1000, info.fps_num, info.fps_den)

    def _on_duration(self, ms: int) -> None:
        if ms > 0:
            self.slider.setRange(0, ms)

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setIcon(icon("pause" if playing else "play", "#FFFFFF"))
        if playing:
            self.timer.start()
        else:
            self.timer.stop()
        self._refresh()

    def _on_player_error(self, error, message: str) -> None:
        if self._player_error_shown:
            return
        self._player_error_shown = True
        self._error(
            "Không phát được video",
            "Trình phát của ứng dụng không mở được video này"
            + (f" ({message})" if message else "")
            + ".\nCó thể video dùng codec mà hệ thống chưa hỗ trợ.",
        )

    # ------------------------------------------------------------------ danh sách phụ đề
    def _select_row(self, row: int, seek: bool = True) -> None:
        """Bấm một dòng: chọn hộp tương ứng và (nếu seek) nhảy tới khung đầu tiên mà phụ đề hiển thị."""
        if self.table_model.subtitle_at(row) is None:
            return
        if row != self.selected_row:
            self._commit_text_edit()  # áp dụng chữ đang gõ dở của phụ đề cũ trước khi đổi chọn
        sub = self.table_model.subtitle_at(row)
        if sub is None:
            return
        if row != self.selected_row:
            self.kf_index = None
        self.selected_row = row
        self._sync_table_selection()
        self._sync_text_panel()
        info = self.video_info
        if info is None:
            self._update_strip(0.0)  # chưa có video: _refresh không chạy, nhưng timeline vẫn phải sáng thanh được chọn
            return
        if not seek:
            self._refresh()
            return
        rng = visible_frame_range(sub, info.fps_num, info.fps_den)
        k = rng[0] if rng else frame_at(sub.start, info.fps_num, info.fps_den)
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self.seek_frame(clamp_frame(k, info.duration, info.fps_num, info.fps_den))

    # ------------------------------------------------------------------ cập nhật hiển thị
    def _refresh(self) -> None:
        """Đồng bộ thanh tua, đồng hồ và hộp đen/chữ trắng với khung hình hiện tại."""
        info = self.video_info
        if info is None:
            return
        k = self._current_frame()
        t = frame_time(k, info.fps_num, info.fps_den)  # hộp tính đúng giờ của khung, như khi xuất
        if not self.slider.isSliderDown():
            self.slider.setValue(self.player.position())
        self.time_label.setText(format_clock(t))
        self.time_total.setText(f"/ {format_clock(info.duration)}")
        items = self.preview.items_at(t) if self.preview is not None else []
        self.view.set_overlay(items, self.selected_row, self._selected_editable())
        self._update_strip(t)

    # ------------------------------------------------------------------ chỉnh sửa (Mốc 4)
    def _selected_sub(self) -> Subtitle | None:
        return self.table_model.subtitle_at(self.selected_row) if self.selected_row is not None else None

    def _selected_editable(self) -> bool:
        """Kéo hộp dùng được cho cả hộp cố định lẫn phụ đề có keyframe (mốc 5)."""
        return self._selected_sub() is not None and self.preview is not None

    def _sync_table_selection(self) -> None:
        sm = self.table.selectionModel()
        sm.blockSignals(True)  # chọn bằng code thì không được nhảy video
        try:
            if self.selected_row is None:
                self.table.clearSelection()
            else:
                idx = self.table_model.index(self.selected_row, COL_TEXT)
                self.table.setCurrentIndex(idx)
                self.table.scrollTo(idx)
        finally:
            sm.blockSignals(False)

    def _sync_text_panel(self) -> None:
        sub = self._selected_sub()
        self.text_edit.blockSignals(True)
        self.text_edit.setEnabled(sub is not None)
        self.text_edit.setPlainText(sub.text if sub is not None else "")
        self.text_edit.blockSignals(False)

    def apply_snapshot(self, subs: list[Subtitle], row: int | None) -> None:
        """Đặt lại toàn bộ danh sách phụ đề (do lệnh undo/redo gọi) rồi làm mới mọi thứ."""
        if self.doc is None:
            return
        self.doc.subtitles[:] = clone_subtitles(subs)
        self.table_model.set_document(self.doc)
        self._sync_timeline()
        self._rebuild_preview()  # cảnh báo đã hiện lúc mở file, không hiện lại ở mỗi lần sửa
        self.selected_row = row if row is not None and 0 <= row < len(self.doc.subtitles) else None
        self.kf_index = None
        self._sync_table_selection()
        self._sync_text_panel()
        self._update_status()
        self._refresh()

    def _edit(self, text: str, mutate: Callable[[list[Subtitle]], int | None]) -> None:
        """Chạy `mutate` trên bản sao danh sách (trả về dòng cần chọn sau đó) và đẩy thành một lệnh undo."""
        if self.doc is None:
            return
        before = clone_subtitles(self.doc.subtitles)
        after = clone_subtitles(before)
        row = mutate(after)
        self.undo_stack.push(SnapshotCommand(self, text, before, after, self.selected_row, row))

    def _commit_text_edit(self) -> None:
        sub = self._selected_sub()
        if sub is None or self.selected_row is None:
            return
        new = self.text_edit.toPlainText().replace("\r\n", "\n")
        if new == sub.text:
            return
        row = self.selected_row

        def mutate(subs: list[Subtitle]) -> int:
            subs[row].text = new
            return row
        self._edit("Sửa nội dung", mutate)

    def _on_time_edited(self, row: int, col: int, seconds: float) -> None:
        # Hoãn một nhịp: model bị nạp lại khi đẩy lệnh, không được làm trong lúc delegate đang đóng ô sửa.
        QTimer.singleShot(0, lambda: self._apply_time_edit(row, col, seconds))

    def _apply_time_edit(self, row: int, col: int, seconds: float) -> None:
        sub = self.table_model.subtitle_at(row)
        if sub is None:
            return
        start, end = (seconds, sub.end) if col == COL_START else (sub.start, seconds)
        if not valid_times(start, end):
            self.statusBar().showMessage("Giờ không hợp lệ: giờ kết thúc phải sau giờ bắt đầu.", 5000)
            return
        if (start, end) == (sub.start, sub.end):
            return

        def mutate(subs: list[Subtitle]) -> int:
            subs[row].start, subs[row].end = start, end
            return row
        self._edit("Sửa giờ", mutate)

    def _res_size(self) -> tuple[float, float]:
        """Kích thước khung theo hệ toạ độ #res (hoặc video nếu không có #res)."""
        if self.doc is not None and self.doc.res:
            return float(self.doc.res[0]), float(self.doc.res[1])
        if self.video_info is not None:
            return float(self.video_info.width), float(self.video_info.height)
        return 768.0, 576.0

    def add_subtitle(self) -> None:
        if self.doc is None:
            if self.video_info is None:
                self.statusBar().showMessage("Hãy mở video hoặc file .srtpx trước.", 4000)
                return
            self.doc = SrtpxDocument(res=self.video_info.size)  # chưa có file thì tạo mới theo video
            self.table_model.set_document(self.doc)
            self._rebuild_preview()
        self._commit_text_edit()
        info = self.video_info
        t = frame_time(self._current_frame(), info.fps_num, info.fps_den) if info else (
            self.doc.subtitles[-1].end if self.doc.subtitles else 0.0)
        end = t + DEFAULT_NEW_DURATION
        if info and info.duration > t + 0.1:
            end = min(end, info.duration)
        res_size = self._res_size()

        def mutate(subs: list[Subtitle]) -> int:
            prev = next((s for s in reversed(subs) if s.start <= t), None)
            box = prev.first_box if prev is not None else default_box(res_size)
            pos = insert_position(subs, t)
            subs.insert(pos, Subtitle(0, t, end, DEFAULT_NEW_TEXT, box))
            renumber(subs)
            return pos
        self._edit("Thêm phụ đề", mutate)

    def duplicate_subtitle(self) -> None:
        if self._selected_sub() is None or self.selected_row is None:
            return
        self._commit_text_edit()
        row = self.selected_row

        def mutate(subs: list[Subtitle]) -> int:
            subs.insert(row + 1, clone_subtitle(subs[row]))
            renumber(subs)
            return row + 1
        self._edit("Nhân đôi phụ đề", mutate)

    def delete_subtitle(self) -> None:
        if self._selected_sub() is None or self.selected_row is None:
            return
        row = self.selected_row

        def mutate(subs: list[Subtitle]) -> int | None:
            del subs[row]
            renumber(subs)
            return min(row, len(subs) - 1) if subs else None
        self._edit("Xóa phụ đề", mutate)

    # -- kéo hộp trên preview
    def _on_drag_started(self, row: int) -> None:
        sub = self.table_model.subtitle_at(row)
        info = self.video_info
        if self.doc is None or sub is None or info is None:
            return
        self._drag_before = clone_subtitles(self.doc.subtitles)
        self._drag_time = frame_time(self._current_frame(), info.fps_num, info.fps_den)
        # Hộp xuất phát là hộp ĐÃ nội suy tại giờ hiện tại; kéo ở giờ chưa có keyframe sẽ tự tạo keyframe (7.2).
        self._drag_box = interpolate_box(sub, self._drag_time)
        self._drag_state = (sub.box, list(sub.keyframes))

    def _on_drag_moved(self, row: int, mode: str, dx: float, dy: float) -> None:
        sub = self.table_model.subtitle_at(row)
        if sub is None or self._drag_box is None or self.preview is None:
            return
        sc = self.preview.scale  # dx, dy tính theo điểm ảnh video thực -> đổi về toạ độ #res
        set_box_at(sub, self._drag_time, apply_drag(self._drag_box, mode, dx / sc.sx, dy / sc.sy, self._res_size()))
        self.preview.update_row(row)
        self._refresh()

    def _on_drag_finished(self, row: int) -> None:
        before, state = self._drag_before, self._drag_state
        self._drag_before = self._drag_box = self._drag_state = None
        sub = self.table_model.subtitle_at(row)
        if before is None or sub is None or self.doc is None or state is None:
            return
        if (sub.box, sub.keyframes) == state:
            return  # chỉ bấm, không kéo
        after = clone_subtitles(self.doc.subtitles)
        self.undo_stack.push(SnapshotCommand(self, "Kéo hộp", before, after, row, row))
        if sub.keyframes:
            self.kf_index = find_keyframe(sub, self._drag_time)
            self._refresh()

    # -- keyframe (mốc 5)
    def _sync_timeline(self) -> None:
        """Nạp lại các thanh phụ đề vào timeline (gọi khi danh sách hoặc giờ phụ đề đổi)."""
        subs = self.doc.subtitles if self.doc is not None else []
        duration = self.video_info.duration if self.video_info is not None else 0.0
        self.timeline.set_items([(i, s.start, s.end, s.text) for i, s in enumerate(subs)], duration)

    def _on_range_edited(self, row: int, start: float, end: float, is_move: bool) -> None:
        """Kéo/cắt thanh phụ đề trên timeline = một bước undo. Dời cả thanh thì keyframe dời theo."""
        sub = self.table_model.subtitle_at(row)
        if sub is None:
            return
        if not valid_times(start, end):
            self.statusBar().showMessage("Giờ không hợp lệ: giờ kết thúc phải sau giờ bắt đầu.", 5000)
            self._sync_timeline()
            return
        off = start - sub.start

        def mutate(subs: list[Subtitle]) -> int:
            s = subs[row]
            s.start, s.end = start, end
            if is_move and s.keyframes and abs(off) > 1e-9:
                moved: list[Keyframe] = []
                for k in s.keyframes:
                    t = round(max(0.0, k.time + off), 3)
                    if moved and t <= moved[-1].time:
                        continue  # giờ trùng sau khi kẹp về 0: giữ keyframe đầu
                    moved.append(Keyframe(t, k.box))
                s.keyframes = moved
            return row
        self._edit("Dời phụ đề" if is_move else "Cắt phụ đề", mutate)

    def _update_strip(self, t: float) -> None:
        sub, info = self._selected_sub(), self.video_info
        self.timeline.set_state(
            t, self.selected_row if sub is not None else None,
            tuple(k.time for k in sub.keyframes) if sub is not None else (),
            self.kf_index if sub is not None else None)
        if sub is None or info is None:
            self.kf_strip.set_state([], 0.0, None, t, None)
            return
        self.kf_strip.set_state([k.time for k in sub.keyframes], info.duration, self.kf_index, t, (sub.start, sub.end))

    def _seek_time(self, t: float) -> None:
        info = self.video_info
        if info is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self.seek_frame(clamp_frame(frame_at(t, info.fps_num, info.fps_den), info.duration, info.fps_num, info.fps_den))

    def _on_strip_seek(self, t: float) -> None:
        self._seek_time(t)

    def _on_kf_selected(self, i: int) -> None:
        sub = self._selected_sub()
        if sub is None or not (0 <= i < len(sub.keyframes)):
            return
        self.kf_index = i
        self._seek_time(sub.keyframes[i].time)

    def _on_kf_drag_started(self, i: int) -> None:
        sub = self._selected_sub()
        if sub is None or self.doc is None:
            return
        self._kf_before = clone_subtitles(self.doc.subtitles)
        self._kf_orig = list(sub.keyframes)

    def _on_kf_dragged(self, i: int, t: float) -> None:
        sub, info = self._selected_sub(), self.video_info
        if sub is None or self._kf_orig is None or not (0 <= i < len(sub.keyframes)):
            return
        sub.keyframes = list(self._kf_orig)
        move_keyframe(sub, i, t, info.duration if info else None)
        if self.preview is not None and self.selected_row is not None:
            self.preview.update_row(self.selected_row)
        self._refresh()

    def _on_kf_drag_finished(self, i: int) -> None:
        before, orig = self._kf_before, self._kf_orig
        self._kf_before = self._kf_orig = None
        sub = self._selected_sub()
        if before is None or sub is None or self.doc is None or self.selected_row is None or sub.keyframes == orig:
            return
        row = self.selected_row
        after = clone_subtitles(self.doc.subtitles)
        self.undo_stack.push(SnapshotCommand(self, "Đổi giờ keyframe", before, after, row, row))
        self.kf_index = i
        self._refresh()

    def add_keyframe_here(self) -> None:
        """Thêm keyframe tại giờ hiện tại, lấy đúng hộp đang hiển thị (đã nội suy)."""
        sub, info = self._selected_sub(), self.video_info
        if sub is None or info is None or self.selected_row is None:
            self.statusBar().showMessage("Hãy mở video và chọn một phụ đề trước.", 4000)
            return
        t = frame_time(self._current_frame(), info.fps_num, info.fps_den)
        if find_keyframe(sub, t) is not None:
            self.statusBar().showMessage("Đã có keyframe ở giờ này.", 4000)
            return
        self._commit_text_edit()
        row = self.selected_row

        def mutate(subs: list[Subtitle]) -> int:
            add_keyframe(subs[row], t, interpolate_box(subs[row], t))
            return row
        self._edit("Thêm keyframe", mutate)
        new_sub = self._selected_sub()
        self.kf_index = find_keyframe(new_sub, t) if new_sub is not None else None
        self._refresh()

    def delete_keyframe_selected(self) -> None:
        sub = self._selected_sub()
        if sub is None or self.selected_row is None or self.kf_index is None or not (0 <= self.kf_index < len(sub.keyframes)):
            self.statusBar().showMessage("Bấm vào một chấm keyframe để chọn trước khi xóa.", 4000)
            return
        row, i = self.selected_row, self.kf_index

        def mutate(subs: list[Subtitle]) -> int:
            delete_keyframe(subs[row], i)
            return row
        self._edit("Xóa keyframe", mutate)
        self.kf_index = None
        self._refresh()

    def delete_selected(self) -> None:
        """Phím Delete: xóa keyframe đang chọn nếu có, nếu không thì xóa phụ đề."""
        if self.kf_index is not None and self._selected_sub() is not None:
            self.delete_keyframe_selected()
        else:
            self.delete_subtitle()

    def copy_keyframes_to_other(self) -> None:
        sub = self._selected_sub()
        if sub is None or self.selected_row is None or self.doc is None or not sub.keyframes:
            self.statusBar().showMessage("Chọn một phụ đề có keyframe để sao chép.", 4000)
            return
        n, ok = QInputDialog.getInt(self, "Copy keyframe", "Số thứ tự phụ đề đích:", sub.index, 1, 1_000_000)
        if not ok:
            return
        src = self.selected_row
        dst = next((r for r, s in enumerate(self.doc.subtitles) if s.index == n), None)
        if dst is None or dst == src:
            self.statusBar().showMessage("Số thứ tự phụ đề đích không hợp lệ.", 4000)
            return
        ans = QMessageBox.question(
            self, "Copy keyframe",
            f"Chép {len(sub.keyframes)} keyframe của phụ đề {sub.index} sang phụ đề {n}?\n"
            "Keyframe/hộp cũ của phụ đề đích sẽ bị thay (giờ dời theo giờ bắt đầu của phụ đề đích).")
        if ans != QMessageBox.StandardButton.Yes:
            return

        def mutate(subs: list[Subtitle]) -> int:
            copy_keyframes(subs[src], subs[dst])
            return src
        self._edit("Copy keyframe", mutate)

    # -- lưu
    def _maybe_save(self) -> bool:
        """Hỏi lưu nếu có thay đổi chưa lưu; trả về False nếu người dùng bấm Hủy."""
        self._commit_text_edit()
        if self.doc is None or self.undo_stack.isClean():
            return True
        ans = QMessageBox.question(
            self, "Chưa lưu thay đổi", "File .srtpx có thay đổi chưa lưu. Lưu lại trước khi tiếp tục?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if ans == QMessageBox.StandardButton.Cancel:
            return False
        if ans == QMessageBox.StandardButton.Save:
            return self.save()
        return True

    def save(self) -> bool:
        if self.doc is None:
            self.statusBar().showMessage("Chưa có gì để lưu.", 4000)
            return False
        if not self.srtpx_path:
            return self.save_as()
        return self._write(self.srtpx_path)

    def save_as(self) -> bool:
        if self.doc is None:
            self.statusBar().showMessage("Chưa có gì để lưu.", 4000)
            return False
        start = self.srtpx_path or str(self.settings.value("dirs/srtpx", self.settings.value("dirs/video", "")))
        path, _ = QFileDialog.getSaveFileName(self, "Lưu file .srtpx", start, SRTPX_FILTER)
        if not path:
            return False
        if not Path(path).suffix:
            path += ".srtpx"
        self.settings.setValue("dirs/srtpx", str(Path(path).parent))
        return self._write(path)

    def _write(self, path: str) -> bool:
        self._commit_text_edit()  # chữ đang gõ dở phải vào file
        assert self.doc is not None
        try:
            save_srtpx(self.doc, path)
        except SrtpxError as exc:
            self._error("Không lưu được file", str(exc))
            return False
        self.srtpx_path = path
        self.undo_stack.setClean()
        self._update_title()
        self.statusBar().showMessage(f"Đã lưu {Path(path).name}", 4000)
        return True

    def _update_status(self) -> None:
        info = self.video_info
        if info is None:
            self.status_video.setText("Chưa mở video")
        else:
            self.status_video.setText(f"{info.width}×{info.height}, {info.fps:.3f} khung/giây, {format_clock(info.duration)}")
        if self.doc is None:
            self.status_doc.setText("Chưa mở file .srtpx")
        else:
            res = f", #res {self.doc.res[0]}x{self.doc.res[1]}" if self.doc.res else ", không có #res"
            self.status_doc.setText(f"{len(self.doc.subtitles)} phụ đề{res}")

    def _update_title(self) -> None:
        dirty = not self.undo_stack.isClean()
        parts = ["Che phụ đề cứng" + ("" if not dirty else " *")]
        if self.video_path:
            parts.append(Path(self.video_path).name)
        if self.srtpx_path:
            parts.append(Path(self.srtpx_path).name)
        self.setWindowTitle(" — ".join(parts))
        name = Path(self.srtpx_path).stem if self.srtpx_path else "Dự án mới"
        self.title_label.setText(name + ("  •" if dirty else ""))
        self.sub_label.setText(Path(self.video_path).name if self.video_path else "Chưa mở video")
