"""Undoable edits and the two sidebars (Review, Redacted). Run from the repository folder:  uv run pytest"""
import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QUndoStack
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from helpers import LIST, sample
from pdfredact import Finding
from pdfredact.gui.commands import AddBox, AddItems, DropAttachments, RemoveBox, RemoveItem, RenameItem, SetKept
from pdfredact.gui.result_panel import ResultPanel
from pdfredact.gui.review_panel import ReviewPanel
from pdfredact.gui.session import Box, Session
from pdfredact.gui.wording import ALWAYS_REMOVED, Fix
from pdfredact.redaction import Result


@pytest.fixture
def letter(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    s = Session(str(path), path.read_bytes())
    yield s
    s.close()


def state(s):
    return list(s.items), set(s.kept), list(s.drawn), s.drop_attachments


# ----------------------------------------------------------------------------- undo
def spouse_box(s):
    return next(b for b in s.boxes_on(1) if s.text_in(1, pymupdf.Rect(b.rect))[0] == "Marc-André Bélanger")


def test_commands_undo_redo(qapp, letter):
    first, second = Box(0, (10.0, 10.0, 90.0, 40.0), "user"), Box(0, (20.0, 20.0, 99.0, 50.0), "user")
    steps = [lambda: AddItems(letter, ["Geneviève", "H2X 1Y4", "Paul"]),
             lambda: RemoveItem(letter, "H2X 1Y4"),
             lambda: RenameItem(letter, "Paul", "  Marc-André   Bélanger "),
             lambda: SetKept(letter, spouse_box(letter), True),
             lambda: AddBox(letter, first),
             lambda: AddBox(letter, second),
             lambda: RemoveBox(letter, first),
             lambda: DropAttachments(letter),
             lambda: RemoveItem(letter, "Marc-André Bélanger")]          # its kept spot goes with it
    stack = QUndoStack()
    history = [state(letter)]
    for make in steps:
        stack.push(make())
        history.append(state(letter))
    assert letter.items == ["Geneviève"] and letter.drop_attachments
    assert letter.drawn == [second] and letter.kept == set()
    assert stack.undoText() == "Remove “Marc-André Bélanger”"
    for expected in reversed(history[:-1]):
        stack.undo()                                    # every undo puts back exactly the state before
        assert state(letter) == expected
    for expected in history[1:]:
        stack.redo()
        assert state(letter) == expected


# ----------------------------------------------------------------------------- Review sidebar
def test_review_panel_lists_items_with_counts(qapp, letter):
    letter.add_items(LIST + ["Jean Personne"])
    panel = ReviewPanel()
    panel.update_from(letter, None, 0)
    assert panel.counts.text() == "12 items · 12 boxes"
    assert panel.row("Côté-Tremblay").badge.text() == "3"
    missing = panel.row("Jean Personne")
    assert missing.badge.text() == "not found" and not missing.help.isHidden()
    assert panel.row("Côté-Tremblay").help.isHidden()
    letter.set_kept(letter.boxes_on(1)[0], True)
    panel.update_from(letter, None, 0)
    assert any("kept" in panel.row(t).badge.text() for t in letter.items)
    assert panel.counts.text() == "12 items · 11 boxes"


def test_not_found_help_shows_once(qapp, letter):
    letter.add_items(["Jean Personne", "Paul Inconnu", "Côté-Tremblay"])
    panel = ReviewPanel()
    panel.update_from(letter, None, 0)
    assert [panel.row(t).help.isHidden() for t in letter.items] == [False, True, True]
    panel.update_from(letter, "Paul Inconnu", 0)                 # the selected one, when it isn't found
    assert [panel.row(t).help.isHidden() for t in letter.items] == [True, False, True]
    panel.update_from(letter, "Côté-Tremblay", 0)
    assert [panel.row(t).help.isHidden() for t in letter.items] == [False, True, True]


def test_a_removed_row_disappears_at_once(qapp, letter):
    letter.add_items(["Paul", "Geneviève"])
    panel = ReviewPanel()
    panel.show()
    panel.update_from(letter, None, 0)
    QApplication.processEvents()                  # rows added to a shown panel are shown by a queued call
    row = panel.row("Paul")
    assert row.isVisibleTo(panel)
    letter.remove_item("Paul")
    panel.update_from(letter, None, 0)
    assert not row.isVisibleTo(panel)
    panel.close()


def test_selecting_a_row_shows_its_occurrences(qapp, letter):
    letter.add_items(["Côté-Tremblay", "Paul"])
    panel = ReviewPanel()
    panel.update_from(letter, None, 0)
    selected, steps, keeps = [], [], []
    panel.itemSelected.connect(selected.append)
    panel.stepRequested.connect(steps.append)
    panel.keepToggled.connect(lambda: keeps.append(1))
    row = panel.row("Côté-Tremblay")
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert selected == ["Côté-Tremblay"]
    panel.update_from(letter, "Côté-Tremblay", 1)
    assert not row.nav.isHidden() and row.position.text() == "2 of 3 · page 1"
    QTest.mouseClick(row.next, Qt.MouseButton.LeftButton)
    QTest.mouseClick(row.keep, Qt.MouseButton.LeftButton)
    assert steps == [1] and keeps == [1] and row.keep.text() == "Keep visible here"
    assert panel.row("Paul").nav.isHidden()


def test_remove_and_rename_from_a_row(qapp, letter):
    letter.add_items(["Paul", "Geneviève"])
    panel = ReviewPanel()
    panel.show()
    panel.update_from(letter, None, 0)
    removed, renamed = [], []
    panel.removeRequested.connect(removed.append)
    panel.renameRequested.connect(lambda old, new: renamed.append((old, new)))
    QTest.mouseClick(panel.row("Paul").remove, Qt.MouseButton.LeftButton)
    row = panel.row("Geneviève")
    QTest.mouseDClick(row.label, Qt.MouseButton.LeftButton)
    assert not row.editor.isHidden()
    row.editor.setText("Geneviève Côté")      # (QTest can only type ASCII: it crashes on accents)
    QTest.keyClick(row.editor, Qt.Key.Key_Return)
    assert removed == ["Paul"] and renamed == [("Geneviève", "Geneviève Côté")] and row.editor.isHidden()
    panel.close()


def test_typing_and_pasting_items(qapp, letter):
    panel = ReviewPanel()
    panel.show()
    panel.update_from(letter, None, 0)
    entered = []
    panel.itemsEntered.connect(entered.append)
    QTest.keyClicks(panel.input, "Paul Tremblay")
    QTest.keyClick(panel.input, Qt.Key.Key_Return)
    QGuiApplication.clipboard().setText("Côté-Tremblay\r\n046 454 286\n\n  H2X 1Y4  \n")
    panel.input.setFocus()
    QTest.keySequence(panel.input, QKeySequence(QKeySequence.StandardKey.Paste))
    assert entered == [["Paul Tremblay"], ["Côté-Tremblay", "046 454 286", "H2X 1Y4"]]
    panel.close()


def test_add_result_notes(qapp, letter):
    panel = ReviewPanel()
    panel.update_from(letter, None, 0)
    panel.input.setText("A")
    panel.show_add_result(letter.add_items(["A"]), typed=True)
    assert "too short" in panel.note.text() and panel.input.text() == "A" and not panel.note.isHidden()
    panel.input.setText("Paul")
    panel.show_add_result(letter.add_items(["Paul"]), typed=True)
    assert panel.note.isHidden() and panel.input.text() == ""
    panel.input.setText("Geneviè")                          # typed, not added yet
    panel.show_add_result(letter.add_items(["Tremblay"]))   # added from elsewhere (the page, a fix)
    assert panel.input.text() == "Geneviè"


def test_check_by_eye(qapp, tmp_path):
    path = sample(tmp_path, "scan_no_text.pdf")
    s = Session(str(path), path.read_bytes())
    panel = ReviewPanel()
    pages = []
    panel.pageRequested.connect(pages.append)
    panel.update_from(s, None, 0)
    [entry] = panel.eye_entries()
    assert entry.text.text().startswith("Page 1 has no text, only a picture")
    QTest.mouseClick(entry.link, Qt.MouseButton.LeftButton)
    assert pages == [0]
    s.add_drawn(Box(0, (10.0, 10.0, 50.0, 50.0), "user"))
    panel.update_from(s, None, 0)
    assert "boxed by you" in panel.eye_entries()[0].text.text()
    s.close()


def test_the_sidebar_says_what_every_redacted_copy_loses(qapp, letter):
    """The same for every document, whatever the list holds: all of it goes, not only what holds an item."""
    panel = ReviewPanel()
    for items in ([], ["Marc-André Bélanger"]):         # the second is also in the document's properties
        letter.add_items(items)
        panel.update_from(letter, None, 0)
        shown = [w.text() for w in panel.findChildren(QLabel) if w.isVisibleTo(panel)]
        assert ALWAYS_REMOVED in shown and not any("Also in" in text for text in shown)
    assert "properties" in ALWAYS_REMOVED and "invisible text" in ALWAYS_REMOVED


# ----------------------------------------------------------------------------- Redacted sidebar
def buttons(panel, text):
    QApplication.processEvents()          # widgets added to a shown panel are shown by a queued call
    return [b for b in panel.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(panel)]


def test_result_panel_pass_and_fail_states(qapp, letter):
    letter.add_items(["Paul"])
    panel = ResultPanel()
    panel.show()
    fixes = []
    panel.fixRequested.connect(fixes.append)
    failed = Result(areas=3, pages=1, fails=[Finding("attachments", "cli text", count=1)])
    panel.show_result(failed, letter, False, "letter_fr - REDACTED.pdf")
    assert not panel.save.isEnabled() and panel.heading.text() == "Not safe to share"
    [fix] = buttons(panel, "Remove attached files and redo")
    fix.click()
    assert fixes == [Fix("drop-attachments", "Remove attached files and redo")]
    passed = Result(areas=3, pages=1, warns=[Finding("kept-visible", "cli text", page=1, text="Paul")],
                    notes=["cleaned 1 bookmark(s)"])
    panel.show_result(passed, letter, True, "letter_fr - REDACTED.pdf")
    assert panel.save.isEnabled() and panel.heading.text() == "Passed all checks"
    assert "letter_fr - REDACTED.pdf" in panel.caption.text()
    [show] = buttons(panel, "Show")
    show.click()
    assert fixes[-1] == Fix("page", "Show", page=1)
    panel.show_running("redact", 37, 242)
    assert not panel.save.isEnabled() and panel.progress.value() == 37 and panel.progress.maximum() == 2 * 242
    panel.show_error("the original file changed since you opened it: open it again")
    assert not panel.save.isEnabled() and buttons(panel, "Try again")
    panel.show_nothing()
    assert not panel.save.isEnabled()
    panel.close()


def test_a_failed_check_can_be_ignored(qapp, letter):
    """Every failure has an Ignore button. Save turns on once each failure is ignored (and the result is current);
    an ignored failure says so, and its button takes the choice back."""
    panel = ResultPanel()
    panel.show()
    toggled = []
    panel.ignoreToggled.connect(toggled.append)
    first = Finding("attachments", "cli text", count=1)
    second = Finding("not-boxed", "cli text", page=2, text="Paul")
    failed = Result(areas=3, pages=1, fails=[first, second])
    panel.show_result(failed, letter, True, "letter_fr - REDACTED.pdf")
    assert not panel.save.isEnabled() and len(buttons(panel, "Ignore")) == 2 and not buttons(panel, "Don't ignore")
    panel.show_result(failed, letter, True, "letter_fr - REDACTED.pdf", ignored={first})
    assert not panel.save.isEnabled() and len(buttons(panel, "Don't ignore")) == 1
    [ignore] = buttons(panel, "Ignore")
    ignore.click()
    assert toggled == [second]
    panel.show_result(failed, letter, True, "letter_fr - REDACTED.pdf", ignored={first, second})
    assert panel.save.isEnabled() and panel.heading.text() == "Not safe to share"
    assert len(buttons(panel, "Don't ignore")) == 2 and "2 failed checks ignored" in panel.caption.text()
    texts = [w.text() for w in panel.findChildren(QLabel) if w.isVisibleTo(panel)]
    assert any(t.startswith("Ignored. Page 2:") for t in texts)
    buttons(panel, "Don't ignore")[0].click()
    assert toggled[-1] in (first, second)
    panel.show_result(failed, letter, False, "letter_fr - REDACTED.pdf", ignored={first, second})
    assert not panel.save.isEnabled()                        # a result that isn't current can't be saved
    panel.close()


def test_changing_state_opens_no_stray_window(qapp, letter):
    """Labels moved from one state to the next must never show on their own, as little windows."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QWidget
    panel = ResultPanel()
    panel.show()
    stray = []

    class Spy(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow() \
                    and watched is not panel:
                stray.append(watched)
            return False
    spy = Spy()
    qapp.installEventFilter(spy)
    try:
        panel.show_nothing()
        panel.show_running("redact", 1, 2)
        panel.show_result(Result(areas=1, pages=1), letter, True, "x.pdf")
        panel.show_error("oops")
        panel.show_running("check", 2, 2)
    finally:
        qapp.removeEventFilter(spy)
    assert stray == []
    panel.close()


def test_check_by_eye_entries_are_made_once_per_document(qapp, tmp_path):
    """Problems and warnings don't change while a document is open: an update only changes their tone."""
    path = sample(tmp_path, "scan_no_text.pdf")
    s = Session(str(path), path.read_bytes())
    panel = ReviewPanel()
    panel.update_from(s, None, 0)
    entries = panel.eye_entries()
    s.add_drawn(Box(0, (10.0, 10.0, 50.0, 50.0), "user"))
    panel.update_from(s, None, 0)
    assert panel.eye_entries() == entries and "boxed by you" in entries[0].text.text()
    s.remove_drawn(s.drawn[0])
    panel.update_from(s, None, 0)
    assert panel.eye_entries() == entries and "boxed by you" not in entries[0].text.text()
    s.close()
    other = Session(str(sample(tmp_path, "letter_fr.pdf")), sample(tmp_path, "letter_fr.pdf").read_bytes())
    panel.update_from(other, None, 0)
    assert panel.eye_entries() == []
    other.close()
