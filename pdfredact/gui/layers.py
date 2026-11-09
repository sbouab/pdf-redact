"""What the page viewer draws over the pages in each view, and what the mouse can do there."""
import pymupdf

from .viewer import Shape

DRAWN_LABEL = "drawn by you"


class ReviewLayer:
    """The Review view: red boxes for what goes, dashed outlines for the spots kept visible, the boxes drawn by
    hand, and a ring around the place the list shows. `current()` gives that place (a Match) or None."""

    def __init__(self, session, current, interactive=True):
        self._session, self._current, self.interactive = session, current, interactive

    def shapes(self, page):
        s = self._session
        out = [Shape(pymupdf.Rect(b.rect), "kept" if b in s.kept else "remove") for b in s.boxes_on(page)]
        out += [Shape(pymupdf.Rect(b.rect), "drawn", DRAWN_LABEL) for b in s.drawn_on(page)]
        match = self._current()
        if match is not None and match.page == page:
            out += [Shape(r, "ring") for r in match.rects]
        return out

    def hit(self, page, point):
        return self._session.box_at(page, point.x, point.y)

    def selection(self, page, rect):
        return self._session.text_in(page, rect)[1]

    def notice(self, page):
        """Why this page needs a look (a scan, pictures, unreadable text), at the top of the page."""
        problems = [problem for p, problem in self._session.problems if p == page]
        return f"Check by eye: this page {'; it '.join(problems)}." if problems else ""

    def hint(self, page, box):
        if box.kind == "user":
            return "Drawn by you. Click to delete this box."
        if box in self._session.kept:
            return "Kept visible. Click to black it out again."
        return "Will be blacked out. Click to keep this spot visible."


class ResultLayer:
    """The Redacted view: only a dashed outline around the spots kept visible."""

    interactive = False

    def __init__(self, session):
        self._session = session

    def shapes(self, page):
        s = self._session
        return [Shape(pymupdf.Rect(b.rect), "result-kept") for b in s.boxes_on(page) if b in s.kept]

    def hit(self, page, point):
        return None

    def selection(self, page, rect):
        return []

    def hint(self, page, target):
        return ""

    def notice(self, page):
        return ""
