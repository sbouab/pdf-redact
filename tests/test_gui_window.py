"""The whole app in one window: open, edit the list and the boxes, redact in the background, save.
Dialogs are replaced by recorded answers. Run from the repository folder:  uv run pytest"""
import hashlib
import multiprocessing
import os

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

import pdfredact.gui.window as window_module
from helpers import LIST, encrypted, let_events_run, page_text, sample, wait_until, with_attachment
from pdfredact.gui.window import MainWindow
from pdfredact.gui.workspace import Workspace


@pytest.fixture
def app_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(window_module, "BACKGROUND_DELAY_MS", 150)
    workspace = Workspace(tmp_path)
    w = MainWindow(workspace)
    w.told, w.asked = [], []
    w._tell = lambda title, text: w.told.append((title, text))
    w._ask = lambda title, text, yes: w.asked.append((title, text)) or True
    w.resize(1200, 800)
    w.show()
    yield w
    assert w.close(), "the window refused to close: it would be destroyed with its edits at exit"
    workspace.close()
    assert not workspace.path.exists()


def opened(w, tmp_path, name="letter_fr.pdf"):
    path = sample(tmp_path, name)
    assert w.open_path(str(path))
    return path


def click_box(w, box):
    widget = w.viewer.page_widget(box.page)
    w.viewer.scroll_to(box.page, pymupdf.Rect(box.rect))
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.to_widget(pymupdf.Rect(box.rect)).center().toPoint())


def drag(w, page, rect):
    widget = w.viewer.page_widget(page)
    w.viewer.scroll_to(page, rect)
    area = widget.to_widget(rect)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=area.topLeft().toPoint())
    QTest.mouseMove(widget, (area.topLeft() + QPointF(10, 10)).toPoint())
    QTest.mouseMove(widget, area.bottomRight().toPoint())
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=area.bottomRight().toPoint())


def test_full_flow_saves_a_checked_file(app_window, tmp_path):
    w = app_window
    original = opened(w, tmp_path)
    before = original.read_bytes()
    w.add_items(LIST)
    spouse = next(b for b in w.session.boxes_on(1)
                  if w.session.text_in(1, pymupdf.Rect(b.rect))[0] == "Marc-André Bélanger")
    click_box(w, spouse)
    assert spouse in w.session.kept
    wait_until(w.result_ready)                         # redacted in the background, still in Review
    assert w.view == "review"
    w.show_view("redacted")
    assert w.result_panel.heading.text() == "Passed all checks" and w.result_panel.save.isEnabled()
    out = tmp_path / "shared" / "letter - REDACTED.pdf"
    out.parent.mkdir()
    assert w.save_to(str(out))
    text = page_text(out)
    assert "Marc-André Bélanger" in text and "Côté-Tremblay" not in text and "Nous avons examiné" in text
    assert hashlib.sha256(out.read_bytes()).hexdigest() == w.last_result_sha256()
    assert original.read_bytes() == before
    assert list(w.workspace.path.glob("*.pdf")) == []          # the checked copy lives in memory only
    assert not w.saved_banner.isHidden()


def test_save_refuses_the_original_and_stale_results(app_window, tmp_path):
    w = app_window
    original = opened(w, tmp_path)
    before = original.read_bytes()
    w.add_items(["Côté-Tremblay"])
    wait_until(w.result_ready)
    assert not w.save_to(str(original)) and original.read_bytes() == before
    assert "original" in w.told[-1][1]
    w.add_items(["046 454 286"])                                # the result no longer matches the list
    assert not w.result_ready() and not w.save_to(str(tmp_path / "other.pdf"))
    assert not (tmp_path / "other.pdf").exists()


def ignore_buttons(w):
    QApplication.processEvents()          # widgets added to a shown panel are shown by a queued call
    return [b for b in w.result_panel.findChildren(QPushButton)
            if b.text() == "Ignore" and b.isVisibleTo(w.result_panel)]


