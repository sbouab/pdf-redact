"""A check that the app works as built, run by packaging/macos/build.sh as `PDF Redact --self-check`: the network
is blocked, the window can be made (it isn't shown), and a made-up PDF is redacted and checked by a helper process,
the way the app does it. It prints what it found; the exit status is 0 when all of it worked."""
import shutil
import socket
import tempfile
import time
import traceback
from pathlib import Path

from .. import offline

ITEM = "Jane Example"          # made up, like the rest of the PDF made here
KEPT = "555-0100"              # on the same line, not on the list: it must stay
TIMEOUT = 120                  # seconds the helper process may take


def run():
    """Run the check; returns the exit status."""
    folder = Path(tempfile.mkdtemp(prefix="pdf-redact-self-check-"))
    try:
        _check_network()
        app = _check_window()
        areas = _check_job(app, folder)
    except Exception:
        traceback.print_exc()
        print("self-check FAILED")
        return 1
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print(f"self-check passed: network blocked, window made, made-up PDF redacted by a helper process "
          f"({areas} area(s) blacked out, every check passed)")
    return 0


def _check_network():
    """The block is in place (looked at, not tried: nothing is sent)."""
    for function in (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo):
        if function.__module__ != offline.__name__:
            raise AssertionError(f"the network is not blocked: {function.__name__} is the system's own")


def _check_window():
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow
    from .workspace import Workspace
    app = QApplication.instance() or QApplication(["PDF Redact"])
    workspace = Workspace()
    try:
        window = MainWindow(workspace)
        window.shutdown()
        window.deleteLater()
    finally:
        workspace.close()
    return app


def _check_job(app, folder):
    """Redact a made-up PDF through the app's own job runner (a helper process); returns the areas blacked out."""
    import pymupdf
    from PySide6.QtCore import QEventLoop

    from .runner import JobRunner
    from .session import Session
    source = folder / "made-up.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), f"Name: {ITEM}, phone {KEPT}")
    doc.save(source)
    doc.close()
    session = Session(str(source), source.read_bytes())
    try:
        session.add_items([ITEM])
        request = session.job_request(folder / "redacted.pdf")
    finally:
        session.close()
    runner, done = JobRunner(), []
    runner.finished.connect(lambda generation, outcome: done.append(outcome))
    runner.start(1, request)
    end = time.monotonic() + TIMEOUT
    while not done:
        if time.monotonic() > end:
            runner.cancel()
            raise AssertionError(f"the helper process gave no answer in {TIMEOUT} s")
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)
    outcome = done[0]
    if outcome.result is None or outcome.result.fails:
        raise AssertionError(f"the redaction failed: {outcome.error or [str(f) for f in outcome.result.fails]}")
    if outcome.blocked:
        raise AssertionError(f"the helper process tried to reach the network: {outcome.blocked}")
    redacted = pymupdf.open(outcome.path)
    text = redacted[0].get_text()
    redacted.close()
    if ITEM in text or KEPT not in text:
        raise AssertionError(f"the redacted copy is wrong: {text!r}")
    return outcome.result.areas
