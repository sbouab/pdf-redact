"""The look from the mockups: colors, the style sheet of the app's own parts, and the app icon (drawn here, so
the app ships no image files). Standard controls keep each system's native look."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel

# boxes over the pages
REMOVE_FILL = QColor(225, 29, 29, 66)
REMOVE_EDGE = QColor(190, 20, 20, 178)
KEPT_EDGE = QColor("#6e6e73")
RING = QColor("#0a64d6")
SELECT_FILL = QColor(180, 213, 254, 150)
DRAG_EDGE = QColor("#0a64d6")
DRAG_FILL = QColor(10, 100, 214, 20)
LABEL_TEXT = QColor("#9a1515")             # the small label above a box drawn by hand
RESULT_KEPT_EDGE = QColor("#b45309")
UNREADABLE_PAGE = QColor("#f4f4f6")
NOTICE_FILL = QColor("#fff1d6")
NOTICE_EDGE = QColor("#f0c36d")
NOTICE_TEXT = QColor("#7a4a00")

STYLE_SHEET = """
QWidget#Desk { background: #dcdce0; }
QWidget#Sidebar { background: #fbfbfd; }
QFrame#SidebarEdge { background: #d4d4d8; }
QToolBar#TopBar { background: #f6f6f6; border: 0; border-bottom: 1px solid #d4d4d8; spacing: 12px; padding: 6px 12px; }
QLabel#FileName { font-weight: 600; }
QLabel[role="muted"] { color: #6e6e73; }
QLabel[role="caption"] { color: #6e6e73; font-size: 12px; }
QLabel[role="heading"] { font-size: 16px; font-weight: 600; }
QLabel[role="subheading"] { font-size: 13px; font-weight: 600; color: #48484d; }
QLabel[role="note"] { color: #6b3f00; font-size: 12px; }

QPushButton[role="primary"] { background: #0a64d6; color: white; border: 0; border-radius: 8px;
    padding: 10px 18px; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #0a5bc4; }
QPushButton[role="primary"]:disabled { background: #c9d7ea; color: #4a5568; }
QPushButton[role="secondary"] { background: white; color: #1d1d1f; border: 1px solid #c7c7cc; border-radius: 8px;
    padding: 9px 18px; }
QPushButton[role="secondary"]:hover { background: #f2f2f5; }
QPushButton[role="secondary"]:disabled { color: #a1a1a8; }
QPushButton[role="danger"] { background: #881337; color: white; border: 0; border-radius: 7px; padding: 9px 14px;
    font-weight: 600; }
QPushButton[role="danger"]:hover { background: #6f0f2c; }
QPushButton[role="segment"] { background: white; color: #1d1d1f; border: 1px solid #c7c7cc; padding: 5px 16px; }
QPushButton[role="segment"]:checked { background: #1d1d1f; color: white; font-weight: 600; }
QPushButton[role="segment"]:disabled { color: #a1a1a8; }
QPushButton[role="segment"][side="left"] { border-top-left-radius: 7px; border-bottom-left-radius: 7px; }
QPushButton[role="segment"][side="right"] { border-top-right-radius: 7px; border-bottom-right-radius: 7px;
    border-left: 0; }
QPushButton[role="link"] { background: transparent; border: 0; color: #0a5bc4; padding: 0; text-align: left; }
QPushButton[role="link"]:hover { color: #06428f; text-decoration: underline; }
QToolButton[role="icon"] { border: 0; border-radius: 5px; color: #6e6e73; padding: 1px 6px; font-size: 15px; }
QToolButton[role="icon"]:hover { background: #e5e5ea; color: #1d1d1f; }

QLineEdit#ItemInput { padding: 7px 9px; border: 1px solid #b9b9c0; border-radius: 7px; background: white; }
QLineEdit#ItemInput:focus { border: 1px solid #0a64d6; }

QFrame[role="row"] { border-radius: 7px; }
QFrame[role="row"]:hover { background: #f0f0f4; }
QFrame[role="row"][state="current"] { background: #e3eefc; }
QFrame[role="row"][state="missing"] { background: #fff4e0; }
QLabel[role="badge"] { background: #ececf0; color: #48484d; border-radius: 9px; padding: 1px 8px; font-size: 12px; }
QLabel[role="badge"][state="missing"] { background: #ffe2ad; color: #6b3f00; }
QLabel[role="nav"] { color: #0a4fa8; font-size: 12px; }

QFrame[role="card"] { border-radius: 10px; }
QFrame[role="card"][tone="pass"] { background: #e6f4ea; }
QFrame[role="card"][tone="pass"] QLabel { color: #14532d; }
QFrame[role="card"][tone="fail"] { background: #fde8ec; }
QFrame[role="card"][tone="fail"] QLabel { color: #881337; }
QFrame[role="card"][tone="busy"] { background: #eef3fb; }
QProgressBar { border: 0; border-radius: 3px; background: #d6e2f5; max-height: 6px; }
QProgressBar::chunk { border-radius: 3px; background: #0a64d6; }
QFrame[role="note"] { border-radius: 7px; }
QFrame[role="note"][tone="amber"] { background: #fff4e0; }
QFrame[role="note"][tone="amber"] QLabel { color: #5c3600; }
QFrame[role="note"][tone="gray"] { background: #f0f0f3; }
QFrame[role="note"][tone="gray"] QLabel { color: #48484d; }
QFrame[role="note"][tone="failure"] { background: white; border: 1px solid #f3b6c2; border-radius: 8px; }
QFrame#SavedBanner { background: #e6f4ea; border-radius: 8px; }
QFrame#SavedBanner QLabel { color: #14532d; }
QFrame#DropZone { background: white; border: 2px dashed #a1a1a8; border-radius: 14px; }
QFrame#DropZone[hover="true"] { border-color: #0a64d6; background: #f5f9ff; }
QLabel#DropTitle { font-size: 26px; font-weight: 600; }
"""


def label(text="", role=None, wrap=False):
    """A label styled by `role` (see the style sheet: "heading", "caption", "muted", "note"...)."""
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def set_state(widget, **properties):
    """Set dynamic properties the style sheet selects on, and re-apply the style."""
    for name, value in properties.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def app_icon():
    """A page with lines of text, one of them blacked out."""
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(_icon(size))
    return icon


def _icon(size):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64
    p.setPen(QPen(QColor("#a1a1a8"), max(1.0, 1.5 * s)))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(11 * s, 5 * s, 42 * s, 54 * s), 5 * s, 5 * s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#c7c7cc"))
    for y in (14, 22, 42, 50):
        p.drawRoundedRect(QRectF(18 * s, y * s, 28 * s, 3.5 * s), 1.5 * s, 1.5 * s)
    p.setBrush(QColor("#111111"))
    p.drawRoundedRect(QRectF(17 * s, 29.5 * s, 30 * s, 7 * s), 1 * s, 1 * s)
    p.end()
    return pixmap
