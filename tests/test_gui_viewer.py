"""The page viewer: drawing pages at the right place (rotated pages too), clicks, drags and zoom.
Run from the repository folder:  uv run pytest"""
import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from helpers import sample, wait_until
from pdfredact.gui.viewer import PageView, Shape


class StubLayer:
    """One clickable target over a given rectangle of page 1."""
    interactive = True

    def __init__(self, rect):
        self.rect = rect

    def shapes(self, page):
        return [Shape(self.rect, "remove")] if page == 0 else []

    def hit(self, page, point):
        return "target" if page == 0 and point in self.rect else None

    def selection(self, page, rect):
        return []

    def hint(self, page, target):
        return "a hint"

    def notice(self, page):
        return "Check by eye: this page is a scan" if page == 0 else ""


@pytest.fixture
def rotated(tmp_path):
    doc = pymupdf.open(sample(tmp_path, "rotated_page.pdf"))
    yield doc
    doc.close()


def shown(qapp, doc, layer=None):
    view = PageView()
    view.resize(900, 700)
    view.set_document(doc, layer)
    view.show()
    wait_until(lambda: view.page_widget(0).isVisible())
    return view


def dark_share(image, rect):
    rect = rect.toAlignedRect().intersected(image.rect())
    dark = sum(1 for x in range(rect.left(), rect.right()) for y in range(rect.top(), rect.bottom())
               if image.pixelColor(x, y).lightness() < 128)
    return dark / max(1, rect.width() * rect.height())


def test_mapping_round_trips_on_a_rotated_page(qapp, rotated):
    view = shown(qapp, rotated)
    widget = view.page_widget(0)
    assert widget.width() > widget.height()                       # shown sideways, as the page says
    sin = rotated[0].search_for("046 454 286")[0]                 # PDF coordinates of the unrotated page
    on_screen = widget.to_widget(sin)
    back = widget.to_pdf(on_screen)
    assert all(abs(a - b) < 0.5 for a, b in zip(back, sin))
    image = widget.grab().toImage()
    assert dark_share(image, on_screen) > 0.05                    # the digits are drawn there
    assert dark_share(image, on_screen.translated(0, on_screen.height() * 4)) < 0.01
    view.close()


def test_click_emits_the_hit_box(qapp, tmp_path):
    doc = pymupdf.open(sample(tmp_path, "letter_fr.pdf"))
    target = doc[0].search_for("Côté-Tremblay")[0]
    view = shown(qapp, doc, StubLayer(target))
    clicked = []
    view.boxClicked.connect(clicked.append)
    widget = view.page_widget(0)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.to_widget(target).center().toPoint())
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert clicked == ["target"]
    view.close()
    doc.close()


def test_drag_emits_pdf_rect(qapp, tmp_path):
    doc = pymupdf.open(sample(tmp_path, "letter_fr.pdf"))
    view = shown(qapp, doc, StubLayer(pymupdf.Rect()))
    dragged = []
    view.areaDragged.connect(lambda page, rect, where: dragged.append((page, rect)))
    widget = view.page_widget(0)
    wanted = pymupdf.Rect(100, 200, 300, 260)
    area = widget.to_widget(wanted)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=area.topLeft().toPoint())
    QTest.mouseMove(widget, (area.topLeft() + QPointF(20, 20)).toPoint())
    QTest.mouseMove(widget, area.bottomRight().toPoint())
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=area.bottomRight().toPoint())
    [(page, rect)] = dragged
    assert page == 0 and all(abs(a - b) < 2 for a, b in zip(rect, wanted))
    view.close()
    doc.close()


def test_zoom_and_fit_width(qapp, tmp_path):
    doc = pymupdf.open(sample(tmp_path, "letter_fr.pdf"))
    view = shown(qapp, doc)
    fit = view.page_widget(0).width()
    assert view.viewport().width() - 80 < fit <= view.viewport().width()
    view.zoom_in()
    assert abs(view.page_widget(0).width() - fit * 1.25) <= 2
    view.fit_width()
    assert abs(view.page_widget(0).width() - fit) <= 1
    view.close()
    doc.close()


def test_a_page_notice_is_drawn_on_the_page(qapp, tmp_path):
    doc = pymupdf.open(sample(tmp_path, "scan_no_text.pdf"))
    view = shown(qapp, doc, StubLayer(pymupdf.Rect()))
    image = view.page_widget(0).grab().toImage()
    amber = [image.pixelColor(x, y) for x in range(4, 300) for y in range(4, 60)]
    assert any(c.red() > 240 and 200 < c.green() < 250 and c.blue() < 235 for c in amber)
    view.close()
    doc.close()


def test_pages_of_the_previous_document_disappear_at_once(qapp, tmp_path):
    """Replaced pages are deleted later by Qt; until then they must not show (no stale page for a frame)."""
    letter = pymupdf.open(sample(tmp_path, "letter_fr.pdf"))
    scan = pymupdf.open(sample(tmp_path, "scan_no_text.pdf"))
    view = shown(qapp, letter)
    old = [view.page_widget(i) for i in range(view.page_count())]
    assert all(w.isVisibleTo(view) for w in old)
    view.set_document(scan, None)
    assert not any(w.isVisibleTo(view) for w in old)
    view.close()
    letter.close()
    scan.close()
