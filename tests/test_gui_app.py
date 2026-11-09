"""Starting the app: it refuses to run online when offline mode is required. Run:  uv run pytest"""
import sys
import tempfile

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfredact import offline
from pdfredact.gui import app


@pytest.fixture
def online(monkeypatch, tmp_path):
    """As if the computer could reach the internet; the test process's network is left alone. Should the app
    start anyway, its event loop returns at once and its private folder is made in the test's folder."""
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(offline, "block_network", lambda: "192.168.1.20")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    told = []
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: told.append(text))
    monkeypatch.delenv("REDACT_REQUIRE_OFFLINE", raising=False)
    return told


def test_refuses_to_start_online_when_offline_is_required(qapp, online):
    assert app.main(require_offline=True) == 2
    assert "offline mode was required" in online[0]


def test_the_environment_variable_also_requires_offline(qapp, online, monkeypatch):
    monkeypatch.setenv("REDACT_REQUIRE_OFFLINE", "1")
    assert app.main() == 2 and online


def test_an_error_raised_while_its_message_shows_is_only_printed(qapp, monkeypatch, capsys):
    """A drawing error repeats on every repaint, and the message itself makes the window repaint: one message,
    not a pile of them."""
    report = app.ErrorReport()
    shown = []

    def critical(parent, title, text):
        shown.append(text)
        report(RuntimeError, RuntimeError("page can't be drawn"), None)       # the repaint behind the message
    monkeypatch.setattr(QMessageBox, "critical", critical)
    report(RuntimeError, RuntimeError("page can't be drawn"), None)
    assert len(shown) == 1 and capsys.readouterr().err.count("page can't be drawn") == 2


def test_cli_has_the_gui_command():
    from pdfredact.cli import _parser
    args = _parser().parse_args(["gui", "letter.pdf", "--require-offline"])
    assert (args.command, args.pdf, args.require_offline) == ("gui", "letter.pdf", True)
    assert _parser().parse_args(["gui"]).pdf is None