def test_failed_checks_can_be_ignored_to_save_anyway(app_window, tmp_path, monkeypatch):
    """A failed check keeps Save off until the user ignores it. Saving then asks first, naming what failed. The
    choice holds for that copy only: after an edit, the new copy is checked, and a failure must be ignored again."""
    w = app_window
    path = sample(tmp_path, "letter_fr.pdf")
    with_attachment(path)                                        # attached files can't be checked: a failure
    assert w.open_path(str(path))
    w.add_items(["Côté-Tremblay"])
    w.show_view("redacted")
    wait_until(w.result_ready)
    out = tmp_path / "anyway.pdf"
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: str(out))
    assert w.result_panel.heading.text() == "Not safe to share" and not w.result_panel.save.isEnabled()
    assert not w.save_to(str(out)) and not out.exists()
    for button in ignore_buttons(w):
        button.click()
    assert w.result_panel.save.isEnabled() and not ignore_buttons(w)
    accept, w._ask = w._ask, lambda title, text, yes: (w.asked.append((title, text)), False)[1]
    w.save()
    assert not out.exists()                                      # not confirmed: nothing saved
    w._ask = accept
    w.save()
    assert out.exists() and hashlib.sha256(out.read_bytes()).hexdigest() == w.last_result_sha256()
    assert [title for title, _ in w.asked] == ["Save without passing every check?"] * 2
    assert "attached file" in w.asked[-1][1]
    w.add_items(["046 454 286"])                                 # a new copy, checked again
    wait_until(w.result_ready)
    assert not w.result_panel.save.isEnabled() and ignore_buttons(w)
    assert not w.save_to(str(tmp_path / "again.pdf"))


def test_an_edit_cancels_the_running_job_and_starts_again(app_window, tmp_path):
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Côté-Tremblay"])
    wait_until(w.runner.is_running)
    first = w.runner.generation
    w.add_items(["046 454 286"])
    assert w.runner.generation != first
    wait_until(w.result_ready)
    assert w.result_generation() == w.session.generation


def test_save_from_review_redacts_then_asks_where(app_window, tmp_path, monkeypatch):
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Côté-Tremblay"])
    target = tmp_path / "chosen.pdf"
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: str(target))
    w.save()
    assert w.view == "redacted"
    wait_until(target.exists)
    assert "Côté-Tremblay" not in page_text(target)


def test_drag_over_text_adds_it_to_the_list(app_window, tmp_path, monkeypatch):
    w = app_window
    path = opened(w, tmp_path)
    doc = pymupdf.open(path)
    phone = doc[0].search_for("514 555-0199")[0]
    doc.close()
    monkeypatch.setattr(w, "_pick", lambda menu, pos: next(a for a in menu.actions() if a.text().startswith("Add")))
    drag(w, 0, phone + (-2, -2, 2, 2))
    assert w.session.items == ["514 555-0199"] and w.session.found("514 555-0199") == 1


def test_drag_over_nothing_draws_a_box_and_a_click_removes_it(app_window, tmp_path):
    w = app_window
    opened(w, tmp_path)
    drag(w, 0, pymupdf.Rect(300, 600, 450, 700))
    [box] = w.session.drawn
    assert box.kind == "user" and box.page == 0
    click_box(w, box)
    assert w.session.drawn == []
    w.undo.undo()
    assert w.session.drawn == [box]


def test_open_another_pdf_keeps_the_list(app_window, tmp_path):
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Tremblay", "046 454 286"])
    drag(w, 0, pymupdf.Rect(300, 600, 450, 700))
    opened(w, tmp_path / "next", "t4_slip.pdf")
    assert w.asked                                               # the drawn box would be lost: asked first
    assert w.session.name == "t4_slip.pdf" and w.session.items == ["Tremblay", "046 454 286"]
    assert w.session.drawn == [] and w.session.found("046 454 286") == 1


def test_reading_another_pdf_starts_no_job_for_the_one_it_replaces(app_window, tmp_path, monkeypatch):
    """Reading a big PDF lets the window run (its progress shows): the edits made just before must not start a
    redaction of the PDF about to be closed. If the new one can't be opened, the old one is redacted as usual."""
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Tremblay"])                                    # the background job waits for a pause in edits
    assert w._idle.isActive() and not w.runner.is_running()
    real_session, jobs, started = window_module.Session, w._jobs, []

    def slow_read(*args, **kwargs):
        let_events_run(0.5)                                      # longer than the pause before the job
        started.append(w._jobs - jobs)
        return real_session(*args, **kwargs)
    monkeypatch.setattr(window_module, "Session", slow_read)
    not_a_pdf = tmp_path / "notes.pdf"
    not_a_pdf.write_bytes(b"just some text")
    assert not w.open_path(str(not_a_pdf)) and w.told
    assert started == [0] and w._idle.isActive()                 # resumed for the PDF still open
    opened(w, tmp_path / "next", "t4_slip.pdf")
    assert started == [0, 0]


