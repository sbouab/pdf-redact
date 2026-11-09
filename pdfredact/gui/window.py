"""The main window: opening a PDF, the Review and Redacted views, saving, and the wiring between the session,
the sidebars, the pages and the redaction job.

Every edit goes through the undo stack, and every change of the stack (an edit, an undo, a redo, a group of
commands at once) ends in _edited(): the job running for an older state is cancelled, and a new one starts in the
background once edits pause (BACKGROUND_DELAY_MS), or at once in the Redacted view. A result is shown, and can be
saved, only for the edit generation it was made from.
"""
import contextlib
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QProgressDialog, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import PasswordError, UserError, offline
from ..geometry import union
from ..naming import REDACTED, output_path
from ..redaction import Result
from . import commands
from .layers import ResultLayer, ReviewLayer
from .result_panel import ResultPanel
from .review_panel import ReviewPanel
from .runner import JobRunner
from .session import Box, Cancelled, Session
from .style import set_state
from .viewer import PageView
from .widgets import Legend, OpenScreen, SavedBanner
from .wording import plural, quoted, sentence

APP_NAME = "PDF Redact"
BACKGROUND_DELAY_MS = 1200      # quiet time after the last edit before the redacted copy is made in the background
MIN_BOX = 4.0                   # points: a smaller box dragged over nothing is taken for a slip of the mouse
REVIEW_HINT = "Select text to add it · Click a red box to keep that spot · Drag to box anything else"
REDACTED_HINT = "Redacted view: what the saved file will look like"
PENDING_HINT = "Redacting: the red boxes show what is being blacked out"


