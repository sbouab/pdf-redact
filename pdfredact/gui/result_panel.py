"""The Redacted sidebar: the job's progress, then the result of the checks (passed, or what failed and the one
fix each failure needs), the warnings to look at, and Save, which stays off until every check passes."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .style import label, set_state
from .wording import finding_fixes, finding_text, plural, sentence, stage_text, summary

STAGES = ("redact", "check")      # as the job reports them
SAVE_OFF = "Off until every check passes."
FAILED_HELP = ("Every failure comes with the one fix it needs, or a page to go look at. When all checks pass, "
               "Save turns back on.")


def _label(text="", role=None):
    return label(text, role, wrap=True)


class ResultPanel(QWidget):
    """The sidebar of the Redacted view."""

    saveRequested = Signal()
    backRequested = Signal()
    retryRequested = Signal()
    fixRequested = Signal(object)        # a Fix chosen next to a finding

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 18, 16, 16)
        outer.setSpacing(10)
        body = QWidget()
        body.setObjectName("Sidebar")
        self._content = QVBoxLayout(body)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.save = QPushButton("Save redacted PDF…")
        self.save.setProperty("role", "primary")
        self.save.clicked.connect(self.saveRequested.emit)
        self.caption = _label(role="caption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        back = QPushButton("Back to Review")
        back.setProperty("role", "secondary")
        back.clicked.connect(self.backRequested.emit)
        for w in (self.save, self.caption, back):
            outer.addWidget(w)
        self.heading = None              # the heading of the state shown; each state makes its own
        self.progress = self.stage = None          # the progress bar and its line, while the job runs
        self._mode = None

    # ------------------------------------------------------------------ states
    def show_nothing(self):
        self._start("nothing")
        self._content.addWidget(self._card("busy", "Nothing to redact yet",
                                           ["Add what to remove in Review, or drag a box over the page."]))
        self._finish(False, "Add something to remove first.")

    def show_running(self, stage, done, total):
        if self._mode != "running":
            self._start("running")
            card = self._card("busy", "Redacting and checking…", [])
            self.progress, self.stage = QProgressBar(), _label()
            self.progress.setTextVisible(False)
            card.column.addWidget(self.progress)
            card.column.addWidget(self.stage)
            self._content.addWidget(card)
            self._content.addWidget(self._note("gray", ["Editing in Review stops this and starts it again."]))
            self._finish(False, "Save turns on when every check passes.")
        self.progress.setRange(0, len(STAGES) * max(total, 1))
        self.progress.setValue(STAGES.index(stage) * total + done if stage in STAGES else 0)
        self.stage.setText(stage_text(stage, done, total))

    def show_error(self, message):
        self._start("error")
        self._content.addWidget(self._card("fail", "Couldn't redact", [sentence(message)]))
        retry = QPushButton("Try again")
        retry.setProperty("role", "secondary")
        retry.clicked.connect(self.retryRequested.emit)
        self._content.addWidget(retry, 0, Qt.AlignmentFlag.AlignLeft)
        self._finish(False, SAVE_OFF)

    def show_result(self, result, session, can_save, suggested_name):
        """The outcome of the checks. Save is on when they passed and `can_save` (the result is current)."""
        self._start("result")
        passed = not result.fails
        if passed:
            lines = [summary(result) + (" Nothing under a black box can be copied out." if result.areas else "")]
            self._content.addWidget(self._card("pass", "Passed all checks", lines))
        else:
            lines = [f"{plural(len(result.fails), 'check')} failed. Nothing was saved."]
            self._content.addWidget(self._card("fail", "Not safe to share", lines))
            for f in result.fails:
                self._content.addWidget(self._finding(f, "failure"))
        if result.warns:
            self._content.addWidget(_label("Look at before sharing", role="subheading"))
            for f in result.warns:
                self._content.addWidget(self._finding(f, "amber"))
        if result.notes:
            self._content.addWidget(self._note("gray", [sentence(n) for n in result.notes]))
        made = f"Made from your {plural(len(session.items), 'item')}"
        if session.drawn:
            made += f" and {plural(len(session.drawn), 'box', 'boxes')} you drew"
        made += ". Change them in Review: this view redoes itself when you come back."
        self._content.addWidget(self._note("gray", [made if passed else FAILED_HELP]))
        enabled = passed and can_save
        self._finish(enabled, f"Suggested name: {suggested_name}" if enabled else SAVE_OFF)

    # ------------------------------------------------------------------ parts
    def _start(self, mode):
        """Empty the panel for another state."""
        self._mode = mode
        while self._content.count():
            w = self._content.takeAt(0).widget()
            if w is not None:
                w.hide()                 # at once: Qt deletes it later, and it must not show until then
                w.deleteLater()

    def _finish(self, can_save, caption):
        self._content.addStretch(1)
        self.save.setEnabled(can_save)
        self.caption.setText(caption)

    def _card(self, tone, title, lines):
        card = QFrame()
        card.setProperty("role", "card")
        card.setProperty("tone", tone)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(10)
        if tone in ("pass", "fail"):
            mark = QLabel("✓" if tone == "pass" else "✕")
            mark.setStyleSheet("font-size: 20px; font-weight: 700;")
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(4)
        self.heading = _label(title, role="heading")
        column.addWidget(self.heading)
        for line in lines:
            column.addWidget(_label(line))
        row.addLayout(column, 1)
        card.column = column                 # where more can go under the title
        set_state(card)
        return card

    def _note(self, tone, lines):
        note = QFrame()
        note.setProperty("role", "note")
        note.setProperty("tone", tone)
        column = QVBoxLayout(note)
        column.setContentsMargins(10, 8, 10, 8)
        column.setSpacing(2)
        for line in lines:
            column.addWidget(_label(line))
        return note

    def _finding(self, f, tone):
        note = self._note(tone, [finding_text(f)])
        fixes = QHBoxLayout()
        fixes.setSpacing(14)
        for fix in finding_fixes(f):
            button = QPushButton(fix.label)
            button.setProperty("role", "danger" if fix.kind == "drop-attachments" else "link")
            button.clicked.connect(lambda _=False, chosen=fix: self.fixRequested.emit(chosen))
            fixes.addWidget(button)
        fixes.addStretch(1)
        note.layout().addLayout(fixes)
        return note
