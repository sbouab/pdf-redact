"""Starting the app: the offline check, the network block, the private folder, Qt, and the window."""
import multiprocessing
import os
import sys
import traceback

from .. import offline

NOT_OFFLINE = ("This computer can reach the internet and offline mode was required, so no file was opened. Turn "
               "off Wi-Fi or unplug the cable, then start the app again.")


class ErrorReport:
    """The app's sys.excepthook: print an unexpected error and show it in a message. An error raised while that
    message is open (a drawing error repeats on every repaint, and the message makes the window repaint) is only
    printed, so messages never pile up."""

    def __init__(self):
        self.showing = False

    def __call__(self, kind, error, tb):
        traceback.print_exception(kind, error, tb)
        if self.showing:
            return
        from PySide6.QtWidgets import QMessageBox
        self.showing = True
        try:
            QMessageBox.critical(None, "Something went wrong", f"{kind.__name__}: {error}\n\nYour original file was "
                                 "not changed. If this happens again, close the app and start it again.")
        finally:
            self.showing = False


def main(path=None, require_offline=False):
    """Run the app until its window closes; `path` is a PDF to open at once. Returns the exit status."""
    multiprocessing.freeze_support()
    route = offline.block_network()             # before Qt is loaded (redact.py did it before PyMuPDF)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .style import STYLE_SHEET, app_icon
    from .window import APP_NAME, MainWindow
    from .workspace import Workspace

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("pdf-redact")
    app.setApplicationDisplayName(APP_NAME)
    if hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)     # the design is light; native controls follow
    app.setWindowIcon(app_icon())
    app.setStyleSheet(STYLE_SHEET)
    if route and (require_offline or os.environ.get("REDACT_REQUIRE_OFFLINE") == "1"):
        QMessageBox.critical(None, "Not offline", NOT_OFFLINE)
        return 2

    sys.excepthook = ErrorReport()

    Workspace.remove_leftovers()
    workspace = Workspace()
    window = None
    try:
        window = MainWindow(workspace)
        window.show()
        if path:
            window.open_path(path)
        return app.exec()
    finally:
        if window is not None:
            window.shutdown()
        workspace.close()