def test_password_is_asked_until_right(app_window, tmp_path, monkeypatch):
    w = app_window
    answers = iter(["wrong", "s3cret"])
    asked = []
    monkeypatch.setattr(w, "_ask_password", lambda name, wrong: asked.append(wrong) or next(answers))
    assert w.open_path(str(encrypted(tmp_path)))
    assert asked == [False, True] and w.session.page_count == 1


def test_closing_during_a_job_leaves_nothing(qapp, tmp_path):
    workspace = Workspace(tmp_path)
    w = MainWindow(workspace)
    w._ask = lambda title, text, yes: True
    w.show()
    opened(w, tmp_path)
    w.add_items(LIST)
    w.show_view("redacted")
    assert w.runner.is_running()
    w.close()
    assert not w.runner.is_running() and multiprocessing.active_children() == []
    workspace.close()
    assert not workspace.path.exists()


def test_an_edit_cancels_a_pending_save(app_window, tmp_path, monkeypatch):
    """Save clicked, then more edits before the result is ready: no Save window pops up later by surprise."""
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Côté-Tremblay"])
    asked = []
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: asked.append(suggested))
    w.save()
    w.show_view("review")
    w.add_items(["046 454 286"])
    wait_until(w.result_ready)
    assert asked == []


def test_going_back_to_review_cancels_a_pending_save(app_window, tmp_path, monkeypatch):
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Côté-Tremblay"])
    asked = []
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: asked.append(suggested))
    w.save()                                        # the job runs: Save waits for it
    w.show_view("review")
    wait_until(w.result_ready)
    assert asked == []


def test_save_from_review_shows_the_result_first(app_window, tmp_path, monkeypatch):
    """Even with the result ready, Save goes through the Redacted view, where its warnings show."""
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Côté-Tremblay"])
    wait_until(w.result_ready)
    asked = []
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: asked.append(suggested))
    w.save()
    assert w.view == "redacted" and len(asked) == 1


def test_the_suggested_name_is_never_the_opened_file(app_window, tmp_path, monkeypatch):
    """A command-line result opened to redact more: "<x> - REDACTED.pdf" would be the original itself."""
    w = app_window
    named = sample(tmp_path, "letter_fr.pdf").rename(tmp_path / "letter - REDACTED.pdf")
    assert w.open_path(str(named))
    w.add_items(["Côté-Tremblay"])
    wait_until(w.result_ready)
    asked = []
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: asked.append(suggested))
    w.save()
    [suggested] = asked
    assert suggested.endswith(".pdf") and os.path.abspath(suggested) != os.path.abspath(named)


def test_a_name_without_pdf_never_replaces_a_file_unasked(app_window, tmp_path, monkeypatch):
    """A Save window may return a name without ".pdf": the name completed must not replace a file unasked."""
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Côté-Tremblay"])
    wait_until(w.result_ready)
    existing = tmp_path / "report.pdf"
    existing.write_bytes(b"keep me")
    monkeypatch.setattr(w, "_ask_save_path", lambda suggested: str(tmp_path / "report"))
    w._ask = lambda title, text, yes: w.asked.append((title, text)) or title != "Replace the file?"   # "no"
    w.save()
    assert existing.read_bytes() == b"keep me" and not (tmp_path / "report").exists()
    assert w.asked[-1][0] == "Replace the file?" and "report.pdf" in w.asked[-1][1]


def test_adding_from_the_page_keeps_what_is_typed(app_window, tmp_path):
    w = app_window
    opened(w, tmp_path)
    w.review_panel.input.setText("1234, rue des")               # typed, not added yet
    w.add_items(["Côté-Tremblay"])                              # e.g. chosen after a drag over the page
    assert w.review_panel.input.text() == "1234, rue des"


def test_undoing_a_spot_kept_over_two_lines_redacts_once(app_window, tmp_path, monkeypatch):
    """Keeping a spot that spans two lines changes two boxes in one edit: undone in the Redacted view, it
    starts one job, not one per box."""
    w = app_window
    opened(w, tmp_path)
    w.add_items(["Geneviève Côté-Tremblay"])
    w.select_item("Geneviève Côté-Tremblay")
    w.step(1)
    assert len(w.current_match().rects) == 2
    w.toggle_current()
    w.show_view("redacted")
    starts = []
    real_start = w.runner.start
    monkeypatch.setattr(w.runner, "start", lambda *a: starts.append(a[0]) or real_start(*a))
    w.undo.undo()
    assert len(starts) == 1
