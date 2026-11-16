"""Giao diện tối kiểu Alight Motion: nền than chì, điểm nhấn hồng, nút bo tròn, thanh cuộn mảnh."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# Bảng màu dùng chung cho QSS và các widget tự vẽ (timeline, keyframe strip, preview).
BG0 = "#0D0D0F"       # nền preview / vùng sâu nhất
BG1 = "#151518"       # nền cửa sổ, panel
BG2 = "#1D1D21"       # thanh công cụ, ô nhập
BG3 = "#28282E"       # hover
BG4 = "#34343C"       # nhấn
BORDER = "#2A2A30"
TEXT = "#ECECF0"
DIM = "#8E8E99"
ACCENT = "#FF3D7F"
ACCENT_HOVER = "#FF5C94"
ACCENT_PRESS = "#E02D6B"
KEYFRAME = "#FFC93C"
LANE_COLORS = ("#6C5CE7", "#17A2B8", "#F08A3C", "#2FB36E", "#D6457F", "#3D82F6")


def _palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG1, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: BG2, QPalette.ColorRole.AlternateBase: BG1,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: BG2,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: BG3,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.PlaceholderText: DIM,
        QPalette.ColorRole.BrightText: "#FFFFFF", QPalette.ColorRole.Link: ACCENT_HOVER,
        QPalette.ColorRole.Mid: BORDER, QPalette.ColorRole.Dark: BG0,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5B5B64"))
    return p


STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Inter", "Roboto", "Helvetica Neue", sans-serif; font-size: 13px; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG1}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: {BG3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; border-radius: 6px; }}

/* ---------- thanh trên cùng ---------- */
QToolBar#topbar {{ background: {BG2}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 10px; spacing: 4px; }}
QLabel#projectTitle {{ font-size: 14px; font-weight: 600; color: {TEXT}; padding: 0 12px; background: transparent; }}
QLabel#projectSub {{ color: {DIM}; font-size: 12px; background: transparent; }}

QToolButton {{ background: transparent; border: none; border-radius: 10px; padding: 7px; }}
QToolButton:hover {{ background: {BG3}; }}
QToolButton:pressed {{ background: {BG4}; }}
QToolButton:disabled {{ background: transparent; }}
QToolButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton#exportButton {{ background: {ACCENT}; color: white; font-weight: 700; padding: 7px 18px 7px 12px; border-radius: 18px; }}
QToolButton#exportButton:hover {{ background: {ACCENT_HOVER}; }}
QToolButton#exportButton:pressed {{ background: {ACCENT_PRESS}; }}
QToolButton#exportButton:disabled {{ background: {BG3}; color: #6A6A74; }}
QToolButton#playButton {{ background: {ACCENT}; border-radius: 20px; padding: 9px; }}
QToolButton#playButton:hover {{ background: {ACCENT_HOVER}; }}
QToolButton#playButton:pressed {{ background: {ACCENT_PRESS}; }}
QToolButton#playButton:disabled {{ background: {BG3}; }}
QToolButton#kfButton {{ background: {BG3}; }}
QToolButton#kfButton:hover {{ background: {BG4}; }}
QToolButton#kfButton:disabled {{ background: {BG2}; }}

QPushButton {{ background: {BG3}; border: none; border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {BG4}; }}
QPushButton:pressed {{ background: {BG2}; }}
QPushButton:default, QPushButton#primary {{ background: {ACCENT}; color: white; }}
QPushButton:default:hover, QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton:disabled {{ background: {BG2}; color: #5B5B64; }}

/* ---------- vùng preview ---------- */
QGraphicsView {{ background: {BG0}; border: none; }}
QWidget#emptyState {{ background: {BG0}; }}
QLabel#emptyTitle {{ font-size: 20px; font-weight: 700; background: transparent; }}
QLabel#emptyHint {{ color: {DIM}; background: transparent; font-size: 14px; line-height: 150%; }}

/* ---------- thanh tua mảnh dưới preview ---------- */
QSlider::groove:horizontal {{ height: 4px; background: {BG4}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HOVER}; }}
QSlider#zoomSlider::sub-page:horizontal {{ background: {DIM}; }}

/* ---------- panel lớp ---------- */
QWidget#panel {{ background: {BG1}; }}
QLabel#sectionTitle {{ color: {DIM}; font-size: 11px; font-weight: 700; letter-spacing: 1px; background: transparent; padding: 2px 0; }}
QLabel#timeLabel {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 15px; font-weight: 600; background: transparent; }}
QLabel#timeTotal {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 13px; color: {DIM}; background: transparent; }}

QTableView {{ background: {BG1}; alternate-background-color: {BG1}; border: none; outline: none; gridline-color: transparent;
             selection-background-color: transparent; }}
QTableView::item {{ padding: 0 8px; border-bottom: 1px solid {BORDER}; }}
QTableView::item:hover {{ background: {BG2}; }}
QTableView::item:selected {{ background: rgba(255, 61, 127, 60); color: white; border-bottom: 1px solid {ACCENT}; }}
QHeaderView::section {{ background: {BG1}; color: {DIM}; border: none; border-bottom: 1px solid {BORDER};
                       padding: 6px 8px; font-size: 11px; font-weight: 700; }}
QTableCornerButton::section {{ background: {BG1}; border: none; }}

QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 7px 10px;
    selection-background-color: {ACCENT}; selection-color: white; }}
QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QPlainTextEdit:disabled {{ color: #5B5B64; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {BG2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 18px; border: none; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 12px; margin-top: 14px; padding: 12px 10px 8px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {DIM}; font-weight: 700; }}

QProgressBar {{ background: {BG3}; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}

QMenu {{ background: {BG2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 28px 8px 14px; border-radius: 7px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::item:disabled {{ color: #5B5B64; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BG4}; border-radius: 4px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #4A4A54; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BG4}; border-radius: 4px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #4A4A54; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {BG0}; color: {DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {DIM}; font-size: 12px; background: transparent; }}
QStatusBar::item {{ border: none; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")  # nền ổn định giữa các máy; QSS bên dưới chỉnh phần còn lại
    app.setPalette(_palette())
    app.setStyleSheet(STYLESHEET)
    font = QFont("Segoe UI")
    font.setPixelSize(13)
    app.setFont(font)
