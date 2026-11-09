"""The open document and the edits made to it in the app: the list, the spots kept visible, the boxes drawn.

No Qt here: the window shows this state, and the redaction job (job.py) works from it. Every edit increases
`generation`, so a redacted result can be matched with the exact state it was made from.
"""
import hashlib
import os
from dataclasses import dataclass
from typing import NamedTuple

import pymupdf

from .. import TAG, UserError
from ..document import XFA_HELP, document_warnings, is_dynamic_xfa, open_pdf, read_stamp
from ..geometry import center_in, inside
from ..review import boxes_from, page_matches, scan_page
from ..text import Term, page_problems
from .job import JobRequest

REVIEW_COPY = "this is a review copy made by the command-line tool: open the original PDF instead"
TOUCH = 1.0      # points of slack around a box when it is clicked
SLACK = 0.1      # points: a match lies in a box within this (as review.boxes_from decides which boxes to keep)


@dataclass(frozen=True)
class Box:
    """A box on a page. `rect` is (x0, y0, x1, y1) in the PDF coordinates of the unrotated page. `kind` is "text",
    "image" (text lying on a picture) or "field" (a form field) for a box around an item of the list, "user" for
    one drawn by hand."""
    page: int
    rect: tuple
    kind: str

    @classmethod
    def around(cls, page, rect, kind):
        return cls(page, tuple(round(v, 2) for v in rect), kind)


class AddResult(NamedTuple):
    """What became of the items given: added, already on the list, or too short to search for."""
    added: list
    duplicates: list
    too_short: list


class Cancelled(Exception):
    """Raised by a progress callback to stop opening a document."""


def _key(text):
    """What makes two items the same item (case, accents and spacing don't count); None if too short to search."""
    pattern = Term(text).pattern
    return pattern.pattern if pattern else None


def _is_review_copy(doc):
    return read_stamp(doc) != (None, None) or any(a.info.get("title") == TAG for page in doc for a in page.annots())


