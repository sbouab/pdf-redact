"""The Review sidebar: the list of things to remove (add, edit, remove, step through where each is found),
what to check by eye, and the button that shows the redacted result."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from .style import label, set_state
from .wording import ALWAYS_REMOVED, plural, problem_text, quoted, warning_text

NOT_FOUND_HELP = "Check the spelling. If it is in a picture or a scan, drag a box over it."
EMPTY = "Nothing on the list yet. Type a name, number or address above, or drag over text on a page."


def _which(texts):
    return f"{quoted(texts[0])} is" if len(texts) == 1 else f"{len(texts)} of them are"


class ItemInput(QLineEdit):
    """The add field: Enter adds what was typed; pasting several lines adds each line."""

    entered = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("ItemInput")
        self.setPlaceholderText("Type, then press Enter")
        self.setAccessibleName("Add to the list")
        self.returnPressed.connect(self._submit)

    def _submit(self):
        if self.text().strip():
            self.entered.emit([self.text().strip()])

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste) and self._paste_lines():
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        for action in menu.actions():
            if action.objectName() == "edit-paste":
                action.triggered.disconnect()
                action.triggered.connect(lambda: self._paste_lines() or self.paste())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _paste_lines(self):
        """Add every line of a text of several lines from the clipboard. False for a single line."""
        lines = [line.strip() for line in QGuiApplication.clipboard().text().splitlines()]
        lines = [line for line in lines if line]
        if len(lines) < 2:
            return False
        self.entered.emit(lines)
        return True


class ItemRow(QFrame):
    """One item: its text, how often it was found, a remove button. When it is the selected item, where it is
    found ("2 of 3 · page 1") with buttons to step and to keep that spot; when it isn't found, what to do."""

    def __init__(self, panel, text):
        super().__init__()
        self._panel, self.text = panel, text
        self.setProperty("role", "row")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 4, 5)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.label = label(text, wrap=True)
        self.label.setToolTip("Double-click to edit")
        self.editor = QLineEdit(text)
        self.editor.setAccessibleName("Edit the item")
        self.editor.hide()
        self.editor.editingFinished.connect(lambda: self._finish_edit(True))       # e.g. clicking elsewhere
        self.editor.installEventFilter(self)
        self.badge = label(role="badge")
        self.remove = QToolButton()
        self.remove.setText("×")
        self.remove.setProperty("role", "icon")
        self.remove.setToolTip("Remove from the list")
        self.remove.setAccessibleName(f"Remove {text}")
        self.remove.clicked.connect(lambda: panel.removeRequested.emit(self.text))
        for w, stretch in ((self.label, 1), (self.editor, 1), (self.badge, 0), (self.remove, 0)):
            top.addWidget(w, stretch)
        layout.addLayout(top)

        self.nav = QWidget()
        nav = QHBoxLayout(self.nav)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(2)
        self.previous, self.next = QToolButton(), QToolButton()
        for button, sign, name, step in ((self.previous, "‹", "Previous", -1), (self.next, "›", "Next", 1)):
            button.setText(sign)
            button.setProperty("role", "icon")
            button.setAccessibleName(name)
            button.setToolTip(f"{name} place where it is found")
            button.clicked.connect(lambda _=False, s=step: panel.stepRequested.emit(s))
        self.position = label(role="nav")
        self.keep = QPushButton()
        self.keep.setProperty("role", "link")
        self.keep.clicked.connect(panel.keepToggled.emit)
        for w in (self.previous, self.position, self.next):
            nav.addWidget(w)
        nav.addStretch(1)
        nav.addWidget(self.keep)
        self.nav.hide()
        layout.addWidget(self.nav)

        self.help = label(NOT_FOUND_HELP, role="note", wrap=True)
        self.help.hide()
        layout.addWidget(self.help)

    def show_state(self, found, kept, current, occurrence=0, page=0, occurrence_kept=False, helped=False):
        """Show the counts; `helped` shows what to do about an item not found (one row at a time)."""
        missing = found == 0
        self.badge.setText("not found" if missing else f"{found} · {kept} kept" if kept else str(found))
        set_state(self.badge, state="missing" if missing else "")
        set_state(self, state="current" if current else "missing" if missing else "")
        self.help.setHidden(not (missing and helped))
        self.nav.setHidden(not current or missing)
        if current and not missing:
            self.position.setText(f"{occurrence + 1} of {found} · page {page + 1}")
            self.keep.setText("Black out here" if occurrence_kept else "Keep visible here")
            self.previous.setEnabled(found > 1)
            self.next.setEnabled(found > 1)

    # ------------------------------------------------------------------ editing in place
    def start_edit(self):
        self.label.hide()
        self.badge.hide()
        self.editor.setText(self.text)
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()

    def _finish_edit(self, accept):
        if self.editor.isHidden():
            return
        new = self.editor.text().strip()
        self.editor.hide()
        self.label.show()
        self.badge.show()
        if accept and new and new != self.text:
            self._panel.renameRequested.emit(self.text, new)

    def eventFilter(self, watched, event):
        # Enter and Escape end the edit here: let through, Enter would reach the row and start editing again
        if watched is self.editor and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                self._finish_edit(event.key() != Qt.Key.Key_Escape)
                self.setFocus()
                return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ mouse and keyboard
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._panel.itemSelected.emit(self.text)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.start_edit()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._panel.move_selection(self.text, -1 if key == Qt.Key.Key_Up else 1)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._panel.removeRequested.emit(self.text)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self.start_edit()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self._panel.itemSelected.emit(self.text)