def _tidy(selected):
    """Selected text without the punctuation a generous drag picks up at its ends ("(514 555-0199)." gives
    "514 555-0199"). Matching ignores punctuation between the parts of an item, so nothing is lost."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", selected)


def _same_file(a, b):
    a, b = os.path.abspath(a), os.path.abspath(b)
    if os.path.normcase(a) == os.path.normcase(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


@dataclass
class _Done:
    """A finished job: the edit generation it was made from and its outcome. When it produced a file, the file's
    bytes (the exact bytes that were checked) and a document opened from them for the pages."""
    generation: int
    result: Result | None = None
    error: str = ""
    data: bytes = b""
    sha256: str = ""
    doc: pymupdf.Document | None = None

    def passed(self):
        return self.result is not None and not self.result.fails

    def close(self):
        if self.doc is not None:
            self.doc.close()
            self.doc = None


class MainWindow(QMainWindow):
    """The app's only window. `workspace` is the private folder for the job's output."""

    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.session = None
        self.view = "review"
        self.undo = QUndoStack(self)
        self.undo.indexChanged.connect(lambda _index: self._edited())
        self.runner = JobRunner(self)
        self.runner.progress.connect(self._job_progress)
        self.runner.finished.connect(self._job_finished)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(BACKGROUND_DELAY_MS)
        self._idle.timeout.connect(self._redact_in_background)
        self._done = None               # the last finished job (_Done)
        self._jobs = 0                  # jobs started: each writes to a file of its own
        self._progress = ("redact", 0, 1)
        self._save_when_ready = False
        self._saved_generation = None
        self._current, self._occurrence = None, 0      # the item selected in the list, and the place shown
        self._showing = None            # what the pages show, to redraw them only when it changes
        self._blocked = []              # network attempts blocked in the helper processes
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setMinimumSize(900, 600)
        self.resize(1280, 800)
        self._build_actions()
        self._build_toolbar()
        self._build_body()
        self._build_status_bar()
        self._build_menus()
        self._update_actions()
        self._watch = QTimer(self)
        self._watch.setInterval(2000)
        self._watch.timeout.connect(self._show_blocked)
        self._watch.start()

    # ================================================================== building
    def _action(self, text, slot, shortcuts=(), role=None):
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcuts:
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
        if role is not None:
            action.setMenuRole(role)
        return action

    def _build_actions(self):
        keys = QKeySequence.StandardKey
        self.open_action = self._action("Open…", self.open_dialog, [keys.Open])
        self.save_action = self._action("Save Redacted PDF…", self.save, [keys.Save])
        self.close_action = self._action("Close Window", self.close, [keys.Close])
        self.quit_action = self._action("Quit", self.close, [keys.Quit], QAction.MenuRole.QuitRole)
        self.undo_action = self.undo.createUndoAction(self, "Undo")
        self.undo_action.setShortcuts(QKeySequence.keyBindings(keys.Undo))
        self.redo_action = self.undo.createRedoAction(self, "Redo")
        self.redo_action.setShortcuts(QKeySequence.keyBindings(keys.Redo))
        self.find_action = self._action("Add to the List", self._focus_input, [keys.Find])
        self.review_action = self._action("Review", lambda: self.show_view("review"), ["Ctrl+1"])
        self.redacted_action = self._action("Redacted", lambda: self.show_view("redacted"), ["Ctrl+2"])
        self.zoom_in_action = self._action("Zoom In", lambda: self.viewer.zoom_in(), [keys.ZoomIn, "Ctrl+="])
        self.zoom_out_action = self._action("Zoom Out", lambda: self.viewer.zoom_out(), [keys.ZoomOut])
        self.fit_action = self._action("Fit Width", lambda: self.viewer.fit_width(), ["Ctrl+0"])
        self.next_action = self._action("Next Place Found", lambda: self.step(1), [keys.FindNext])
        self.previous_action = self._action("Previous Place Found", lambda: self.step(-1), [keys.FindPrevious])
        self.about_action = self._action(f"About {APP_NAME}", self._about, role=QAction.MenuRole.AboutRole)

    def _build_menus(self):
        bar = self.menuBar()
        menus = {"&File": [self.open_action, self.save_action, None, self.close_action, self.quit_action],
                 "&Edit": [self.undo_action, self.redo_action, None, self.find_action],
                 "&View": [self.review_action, self.redacted_action, None, self.zoom_in_action, self.zoom_out_action,
                           self.fit_action, None, self.next_action, self.previous_action],
                 "&Help": [self.about_action]}
        for title, actions in menus.items():
            menu = bar.addMenu(title)
            for action in actions:
                if action is None:
                    menu.addSeparator()
                else:
                    menu.addAction(action)

    def _build_toolbar(self):
        bar = QToolBar("Document")
        bar.setObjectName("TopBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.toggleViewAction().setVisible(False)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.file_label = QLabel(APP_NAME)
        self.file_label.setObjectName("FileName")
        self.pages_label = QLabel()
        self.pages_label.setProperty("role", "muted")
        self.review_button = QPushButton("Review")
        self.redacted_button = QPushButton("Redacted")
        segments = QWidget()
        row = QHBoxLayout(segments)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        group = QButtonGroup(self)
        for button, side, view in ((self.review_button, "left", "review"), (self.redacted_button, "right", "redacted")):
            button.setCheckable(True)
            button.setProperty("role", "segment")
            button.setProperty("side", side)
            button.clicked.connect(lambda _=False, v=view: self.show_view(v))
            group.addButton(button)
            row.addWidget(button)
        self.review_button.setChecked(True)
        self.save_button = QPushButton("Save redacted PDF…")
        self.save_button.setProperty("role", "secondary")
        self.save_button.clicked.connect(self.save)
        name = QWidget()
        names = QHBoxLayout(name)
        names.setContentsMargins(4, 0, 0, 0)
        names.setSpacing(6)
        names.addWidget(self.file_label)
        names.addWidget(self.pages_label)
        for w in (name, self._spacer(), segments, self._spacer(), self.save_button):
            bar.addWidget(w)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.setUnifiedTitleAndToolBarOnMac(True)

    @staticmethod
    def _icon_button(text, name, slot):
        button = QToolButton()
        button.setText(text)
        button.setProperty("role", "icon")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.clicked.connect(slot)
        return button

    @staticmethod
    def _spacer():
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return spacer

    def _build_body(self):
        self.stack = QStackedWidget()
        self.open_screen = OpenScreen()
        self.open_screen.openRequested.connect(self.open_dialog)
        self.stack.addWidget(self.open_screen)

        column = QWidget()
        column.setObjectName("Desk")
        column.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pages = QVBoxLayout(column)
        pages.setContentsMargins(0, 0, 0, 0)
        pages.setSpacing(0)
        self.saved_banner = SavedBanner()
        banner = QHBoxLayout()
        banner.setContentsMargins(16, 12, 16, 0)
        banner.addWidget(self.saved_banner)
        pages.addLayout(banner)
        self.legend = Legend()
        pages.addWidget(self.legend)
        self.viewer = PageView()
        self.viewer.boxClicked.connect(self._box_clicked)
        self.viewer.areaDragged.connect(self._area_dragged)
        self.viewer.currentPageChanged.connect(self._show_page_number)
        self.viewer.zoomChanged.connect(lambda percent: self.zoom_label.setText(f"{percent}%"))
        pages.addWidget(self.viewer, 1)

        self.review_panel = ReviewPanel()
        self.review_panel.itemsEntered.connect(lambda texts: self.add_items(texts, typed=True))
        self.review_panel.removeRequested.connect(self.remove_item)
        self.review_panel.renameRequested.connect(self.rename_item)
        self.review_panel.itemSelected.connect(self.select_item)
        self.review_panel.stepRequested.connect(self.step)
        self.review_panel.keepToggled.connect(self.toggle_current)
        self.review_panel.pageRequested.connect(lambda page: self.viewer.scroll_to(page))
        self.review_panel.redactRequested.connect(lambda: self.show_view("redacted"))
        self.result_panel = ResultPanel()
        self.result_panel.saveRequested.connect(self.save)
        self.result_panel.backRequested.connect(lambda: self.show_view("review"))
        self.result_panel.retryRequested.connect(self._retry)
        self.result_panel.fixRequested.connect(self._apply_fix)
        self.sidebar = QStackedWidget()
        self.sidebar.addWidget(self.review_panel)
        self.sidebar.addWidget(self.result_panel)
        self.sidebar.setMinimumWidth(300)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.addWidget(column)
        split.addWidget(self.sidebar)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([900, 340])
        self.stack.addWidget(split)
        self.setCentralWidget(self.stack)

    def _build_status_bar(self):
        status = self.statusBar()
        self.page_label = QLabel("No document open")
        zoom = QWidget()
        row = QHBoxLayout(zoom)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.zoom_label = QLabel("100%")
        row.addWidget(self._icon_button("−", "Zoom out", lambda: self.viewer.zoom_out()))
        row.addWidget(self.zoom_label)
        row.addWidget(self._icon_button("+", "Zoom in", lambda: self.viewer.zoom_in()))
        self.zoom_controls = zoom
        self.hint_label = QLabel()
        self.job_label = QLabel()
        self.job_label.setProperty("role", "muted")
        self.offline_label = QLabel("Offline · network blocked")
        self.offline_label.setToolTip("The app blocks every network connection: nothing leaves this computer.")
        status.addWidget(self.page_label)
        status.addWidget(zoom)
        status.addWidget(self.hint_label, 1)
        status.addPermanentWidget(self.job_label)
        status.addPermanentWidget(self.offline_label)

    # ================================================================== opening
    def open_dialog(self):
        folder = os.path.dirname(self.session.path) if self.session else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open a PDF", folder, "PDF files (*.pdf)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        """Open the PDF at `path`. The list stays when another PDF was open. Returns True once it is open."""
        path = os.path.abspath(path)
        s = self.session
        if s is not None and (s.kept or s.drawn) and not self._saved():
            if not self._ask("Open another PDF?", f"The list stays, but the spots you kept and the boxes you drew on "
                                                  f"“{s.name}” are lost.", "Open"):
                return False
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            self._tell("Can't open this file", f"“{os.path.basename(path)}” can't be read ({e.strerror or e}).")
            return False
        # Reading lets the window run (its progress shows): the pending background job must not start for the PDF
        # about to be replaced. It resumes if that PDF stays open.
        resume = self._idle.isActive()
        self._idle.stop()
        session = None
        try:
            session = self._read(path, data)
        finally:
            if session is None and resume:
                self._idle.start()
        if session is None:
            return False
        items = list(s.items) if s is not None else []
        self._close_session()
        self.session = session
        if items:
            session.add_items(items)
        self._current, self._occurrence = None, 0
        self._saved_generation = None
        self.file_label.setText(session.name)
        self.pages_label.setText(f"· {plural(session.page_count, 'page')}")
        self.setWindowTitle(f"{session.name} – {APP_NAME}")
        self.stack.setCurrentIndex(1)
        self.show_view("review")
        self._edited()
        return True

    def _read(self, path, data):
        """The bytes of a PDF as a Session, asking for its password as long as needed. None if it can't be opened
        or the user gave up."""
        name = os.path.basename(path)
        password, wrong = None, False
        while True:
            dialog = QProgressDialog(f"Reading “{name}”…", "Cancel", 0, 0, self)
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(500)

            def progress(done, total):
                dialog.setMaximum(total)
                dialog.setValue(done)
                if dialog.wasCanceled():
                    raise Cancelled()
            try:
                return Session(path, data, password, progress)
            except PasswordError:
                password = self._ask_password(name, wrong)
                if password is None:
                    return None
                wrong = True
            except Cancelled:
                return None
            except UserError as e:
                self._tell("Can't open this PDF", sentence(str(e)))
                return None
            finally:
                dialog.reset()                  # also stops its timer, which would show it during a password prompt
                dialog.close()
                dialog.deleteLater()

    def _close_session(self):
        """Stop the job (which deletes its output), forget the result and the edits, and close the document."""
        self._idle.stop()
        self.runner.cancel()
        self._drop_result()
        self._show_pages(None, None, None)
        if self.session is not None:
            self.session.close()
            self.session = None
        self.undo.clear()               # its commands belong to the document closed

    # ================================================================== edits
    def add_items(self, texts, typed=False):
        """Add items to the list; `typed` when they come from the add field, which is then emptied."""
        if self.session is None:
            return
        result = self.session.preview_items(texts)
        if result.added:
            self.undo.push(commands.AddItems(self.session, result.added))
        self.review_panel.show_add_result(result, typed)

    def remove_item(self, text):
        self.undo.push(commands.RemoveItem(self.session, text))

    def rename_item(self, old, new):
        result = self.session.preview_items([new], ignore=old)
        if result.added:
            if self._current == old:
                self._current = result.added[0]
            self.undo.push(commands.RenameItem(self.session, old, new))
        else:
            self.review_panel.show_add_result(result)
            self._refresh()

    def toggle_current(self):
        """Keep visible, or black out again, the place shown for the selected item (every line it spans)."""
        match = self.current_match()
        if match is None:
            return
        s = self.session
        boxes = s.boxes_of(match)
        if not boxes:
            return
        kept = not all(b in s.kept for b in boxes)
        self.undo.beginMacro("Keep Spot Visible" if kept else "Black Out Spot Again")
        for b in boxes:
            if (b in s.kept) != kept:
                self.undo.push(commands.SetKept(s, b, kept))
        self.undo.endMacro()

    def _box_clicked(self, box):
        if box.kind == "user":
            self.undo.push(commands.RemoveBox(self.session, box))
            self._undo_tip("Box deleted")
        else:
            self.undo.push(commands.SetKept(self.session, box, box not in self.session.kept))

    def _area_dragged(self, page, rect, where):
        """A rectangle dragged on a page: over text, offer to add the text to the list or to box only that spot;
        over nothing, box it."""
        text = _tidy(self.session.text_in(page, rect)[0])
        if not text:
            if rect.width >= MIN_BOX and rect.height >= MIN_BOX:
                self._draw_box(page, rect)
            return
        menu = QMenu(self)
        check = self.session.preview_items([text])
        if check.added:
            add = menu.addAction(f"Add {quoted(text)} to the list · found "
                                 f"{plural(self.session.count_matches(text), 'time')}")
        else:
            add = menu.addAction(f"{quoted(text)} is already on the list" if check.duplicates
                                 else f"{quoted(text)} is too short to search for")
            add.setEnabled(False)
        box = menu.addAction("Box this spot only")
        chosen = self._pick(menu, where)
        menu.deleteLater()
        if chosen is add and check.added:
            self.add_items([text])
        elif chosen is box:
            self._draw_box(page, rect)

    def _draw_box(self, page, rect):
        self.undo.push(commands.AddBox(self.session, Box.around(page, rect, "user")))

    def _apply_fix(self, fix):
        if fix.kind == "drop-attachments":
            self.undo.push(commands.DropAttachments(self.session))
        elif fix.kind == "add-item":
            self.add_items([fix.text])
        elif fix.kind == "page":
            self.viewer.scroll_to(fix.page - 1)

    def _edited(self):
        """After every edit, undo and redo: redraw, and redact again (soon, or now in the Redacted view)."""
        s = self.session
        if s is None:
            return
        if self._current not in s.items:
            self._current, self._occurrence = None, 0
        self._save_when_ready = False           # Save was asked for the state before this edit
        self.saved_banner.hide()
        self._refresh()
        if self.runner.is_running() and self.runner.generation != s.generation:
            self.runner.cancel()
        if self.view == "redacted":
            self._show_result_or_run()
        else:
            self._idle.start()
        self._update_actions()
        self._update_job_label()

    def _refresh(self):
        self.review_panel.update_from(self.session, self._current, self._occurrence)
        self.viewer.refresh()

    # ================================================================== the list and its places
    def select_item(self, text):
        self._current, self._occurrence = text, 0
        self._show_occurrence()
        self._refresh()

    def step(self, delta):
        """Show the next (+1) or previous (-1) place where the selected item is found."""
        s = self.session
        if s is None:
            return
        if self._current is None:
            first = next((item for item in s.items if s.found(item)), None)
            if first is not None:
                self.select_item(first)
            return
        found = s.found(self._current)
        if found:
            self._occurrence = (self._occurrence + delta) % found
            self._show_occurrence()
            self._refresh()

    def current_match(self):
        """The place shown for the selected item (a Match), or None."""
        s = self.session
        if s is None or self._current not in s.items:
            return None
        places = s.occurrences(self._current)
        return places[min(self._occurrence, len(places) - 1)] if places else None

    def _show_occurrence(self):
        match = self.current_match()
        if match is None:
            return
        if self.view != "review":
            self.show_view("review")
        self.viewer.scroll_to(match.page, union(match.rects))

    def _focus_input(self):
        if self.session is not None:
            self.show_view("review")
            self.review_panel.focus_input()

    # ================================================================== views
    def show_view(self, name):
        """Show the Review or the Redacted view."""
        if self.session is None:
            return
        self.view = name
        review = name == "review"
        self.review_button.setChecked(review)
        self.redacted_button.setChecked(not review)
        self.sidebar.setCurrentWidget(self.review_panel if review else self.result_panel)
        self.legend.setVisible(review)
        if review:
            self._save_when_ready = False           # back to editing: a Save asked for before waits no more
            self._show_pages("review", self.session.doc, ReviewLayer(self.session, self.current_match))
            self.hint_label.setText(REVIEW_HINT)
            if not self._fresh():
                self._idle.start()
        else:
            self._idle.stop()
            self._show_result_or_run()
        self._update_actions()

    def _show_pages(self, key, doc, layer):
        """Show `doc` with `layer`; when `key` didn't change, only redraw (the pages stay where they are)."""
        if key == self._showing and doc is not None:
            self.viewer.set_layer(layer)
            return
        self._showing = key
        self.viewer.set_document(doc, layer, keep_place=True)

    def _show_result_or_run(self):
        s = self.session
        if not s.has_work():
            self.result_panel.show_nothing()
            self._show_pages(("nothing", s.generation), s.doc, None)
            self.hint_label.setText(REDACTED_HINT)
            return
        if self._fresh():
            self._show_done()
            return
        if not self._running_current():
            self._start_job()
        self.result_panel.show_running(*self._progress)
        self._show_pages(("pending", s.generation), s.doc, ReviewLayer(s, self.current_match, interactive=False))
        self.hint_label.setText(PENDING_HINT)

    def _show_done(self):
        s, done = self.session, self._done
        if done.error:
            self.result_panel.show_error(done.error)
            self._show_pages(("pending", s.generation), s.doc, ReviewLayer(s, self.current_match, interactive=False))
        else:
            self.result_panel.show_result(done.result, s, True, os.path.basename(self._suggested_path()))
            self._show_pages(("result", done.generation), done.doc, ResultLayer(s))
        self.hint_label.setText(REDACTED_HINT)

    # ================================================================== the redaction job
    def _fresh(self):
        return self.session is not None and self._done is not None and self._done.generation == self.session.generation

    def _running_current(self):
        return self.runner.is_running() and self.runner.generation == self.session.generation

    def result_ready(self):
        """True when the redacted copy (passed or failed) is ready for the current state."""
        return self._fresh()

    def result_generation(self):
        return None if self._done is None else self._done.generation

    def last_result_sha256(self):
        return None if self._done is None else self._done.sha256

    def _redact_in_background(self):
        s = self.session
        if s is not None and s.has_work() and not self._fresh() and not self._running_current():
            self._start_job()

    def _start_job(self):
        self._jobs += 1
        out = self.workspace.file(f"redacted-{self._jobs}.pdf")
        self._progress = ("redact", 0, self.session.page_count)
        self.runner.start(self.session.generation, self.session.job_request(out))      # cancels the one running
        self._update_job_label()

    def _retry(self):
        self._drop_result()
        self._show_result_or_run()

    def _job_progress(self, stage, done, total):
        self._progress = (stage, done, total)
        if self.view == "redacted" and self._running_current():
            self.result_panel.show_running(stage, done, total)

    def _job_finished(self, generation, outcome):
        self._blocked += outcome.blocked
        s = self.session
        if s is None or generation != s.generation:
            if outcome.path:
                self.workspace.remove(outcome.path)
            return
        done = _Done(generation, outcome.result, outcome.error)
        if outcome.result is not None:
            try:
                data = Path(outcome.path).read_bytes()
            except OSError as e:
                done = _Done(generation, error=f"the redacted copy can't be read back ({e.strerror or e})")
            else:
                if hashlib.sha256(data).hexdigest() != outcome.sha256:
                    done = _Done(generation, error="the redacted copy changed after it was checked: try again")
                else:
                    done.data, done.sha256 = data, outcome.sha256
                    done.doc = pymupdf.open(stream=data, filetype="pdf")
            finally:
                self.workspace.remove(outcome.path)     # from now on the checked copy lives in memory only
        self._drop_result()
        self._done = done
        self._update_job_label()
        if self.view == "redacted":
            self._show_done()
        if self._save_when_ready:
            self._save_when_ready = False
            if done.passed():
                self._save_dialog()

    def _drop_result(self):
        if self._done is not None:
            self._done.close()
            self._done = None

    def _update_job_label(self):
        s = self.session
        if s is None or not s.has_work():
            text = ""
        elif self._fresh():
            text = ("Redacted copy ready" if self._done.passed() else
                    "Redacted copy: a check failed" if self._done.result is not None else "Redacted copy: an error")
        elif self._running_current():
            text = "Preparing the redacted copy…"
        else:
            text = ""
        self.job_label.setText(text)

    # ================================================================== saving
    def save(self):
        """Save the redacted PDF. It is shown first (the Redacted view, with its warnings); the Save window opens
        at once when the current result passed, or as soon as it does."""
        s = self.session
        if s is None or not s.has_work():
            return
        self.show_view("redacted")
        if not self._fresh():
            self._save_when_ready = True
        elif self._done.passed():
            self._save_dialog()

    def _save_dialog(self):
        path = self._ask_save_path(self._suggested_path())
        if not path:
            return
        if not path.lower().endswith(".pdf"):           # a Save window that didn't add it: asked about another name
            path += ".pdf"
            if os.path.exists(path) and not self._ask("Replace the file?", f"“{os.path.basename(path)}” already "
                                                      "exists. Replace it with the redacted copy?", "Replace"):
                return
        self.save_to(path)

    def _suggested_path(self):
        """"<name> - REDACTED.pdf" next to the original; when that is the original itself (one redacted with the
        command line, opened to redact more), "<name> - REDACTED (2).pdf"."""
        path = output_path(self.session.path, REDACTED)
        if _same_file(path, self.session.path):
            path = path[:-len(".pdf")] + " (2).pdf"
        return path

    def save_to(self, path):
        """Write the checked bytes of the current result to `path` (they were compared with the checked file's
        SHA-256 when read back). Refused for the original, or when there is no passing result for the current
        state. Returns True once saved."""
        s, done = self.session, self._done
        if s is None or not self._fresh() or not done.passed():
            self._tell("Nothing checked to save", "The redacted copy isn't ready yet, or it didn't pass every check.")
            return False
        if _same_file(path, s.path):
            self._tell("Choose another name", "This is your original file, which is never changed. Save the "
                                              "redacted copy under another name.")
            return False
        try:
            _write_new_file(path, done.data)
        except OSError as e:
            self._tell("Couldn't save", f"“{os.path.basename(path)}” couldn't be written ({e.strerror or e}). "
                                        "If it is open in another app, close it and try again.")
            return False
        self._saved_generation = s.generation
        self.saved_banner.show_saved(os.path.abspath(path))
        return True

    def _saved(self):
        return self.session is not None and self._saved_generation == self.session.generation

    # ================================================================== window events
    def closeEvent(self, event):
        s = self.session
        if s is not None and s.has_work() and not self._saved():
            if not self._ask("Close without saving?", "Your list and boxes are not kept after you close, and the "
                                                      "redacted PDF hasn't been saved.", "Close"):
                event.ignore()
                return
        self.shutdown()
        event.accept()

    def shutdown(self):
        """Stop the job and close the documents (the workspace itself is deleted by whoever made it)."""
        self._watch.stop()
        self._close_session()

    def dragEnterEvent(self, event):
        if _dropped_pdf(event.mimeData()):
            event.acceptProposedAction()
            self.open_screen.set_hover(True)

    def dragLeaveEvent(self, event):
        self.open_screen.set_hover(False)

    def dropEvent(self, event):
        self.open_screen.set_hover(False)
        path = _dropped_pdf(event.mimeData())
        if path:
            event.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.open_path(path))      # not inside the drop: the system waits on it

    # ================================================================== small things
    def _update_actions(self):
        has = self.session is not None
        work = has and self.session.has_work()
        for action in (self.find_action, self.review_action, self.redacted_action, self.zoom_in_action,
                       self.zoom_out_action, self.fit_action, self.next_action, self.previous_action):
            action.setEnabled(has)
        self.save_action.setEnabled(work)
        self.save_button.setEnabled(work)
        for w in (self.review_button, self.redacted_button, self.zoom_controls):
            w.setEnabled(has)

    def _show_page_number(self, page):
        if self.session is not None:
            self.page_label.setText(f"Page {page + 1} of {self.session.page_count}")

    def _show_blocked(self):
        hosts = list(offline.BLOCKED) + self._blocked
        if hosts:
            self.offline_label.setText(f"Offline · blocked {plural(len(hosts), 'network attempt')}")
            self.offline_label.setToolTip("Blocked: " + ", ".join(sorted(set(hosts))))
            set_state(self.offline_label, role="note")

    def _undo_tip(self, what):
        key = QKeySequence(QKeySequence.StandardKey.Undo).toString(QKeySequence.SequenceFormat.NativeText)
        self.statusBar().showMessage(f"{what} · {key} to undo", 4000)

    def _about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b><p>Black out what is on your list, check the result, and save it as a new "
                          "file. Everything happens on this computer: the network is blocked, and nothing is kept "
                          "after you close.</p><p>Uses PyMuPDF (AGPL-3.0) and Qt for Python (LGPL-3.0).</p>")

    # ---------------------------------------------------------------- dialogs (tests replace these)
    def _tell(self, title, text):
        QMessageBox.warning(self, title, text)

    def _ask(self, title, text, yes):
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        accept = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is accept

    def _ask_password(self, name, wrong):
        text = f"“{name}” is protected by a password. Enter it to open the file."
        if wrong:
            text = "That password is not right. " + text
        password, ok = QInputDialog.getText(self, "Password needed", text, QLineEdit.EchoMode.Password)
        return password if ok else None

    def _ask_save_path(self, suggested):
        dialog = QFileDialog(self, "Save the redacted PDF", suggested, "PDF files (*.pdf)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("pdf")          # added before the window asks about replacing a file
        try:
            return dialog.selectedFiles()[0] if dialog.exec() else None
        finally:
            dialog.deleteLater()

    def _pick(self, menu, where):
        return menu.exec(where)


def _dropped_pdf(mime):
    for url in mime.urls():
        if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
            return url.toLocalFile()
    return None


def _write_new_file(path, data):
    """Write `data` to `path` through a temporary file in the same folder, so that a failure never leaves a
    half-written file under that name. The file gets the usual permissions for a new file."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".pdf.saving", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name == "posix":
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(tmp, 0o666 & ~mask)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