class Session:
    """A PDF opened from its bytes (`data`, read once from `path`), with the list and the box edits.
    `progress(done, total)` is called after each page is read; raising Cancelled from it stops the opening."""

    def __init__(self, path, data, password=None, progress=None):
        self.path, self.name, self.password = str(path), os.path.basename(path), password
        self.source_hash = hashlib.sha256(data).hexdigest()
        self.doc = open_pdf(self.name, password, data=data)       # keeps `data` alive as long as it is open
        try:
            if is_dynamic_xfa(self.doc):
                raise UserError(XFA_HELP)
            if _is_review_copy(self.doc):
                raise UserError(REVIEW_COPY)
            self.page_count = len(self.doc)
            self._scans, self.problems = [], []          # problems: (page, why items there could go unfound)
            for pno, page in enumerate(self.doc):
                scan = scan_page(page)
                self._scans.append(scan)
                self.problems += [(pno, p) for p in page_problems(page, scan.text, scan.images)]
                if progress:
                    progress(pno + 1, self.page_count)
            self.warnings = document_warnings(self.doc)
        except BaseException:
            self.doc.close()
            raise
        self.items = []
        self.kept = set()              # boxes of the list kept visible
        self.drawn = []                # boxes drawn by hand
        self.drop_attachments = False
        self.generation = 0
        self._found = {}               # item -> its matches; each item is searched once
        self._boxes = {}               # page -> boxes around the matches, without those inside a bigger one

    def close(self):
        self.doc.close()

    # ------------------------------------------------------------------ the list
    def preview_items(self, texts, ignore=None):
        """What add_items would do with `texts`, without doing it. `ignore` is an item that doesn't count as a
        duplicate (the one being renamed)."""
        taken = {_key(item) for item in self.items if item != ignore}
        added, duplicates, too_short = [], [], []
        for raw in texts:
            text = " ".join(raw.split())
            if not text:
                continue
            key = _key(text)
            if key is None:
                too_short.append(text)
            elif key in taken:
                duplicates.append(text)
            else:
                taken.add(key)
                added.append(text)
        return AddResult(added, duplicates, too_short)

    def add_items(self, texts):
        result = self.preview_items(texts)
        if result.added:
            self.items += result.added
            self._changed(boxes=True)
        return result

    def remove_item(self, text):
        """Remove an item. Its spots kept visible are forgotten: added again, it is blacked out everywhere."""
        self.items.remove(text)
        self._changed(boxes=True)

    def rename_item(self, old, new):
        """Replace an item by another, checked like an addition (the result says if it was refused)."""
        result = self.preview_items([new], ignore=old)
        if result.added:
            self.items[self.items.index(old)] = result.added[0]
            self._changed(boxes=True)
        return result

    def snapshot(self):
        """The list and the spots kept visible, for restore() to bring back (undoing a change to the list)."""
        return tuple(self.items), frozenset(self.kept)

    def restore(self, snapshot):
        items, kept = snapshot
        self.items, self.kept = list(items), set(kept)
        self._changed(boxes=True)

    def found(self, item):
        return len(self._matches(item))

    def occurrences(self, item):
        """The item's matches in reading order."""
        return sorted(self._matches(item), key=lambda m: (m.page, m.rects[0].y0, m.rects[0].x0))

    def kept_count(self, item):
        """How many of the item's matches lie in a box kept visible."""
        return sum(1 for m in self._matches(item) if self.match_kept(m))

    def boxes_of(self, match):
        """The list's boxes around a match: one per line it spans, or the box of a longer item around it."""
        return [b for b in self._boxes.get(match.page, [])
                if any(inside(r, pymupdf.Rect(b.rect), SLACK) for r in match.rects)]

    def match_kept(self, match):
        return any(b in self.kept for b in self.boxes_of(match))

    def kept_matches(self):
        """The list's matches in a spot kept visible: the only places an item may still show once redacted."""
        return [m for item in self.items for m in self._matches(item) if self.match_kept(m)]

    def count_matches(self, text):
        """How many times `text` would be found if it were added."""
        return len(self._search(Term(text)))

    # ------------------------------------------------------------------ boxes
    def boxes_on(self, page):
        """The boxes around the list's matches on a page, kept or not."""
        return list(self._boxes.get(page, []))

    def drawn_on(self, page):
        return [b for b in self.drawn if b.page == page]

    def box_at(self, page, x, y):
        """The box under a point of a page: the last one drawn there, else the smallest box of the list."""
        point = pymupdf.Point(x, y)

        def under(b):
            return point in pymupdf.Rect(b.rect) + (-TOUCH, -TOUCH, TOUCH, TOUCH)
        for b in reversed(self.drawn_on(page)):
            if under(b):
                return b
        return min((b for b in self.boxes_on(page) if under(b)), key=lambda b: abs(pymupdf.Rect(b.rect)), default=None)

    def text_in(self, page, rect):
        """The text of the characters whose center is in `rect`, with one space between words and lines, and the
        boxes of those characters."""
        pt = self._scans[page].text
        picked = [i for i in range(len(pt.text)) if pt.visible(i) and center_in(pt.boxes[i], rect)]
        chars, last = [], None
        for i in picked:
            if last is not None and i != last + 1:       # a space or a line break was skipped
                chars.append(" ")
            chars.append(pt.text[i])
            last = i
        return " ".join("".join(chars).split()), [pt.boxes[i] for i in picked]

    def set_kept(self, box, kept):
        if kept:
            self.kept.add(box)
        else:
            self.kept.discard(box)
        self._changed()

    def add_drawn(self, box, index=None):
        """Add a drawn box, last (drawn on top) or at `index` in the drawing order."""
        self.drawn.insert(len(self.drawn) if index is None else index, box)
        self._changed()

    def remove_drawn(self, box):
        """Remove a drawn box; returns where it was in the drawing order."""
        index = self.drawn.index(box)
        del self.drawn[index]
        self._changed()
        return index

    def set_drop_attachments(self, flag):
        self.drop_attachments = flag
        self._changed()

    # ------------------------------------------------------------------ what gets redacted
    def job_boxes(self):
        """The boxes to black out, per page: [(rect, kind)], the list's boxes not kept, then the ones drawn."""
        out = {}
        for page, boxes in self._boxes.items():
            for b in boxes:
                if b not in self.kept:
                    out.setdefault(page, []).append((b.rect, b.kind))
        for b in self.drawn:
            out.setdefault(b.page, []).append((b.rect, b.kind))
        return out

    def removal_count(self):
        return sum(map(len, self.job_boxes().values()))

    def has_work(self):
        """True when there is something to redact or clean: an item on the list or a box drawn."""
        return bool(self.items or self.drawn)

    def job_request(self, out_path):
        """What the redaction job needs to redact the current state into `out_path`."""
        return JobRequest(self.path, self.source_hash, self.password, tuple(self.items), self.job_boxes(),
                          tuple(self.kept_matches()), self.drop_attachments, str(out_path))

    # ------------------------------------------------------------------ internals
    def _search(self, term):
        return [m for pno, scan in enumerate(self._scans) for m in page_matches(pno, scan, [term])]

    def _matches(self, item):
        if item not in self._found:
            self._found[item] = self._search(Term(item))
        return self._found[item]

    def _changed(self, boxes=False):
        if boxes:
            every = [m for item in self.items for m in self._matches(item)]
            self._boxes = {pno: [Box.around(pno, r, kind or "text") for r, kind, _ in found]
                           for pno, found in boxes_from(every).items()}
            self.kept &= {b for page_boxes in self._boxes.values() for b in page_boxes}   # those of items gone
        self.generation += 1