class EyeEntry(QFrame):
    """Something to check by eye, with a link to its page when it is about one."""

    def __init__(self, text, tone, link=None):
        super().__init__()
        self.setProperty("role", "note")
        self.setProperty("tone", tone)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self.text = label(text, wrap=True)
        layout.addWidget(self.text)
        self.link = None
        if link:
            self.link = QPushButton(link)
            self.link.setProperty("role", "link")
            layout.addWidget(self.link, 0, Qt.AlignmentFlag.AlignLeft)

    def show_text(self, text, tone):
        if text != self.text.text():
            self.text.setText(text)
            set_state(self, tone=tone)


class ReviewPanel(QWidget):
    """The sidebar of the Review view. It shows a Session (update_from) and says what the user asks for."""

    itemsEntered = Signal(list)          # texts typed or pasted, to add
    removeRequested = Signal(str)
    renameRequested = Signal(str, str)
    itemSelected = Signal(object)        # the item clicked or reached with the keyboard
    stepRequested = Signal(int)          # -1 / +1: previous / next place the selected item is found
    keepToggled = Signal()               # keep, or black out again, the place shown
    pageRequested = Signal(int)          # 0-based page to show
    redactRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 18, 16, 16)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(label("To remove", role="heading"))
        self.counts = label(role="caption")
        head.addWidget(self.counts, 1, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(head)
        outer.addSpacing(4)
        prompt = label("Add a name, number, address…", role="caption")
        self.input = ItemInput()
        self.input.entered.connect(self.itemsEntered.emit)
        prompt.setBuddy(self.input)
        outer.addWidget(prompt)
        outer.addWidget(self.input)
        outer.addWidget(label("Paste several lines to add them all. Case, accents and spacing don't matter.",
                               role="caption", wrap=True))
        self.note = label(role="note", wrap=True)
        self.note.hide()
        outer.addWidget(self.note)

        body = QWidget()
        body.setObjectName("Sidebar")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 4, 0, 4)
        self._body.setSpacing(2)
        self.empty = label(EMPTY, role="muted", wrap=True)
        self._body.addWidget(self.empty)
        self._eye = QWidget()
        eye = QVBoxLayout(self._eye)
        eye.setContentsMargins(0, 14, 0, 0)
        eye.setSpacing(8)
        eye.addWidget(label("Check by eye", role="subheading"))
        self._eye_list = QVBoxLayout()
        self._eye_list.setSpacing(8)
        eye.addLayout(self._eye_list)
        self._body.addWidget(self._eye)
        removed = label(ALWAYS_REMOVED, role="caption", wrap=True)
        removed.setContentsMargins(0, 14, 0, 0)
        self._body.addWidget(removed)
        self._body.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.redact = QPushButton("Redact and check")
        self.redact.setProperty("role", "primary")
        self.redact.clicked.connect(self.redactRequested.emit)
        outer.addWidget(self.redact)
        caption = label("Opens the Redacted view. Nothing is saved yet.", role="caption", wrap=True)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(caption)
        self._rows = {}                  # item -> its row
        self._order = []                 # the items, in list order
        self._entries = []               # the "check by eye" entries of the session shown
        self._problem_entries = []       # (page, problem, entry) among them
        self._eye_session = None

    # ------------------------------------------------------------------ showing the session
    def update_from(self, session, current, occurrence):
        """Show the session's list, with `current` the selected item and `occurrence` the place shown."""
        items = session.items
        self._order = list(items)
        for text in [t for t in self._rows if t not in items]:
            row = self._rows.pop(text)
            self._body.removeWidget(row)
            row.hide()                 # at once: Qt deletes it later, and it must not show until then
            row.deleteLater()
        for text in items:
            if text in self._rows:
                self._body.removeWidget(self._rows[text])
            else:
                self._rows[text] = ItemRow(self, text)
        for i, text in enumerate(items):                       # rows in list order, before the empty note
            self._body.insertWidget(i, self._rows[text])
        missing = [text for text in items if not session.found(text)]
        helped = current if current in missing else missing[0] if missing else None   # the same help once is enough
        for text in items:
            found = session.found(text)
            if text == current and found:
                place = min(occurrence, found - 1)
                match = session.occurrences(text)[place]
                self._rows[text].show_state(found, session.kept_count(text), True, place, match.page,
                                            session.match_kept(match))
            else:
                self._rows[text].show_state(found, session.kept_count(text), text == current, helped=text == helped)
        self.empty.setHidden(bool(items))
        self.counts.setText(f"{plural(len(items), 'item')} · {plural(session.removal_count(), 'box', 'boxes')}")
        self.redact.setEnabled(session.has_work())
        self._update_eye(session)

    def _update_eye(self, session):
        if session is not self._eye_session:          # problems and warnings are fixed for a document
            self._make_eye_entries(session)
        boxed = {b.page for b in session.drawn}
        for pno, problem, entry in self._problem_entries:
            entry.show_text(problem_text(pno + 1, problem) + (" · boxed by you" if pno in boxed else ""),
                            "gray" if pno in boxed else "amber")
        self._eye.setHidden(not self._entries)

    def _make_eye_entries(self, session):
        for entry in self._entries:
            self._eye_list.removeWidget(entry)
            entry.hide()                 # at once: Qt deletes it later, and it must not show until then
            entry.deleteLater()
        self._eye_session, self._problem_entries = session, []
        for pno, problem in session.problems:
            entry = EyeEntry(problem_text(pno + 1, problem), "amber", f"Go to page {pno + 1}")
            entry.link.clicked.connect(lambda _=False, p=pno: self.pageRequested.emit(p))
            self._problem_entries.append((pno, problem, entry))
        self._entries = [entry for _, _, entry in self._problem_entries]
        self._entries += [EyeEntry(warning_text(w), "amber") for w in session.warnings]
        for entry in self._entries:
            self._eye_list.addWidget(entry)

    def show_add_result(self, result, typed=False):
        """Say what became of an addition. When it was `typed` in the add field, empty the field once dealt
        with; an addition from elsewhere (the page, a fix) leaves what is being typed there alone."""
        notes = []
        if result.too_short:
            notes.append(f"{_which(result.too_short)} too short to search for: drag a box over it on the page instead.")
        if result.duplicates:
            notes.append(f"{_which(result.duplicates)} already on the list.")
        self.note.setText(" ".join(notes))
        self.note.setHidden(not notes)
        if typed and not result.too_short:
            self.input.clear()

    # ------------------------------------------------------------------ for the window and the rows
    def row(self, text):
        return self._rows[text]

    def eye_entries(self):
        return list(self._entries)

    def focus_input(self):
        self.input.setFocus()
        self.input.selectAll()

    def focus_item(self, text):
        if text in self._rows:
            self._rows[text].setFocus()

    def move_selection(self, text, step):
        """Select the item before or after `text` in the list (arrow keys on a row)."""
        i = self._order.index(text) + step
        if 0 <= i < len(self._order):
            self._rows[self._order[i]].setFocus()
            self.itemSelected.emit(self._order[i])
