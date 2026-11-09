"""Smaller parts of the window: the screen shown before a PDF is open, the legend of the boxes, the banner
shown after saving, and showing a saved file in the system's file manager."""
import os
import sys

from PySide6.QtCore import QDir, QProcess, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from . import style
from .style import label, set_state

PRIVACY = ["Your file is never changed: the result is always a new file.",
           "Nothing is saved until you click Save. Your list is not kept after you close.",
           "Works offline: nothing leaves this computer."]


class OpenScreen(QWidget):
    """Before a PDF is open: a place to drop one, the Open button, and what the app promises."""

    openRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Desk")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 48, 16, 48)
        outer.setSpacing(28)
        outer.addStretch(1)
        self.zone = QFrame()
        self.zone.setObjectName("DropZone")
        self.zone.setMaximumWidth(560)
        zone = QVBoxLayout(self.zone)
        zone.setContentsMargins(32, 48, 32, 48)
        zone.setSpacing(14)
        icon = QLabel()
        icon.setPixmap(style.app_icon().pixmap(64, 64))
        title = label("Drop a PDF here")
        title.setObjectName("DropTitle")
        button = QPushButton("Open PDF…")
        button.setProperty("role", "primary")
        button.clicked.connect(self.openRequested.emit)
        shortcut = QKeySequence(QKeySequence.StandardKey.Open).toString(QKeySequence.SequenceFormat.NativeText)
        for w in (icon, title, label("or", "muted"), button, label(shortcut, "caption")):
            zone.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.zone, 0, Qt.AlignmentFlag.AlignHCenter)
        promises = QVBoxLayout()
        promises.setSpacing(8)
        for line in PRIVACY:
            promises.addWidget(label(line, "muted"), 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(promises)
        outer.addStretch(1)

    def set_hover(self, hover):
        set_state(self.zone, hover="true" if hover else "false")


def _swatch(kind):
    pixmap = QPixmap(18, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(2.5, 2.5, 13, 7)
    if kind == "kept":
        p.setPen(QPen(style.KEPT_EDGE, 1.2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(r, 1.5, 1.5)
    else:
        p.setPen(QPen(style.REMOVE_EDGE, 1))
        p.setBrush(style.REMOVE_FILL)
        p.drawRoundedRect(r, 1.5, 1.5)
        if kind == "ring":
            p.setPen(QPen(style.RING, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 2, 2)
    p.end()
    return pixmap


class Legend(QWidget):
    """What the marks over the pages mean."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Desk")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 0)
        row.setSpacing(6)
        row.addStretch(1)
        for kind, text in (("remove", "will be removed"), ("kept", "kept visible (you clicked it)"),
                           ("ring", "the one shown in the list")):
            swatch = QLabel()
            swatch.setPixmap(_swatch(kind))
            row.addWidget(swatch)
            row.addWidget(label(text, "caption"))
            row.addSpacing(12)
        row.addStretch(1)


def reveal_label():
    if sys.platform == "darwin":
        return "Show in Finder"
    return "Show in Explorer" if os.name == "nt" else "Open folder"


def reveal(path):
    """Show a file in the system's file manager (on Linux, open its folder)."""
    if sys.platform == "darwin":
        QProcess.startDetached("open", ["-R", path])
    elif os.name == "nt":
        QProcess.startDetached("explorer.exe", ["/select,", QDir.toNativeSeparators(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class SavedBanner(QFrame):
    """'Saved “…” in <folder>.' with a button to show the file."""

    def __init__(self):
        super().__init__()
        self.setObjectName("SavedBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 6, 8)
        row.setSpacing(10)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.show_file = QPushButton(reveal_label())
        self.show_file.setProperty("role", "link")
        self.show_file.clicked.connect(lambda: reveal(self.path))
        dismiss = QToolButton()
        dismiss.setText("×")
        dismiss.setProperty("role", "icon")
        dismiss.setAccessibleName("Dismiss")
        dismiss.clicked.connect(self.hide)
        row.addWidget(self.text, 1)
        row.addWidget(self.show_file)
        row.addWidget(dismiss)
        self.path = ""
        self.hide()

    def show_saved(self, path):
        self.path = path
        folder = os.path.basename(os.path.dirname(path)) or os.path.dirname(path)
        self.text.setText(f"Saved “{os.path.basename(path)}” in {folder}.")
        self.show()
