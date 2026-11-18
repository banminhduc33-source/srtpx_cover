"""Hộp thoại mốc 6: Cài đặt, tùy chọn xuất, và chạy ffmpeg có thanh tiến trình + nút Hủy."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QProgressDialog, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.dub import DEFAULT_MAX_SPEED, DubOptions
from ..core.dubfilter import DEFAULT_MODEL as DEFAULT_GEMINI_MODEL, Classifier, DubFilterError, gemini_classifier
from ..core.ffmpeg_tools import FfmpegCancelled, FfmpegError, run_ffmpeg
from ..core.geometry import DEFAULT_MARGIN_PX
from ..core.tts import DEFAULT_VOICE, VOICES, TtsCancelled, TtsError

log = logging.getLogger(__name__)
FONT_FILTER = "Font (*.ttf *.otf *.ttc);;Tất cả các file (*.*)"
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"]


def _float(s: QSettings, key: str, default: float) -> float:
    try:
        return float(s.value(key, default))
    except (TypeError, ValueError):
        return float(default)


def _int(s: QSettings, key: str, default: int) -> int:
    return int(_float(s, key, default))


def settings_overrides(s: QSettings) -> list[str]:
    return [str(s.value(f"fonts/{k}", "") or "") for k in ("primary", "vi", "cjk")]


def _bool(s: QSettings, key: str, default: bool) -> bool:
    return str(s.value(key, default)).lower() in ("true", "1")


def dub_enabled(s: QSettings) -> bool:
    return _bool(s, "dub/enabled", True)


def dub_sessionid(s: QSettings) -> str:
    """sessionid trong Cài đặt; trống thì lấy biến môi trường TIKTOK_SESSIONID."""
    import os
    return (str(s.value("dub/sessionid", "") or "").strip() or os.environ.get("TIKTOK_SESSIONID", "").strip())


def dub_voice(s: QSettings) -> str:
    return str(s.value("dub/voice", DEFAULT_VOICE) or DEFAULT_VOICE).strip()


def dub_ai_enabled(s: QSettings) -> bool:
    return _bool(s, "dub/ai_filter", False)


def dub_gemini_key(s: QSettings) -> str:
    """API key Gemini trong Cài đặt; trống thì lấy biến môi trường GEMINI_API_KEY."""
    import os
    return (str(s.value("dub/gemini_key", "") or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip())


def dub_gemini_model(s: QSettings) -> str:
    return str(s.value("dub/gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL).strip()


def dub_classifier(s: QSettings) -> tuple[Classifier | None, str | None]:
    """(hàm lọc Gemini hoặc None, cảnh báo hoặc None). Tắt lọc AI -> (None, None); bật mà thiếu key -> (None, lý do)."""
    if not dub_ai_enabled(s):
        return None, None
    try:
        return gemini_classifier(dub_gemini_key(s), dub_gemini_model(s)), None
    except DubFilterError as exc:
        return None, str(exc)


def dub_options(s: QSettings) -> DubOptions:
    return DubOptions(
        include_comments=_bool(s, "dub/comments", False),
        max_speed=_float(s, "dub/max_speed", DEFAULT_MAX_SPEED),
        orig_volume=_float(s, "dub/orig_volume", 30) / 100.0,
        voice_volume=_float(s, "dub/voice_volume", 100) / 100.0,
        ai_model=dub_gemini_model(s),
    )


# ------------------------------------------------------------------ Cài đặt
class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cài đặt")
        self.settings = settings
        s = settings

        self.margin = QDoubleSpinBox(); self.margin.setRange(0, 1); self.margin.setDecimals(1)
        self.margin.setSuffix(" px"); self.margin.setValue(_float(s, "export/margin", DEFAULT_MARGIN_PX))

        self.codec = QComboBox(); self.codec.addItems(["libx264", "libx265"])
        self.codec.setCurrentText(str(s.value("export/codec", "libx264")))
        self.crf = QSpinBox(); self.crf.setRange(0, 51); self.crf.setValue(_int(s, "export/crf", 18))
        self.preset = QComboBox(); self.preset.addItems(PRESETS)
        self.preset.setCurrentText(str(s.value("export/preset", "veryfast")))
        self.threads = QSpinBox(); self.threads.setRange(1, 32); self.threads.setValue(_int(s, "export/threads", 2))

        self.proxy = QCheckBox("Dùng proxy 480p khi xem/chỉnh sửa (xuất vẫn dùng video gốc)")
        self.proxy.setChecked(str(s.value("proxy/enabled", "false")).lower() in ("true", "1"))

        self.dub_on = QCheckBox("Lồng tiếng: tự tạo giọng khi mở .srtpx và trộn vào video khi xuất")
        self.dub_on.setChecked(dub_enabled(s))
        self.sid = QLineEdit(str(s.value("dub/sessionid", "") or ""))
        self.sid.setEchoMode(QLineEdit.EchoMode.Password)
        self.sid.setPlaceholderText("Cookie sessionid của tiktok.com (hoặc biến môi trường TIKTOK_SESSIONID)")
        self.voice = QComboBox(); self.voice.setEditable(True)
        self.voice.addItems([f"{k}  ({v})" for k, v in VOICES.items()])
        cur = dub_voice(s)
        idx = next((i for i, k in enumerate(VOICES) if k == cur), -1)
        if idx >= 0:
            self.voice.setCurrentIndex(idx)
        else:
            self.voice.setEditText(cur)
        self.dub_floating = QCheckBox("Lồng cả chú thích (có *, trong ngoặc) và block trùng giờ — mặc định bỏ qua")
        self.dub_floating.setChecked(_bool(s, "dub/comments", False))
        self.ai_on = QCheckBox("Lọc bằng AI (Gemini): bỏ dòng không cần lồng như tiếng động, chữ trên màn hình")
        self.ai_on.setChecked(dub_ai_enabled(s))
        self.gemini_key = QLineEdit(str(s.value("dub/gemini_key", "") or ""))
        self.gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key.setPlaceholderText("API key Gemini (hoặc biến môi trường GEMINI_API_KEY)")
        self.gemini_model = QLineEdit(dub_gemini_model(s))
        self.orig_vol = QSpinBox(); self.orig_vol.setRange(0, 200); self.orig_vol.setSuffix(" %")
        self.orig_vol.setValue(_int(s, "dub/orig_volume", 30))
        self.voice_vol = QSpinBox(); self.voice_vol.setRange(0, 300); self.voice_vol.setSuffix(" %")
        self.voice_vol.setValue(_int(s, "dub/voice_volume", 100))
        self.max_speed = QDoubleSpinBox(); self.max_speed.setRange(1.0, 10.0); self.max_speed.setDecimals(1)
        self.max_speed.setSingleStep(0.5); self.max_speed.setPrefix("x")
        self.max_speed.setValue(_float(s, "dub/max_speed", DEFAULT_MAX_SPEED))
        dub_box = QGroupBox("Lồng tiếng (TikTok TTS)"); f3 = QFormLayout(dub_box)
        f3.addRow(self.dub_on)
        f3.addRow("sessionid", self.sid); f3.addRow("Giọng", self.voice)
        f3.addRow(self.dub_floating)
        f3.addRow(self.ai_on)
        f3.addRow("Gemini API key", self.gemini_key); f3.addRow("Model Gemini", self.gemini_model)
        f3.addRow("Âm thanh gốc", self.orig_vol); f3.addRow("Giọng đọc", self.voice_vol)
        f3.addRow("Tua nhanh tối đa", self.max_speed)

        self.font_edits: list[QLineEdit] = []
        font_box = QGroupBox("Font (để trống = mặc định)")
        font_form = QFormLayout(font_box)
        for key, label, hint in (("primary", "Font chính", "Bevan-Regular.ttf trong thư mục fonts/"),
                                 ("vi", "Dự phòng tiếng Việt", "Arial hoặc Segoe UI"),
                                 ("cjk", "Dự phòng Trung/Nhật/Hàn", "Microsoft YaHei (msyh.ttc)")):
            edit = QLineEdit(str(s.value(f"fonts/{key}", "") or "")); edit.setPlaceholderText(hint)
            btn = QPushButton("Chọn…"); btn.clicked.connect(lambda _=False, e=edit: self._browse(e))
            row = QHBoxLayout(); row.addWidget(edit, 1); row.addWidget(btn)
            font_form.addRow(label, row)
            self.font_edits.append(edit)

        form_box = QGroupBox("Hộp và chữ"); f1 = QFormLayout(form_box)
        f1.addRow("Lề an toàn", self.margin)
        enc_box = QGroupBox("Mã hóa video"); f2 = QFormLayout(enc_box)
        f2.addRow("Codec", self.codec); f2.addRow("CRF", self.crf); f2.addRow("Preset", self.preset)
        f2.addRow("Số luồng", self.threads)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save); buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (form_box, font_box, enc_box, dub_box, self.proxy, buttons):
            lay.addWidget(w)
        self.setMinimumWidth(520)

    def _browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn font", edit.text(), FONT_FILTER)
        if path:
            edit.setText(path)

    def _save(self) -> None:
        s = self.settings
        s.setValue("export/margin", self.margin.value())
        s.setValue("export/codec", self.codec.currentText()); s.setValue("export/crf", self.crf.value())
        s.setValue("export/preset", self.preset.currentText()); s.setValue("export/threads", self.threads.value())
        s.setValue("proxy/enabled", self.proxy.isChecked())
        s.setValue("dub/enabled", self.dub_on.isChecked()); s.setValue("dub/sessionid", self.sid.text().strip())
        s.setValue("dub/voice", (self.voice.currentText().split() or [DEFAULT_VOICE])[0])
        s.setValue("dub/comments", self.dub_floating.isChecked())
        s.setValue("dub/ai_filter", self.ai_on.isChecked()); s.setValue("dub/gemini_key", self.gemini_key.text().strip())
        s.setValue("dub/gemini_model", self.gemini_model.text().strip() or DEFAULT_GEMINI_MODEL)
        s.setValue("dub/orig_volume", self.orig_vol.value()); s.setValue("dub/voice_volume", self.voice_vol.value())
        s.setValue("dub/max_speed", self.max_speed.value())
        for key, edit in zip(("primary", "vi", "cjk"), self.font_edits):
            s.setValue(f"fonts/{key}", edit.text().strip())
        self.accept()


# ------------------------------------------------------------------ Tùy chọn xuất
class ExportDialog(QDialog):
    """Chọn nơi lưu và đoạn cần xuất (cả video hoặc từ giây A tới giây B)."""

    def __init__(self, duration: float, default_path: str, parent: QWidget | None = None, vertical: bool = True,
                 main_captions: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Xuất video")
        self.vertical_box = QCheckBox("Dựng khung 9:16 (nền mờ tối 35% + logo GenshinViet.net)")
        self.vertical_box.setChecked(vertical)
        self.main_box = QCheckBox("Chuẩn hóa phụ đề chính: hộp đen ngang theo file, dọc trung bình, chữ 20px, 5 chữ mỗi lần (phụ đề phụ giữ nguyên)")
        self.main_box.setChecked(main_captions)
        self.path = QLineEdit(default_path)
        browse = QPushButton("Chọn…"); browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.path, 1); row.addWidget(browse)
        self.whole = QRadioButton("Cả video"); self.whole.setChecked(True)
        self.part = QRadioButton("Một đoạn:")
        self.a = QDoubleSpinBox(); self.b = QDoubleSpinBox()
        for sp, v in ((self.a, 0.0), (self.b, duration)):
            sp.setRange(0, duration); sp.setDecimals(3); sp.setSuffix(" s"); sp.setValue(v); sp.setEnabled(False)
        self.part.toggled.connect(lambda on: (self.a.setEnabled(on), self.b.setEnabled(on)))
        seg = QHBoxLayout(); seg.addWidget(self.part); seg.addWidget(self.a); seg.addWidget(QLabel("đến")); seg.addWidget(self.b)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Xuất")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Đóng")
        buttons.accepted.connect(self._check); buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(row); lay.addWidget(self.whole); lay.addLayout(seg); lay.addWidget(self.main_box); lay.addWidget(self.vertical_box); lay.addWidget(buttons)
        self.setMinimumWidth(520)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Lưu video", self.path.text(), "Video MP4 (*.mp4);;Tất cả (*.*)")
        if path:
            self.path.setText(path)

    def _check(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        if not self.path.text().strip():
            QMessageBox.warning(self, "Xuất video", "Hãy chọn nơi lưu video."); return
        if self.part.isChecked() and self.b.value() <= self.a.value():
            QMessageBox.warning(self, "Xuất video", "Giây kết thúc phải lớn hơn giây bắt đầu."); return
        self.accept()

    def output_path(self) -> str:
        return self.path.text().strip()

    def vertical(self) -> bool:
        return self.vertical_box.isChecked()

    def main_captions(self) -> bool:
        return self.main_box.isChecked()

    def segment(self) -> tuple[float | None, float | None]:
        return (self.a.value(), self.b.value()) if self.part.isChecked() else (None, None)


# ------------------------------------------------------------------ Chạy ffmpeg có tiến trình + Hủy
class _Worker(QObject):
    progress = Signal(float)
    done = Signal(str, str)  # (trạng thái: ok|cancel|error, thông báo)

    def __init__(self, cmd: list[str], total: float, cancel: threading.Event) -> None:
        super().__init__()
        self.cmd, self.total, self.cancel = cmd, total, cancel

    def run(self) -> None:
        try:
            run_ffmpeg(self.cmd, self.total, self.progress.emit, self.cancel)
            self.done.emit("ok", "")
        except FfmpegCancelled:
            self.done.emit("cancel", "")
        except FfmpegError as exc:
            self.done.emit("error", str(exc))
        except Exception:  # noqa: BLE001 - không lộ stack trace ra giao diện
            log.exception("Lỗi không mong muốn khi chạy ffmpeg")
            self.done.emit("error", "Có lỗi không mong muốn khi chạy ffmpeg. Chi tiết đã được ghi vào file log.")


def run_with_progress(parent: QWidget, title: str, label: str, cmd: list[str], total: float,
                      output: str | Path | None = None) -> tuple[str, str]:
    """Chạy ffmpeg ở luồng riêng, hiện thanh tiến trình có nút Hủy. Trả về (ok|cancel|error, thông báo).

    Hủy hoặc lỗi thì xóa `output` (file dở).
    """
    cancel = threading.Event()
    dlg = QProgressDialog(label, "Hủy", 0, 1000, parent)
    dlg.setWindowTitle(title); dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0); dlg.setAutoClose(False); dlg.setAutoReset(False); dlg.setValue(0)
    dlg.canceled.connect(cancel.set)

    thread, worker, loop = QThread(), _Worker(cmd, total, cancel), QEventLoop()
    result: list[tuple[str, str]] = []
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(lambda p: dlg.setValue(int(p * 1000)))
    worker.done.connect(lambda st, msg: (result.append((st, msg)), thread.quit()))
    thread.finished.connect(loop.quit)
    thread.start(); dlg.show()
    loop.exec()
    thread.wait(); dlg.close()
    status = result[0] if result else ("error", "Tiến trình ffmpeg kết thúc bất thường.")
    if status[0] != "ok" and output:
        Path(output).unlink(missing_ok=True)
    return status


# ------------------------------------------------------------------ Chạy tác vụ Python bất kỳ có tiến trình + Hủy
class _TaskWorker(QObject):
    progress = Signal(float)
    done = Signal(str, str, object)  # (ok|cancel|error, thông báo, kết quả)

    def __init__(self, fn, cancel: threading.Event) -> None:
        super().__init__()
        self.fn, self.cancel = fn, cancel

    def run(self) -> None:
        try:
            self.done.emit("ok", "", self.fn(self.progress.emit, self.cancel))
        except (FfmpegCancelled, TtsCancelled):
            self.done.emit("cancel", "", None)
        except (FfmpegError, TtsError) as exc:
            self.done.emit("error", str(exc), None)
        except Exception:  # noqa: BLE001
            log.exception("Lỗi không mong muốn khi chạy tác vụ nền")
            self.done.emit("error", "Có lỗi không mong muốn. Chi tiết đã được ghi vào file log.", None)


def run_task_with_progress(parent: QWidget, title: str, label: str, fn) -> tuple[str, str, object]:
    """Chạy `fn(report_progress, cancel_event)` ở luồng riêng với thanh tiến trình + nút Hủy.

    Trả về (ok|cancel|error, thông báo lỗi, giá trị fn trả về).
    """
    cancel = threading.Event()
    dlg = QProgressDialog(label, "Hủy", 0, 1000, parent)
    dlg.setWindowTitle(title); dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0); dlg.setAutoClose(False); dlg.setAutoReset(False); dlg.setValue(0)
    dlg.canceled.connect(cancel.set)

    thread, worker, loop = QThread(), _TaskWorker(fn, cancel), QEventLoop()
    result: list[tuple[str, str, object]] = []
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(lambda p: dlg.setValue(int(p * 1000)))
    worker.done.connect(lambda st, msg, val: (result.append((st, msg, val)), thread.quit()))
    thread.finished.connect(loop.quit)
    thread.start(); dlg.show()
    loop.exec()
    thread.wait(); dlg.close()
    return result[0] if result else ("error", "Tác vụ kết thúc bất thường.", None)
