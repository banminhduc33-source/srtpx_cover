"""Điểm vào của giao diện: python -m srtpx_cover.gui"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow
from .theme import apply_theme

APP_NAME = "srtpx_cover"
log = logging.getLogger(APP_NAME)


def log_file_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base or ".") / "app.log"


def setup_logging() -> Path:
    path = log_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(path), level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.INFO)  # không ghi được file log thì thôi, không làm app chết
    return path


def install_excepthook(log_path: Path) -> None:
    """Lỗi bất ngờ: ghi stack trace ra file log, chỉ hiện thông báo tiếng Việt (không lộ stack trace)."""
    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        log.error("Lỗi không mong muốn:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "Có lỗi xảy ra",
                "Ứng dụng gặp lỗi không mong muốn. Bạn có thể tiếp tục làm việc, "
                f"nhưng nên lưu lại công việc.\n\nChi tiết kỹ thuật đã được ghi vào:\n{log_path}",
            )
    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    apply_theme(app)
    QCoreApplication.setOrganizationName(APP_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    install_excepthook(setup_logging())
    window = MainWindow()
    window.show()
    return app.exec()
