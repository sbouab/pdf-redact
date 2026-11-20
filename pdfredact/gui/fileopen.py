"""PDFs the system hands to the app. On a Mac, "Open With" and a PDF dropped on the app's Dock icon reach it as
FileOpen events sent to the application object, not as arguments: this opens them in the window."""
from PySide6.QtCore import QEvent, QObject, QTimer


class FileOpener(QObject):
    """An event filter for the application: each PDF it is handed is opened in the window (its open_path).
    Those handed over before the window exists (the app was started by one) wait for it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._window, self._waiting = None, []

    def set_window(self, window):
        self._window = window
        for path in self._waiting:
            self._open(path)
        self._waiting = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()
            if path and self._window is None:
                self._waiting.append(path)
            elif path:
                self._open(path)
            return True
        return super().eventFilter(watched, event)

    def _open(self, path):
        window = self._window
        QTimer.singleShot(0, lambda: window.open_path(path))     # not inside the event: opening shows dialogs
