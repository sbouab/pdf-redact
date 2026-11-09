"""The edits made in the window, as commands that Edit > Undo can take back. Each one only changes the session:
the window follows the undo stack (its indexChanged signal) to show the new state, once per edit, undo or redo,
even for a group of commands. Additions and renames are checked (Session.preview_items) before a command is made.
"""
from PySide6.QtGui import QUndoCommand

from .wording import quoted


class _ListEdit(QUndoCommand):
    """A change to the list. Undo brings back the list and the spots kept visible as they were before, since
    removing an item also forgets its kept spots."""

    def __init__(self, session, label):
        super().__init__(label)
        self._session, self._before = session, None

    def redo(self):
        self._before = self._session.snapshot()
        self._change()

    def undo(self):
        self._session.restore(self._before)

    def _change(self):
        raise NotImplementedError


class AddItems(_ListEdit):
    def __init__(self, session, texts):
        super().__init__(session, f"Add {quoted(texts[0])}" if len(texts) == 1 else f"Add {len(texts)} Items")
        self._texts = list(texts)

    def _change(self):
        self._session.add_items(self._texts)


class RemoveItem(_ListEdit):
    def __init__(self, session, text):
        super().__init__(session, f"Remove {quoted(text)}")
        self._text = text

    def _change(self):
        self._session.remove_item(self._text)


class RenameItem(_ListEdit):
    def __init__(self, session, old, new):
        super().__init__(session, f"Edit {quoted(old)}")
        self._old, self._new = old, new

    def _change(self):
        self._session.rename_item(self._old, self._new)


class SetKept(QUndoCommand):
    def __init__(self, session, box, kept):
        super().__init__("Keep Spot Visible" if kept else "Black Out Spot Again")
        self._session, self._box, self._kept = session, box, kept

    def redo(self):
        self._session.set_kept(self._box, self._kept)

    def undo(self):
        self._session.set_kept(self._box, not self._kept)


class AddBox(QUndoCommand):
    def __init__(self, session, box):
        super().__init__("Draw Box")
        self._session, self._box = session, box

    def redo(self):
        self._session.add_drawn(self._box)

    def undo(self):
        self._session.remove_drawn(self._box)


class RemoveBox(QUndoCommand):
    def __init__(self, session, box):
        super().__init__("Delete Box")
        self._session, self._box, self._index = session, box, None

    def redo(self):
        self._index = self._session.remove_drawn(self._box)

    def undo(self):
        self._session.add_drawn(self._box, self._index)


class DropAttachments(QUndoCommand):
    def __init__(self, session):
        super().__init__("Remove Attached Files")
        self._session = session

    def redo(self):
        self._session.set_drop_attachments(True)

    def undo(self):
        self._session.set_drop_attachments(False)
