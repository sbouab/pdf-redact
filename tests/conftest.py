"""Shared test setup: Qt runs without a screen, with one application object for every test."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # before Qt is loaded


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["pdf-redact tests"])
