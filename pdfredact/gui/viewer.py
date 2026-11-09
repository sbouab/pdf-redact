"""The pages of a PDF in a scrolling column, what a layer draws over them (the boxes), and the mouse on them:
a click on a box, or a dragged rectangle.

Positions given to and by this module are PDF coordinates of the unrotated page, as PyMuPDF's text and
annotations use them; pages are shown turned as their rotation says.
"""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

import pymupdf
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QScrollArea, QStyle, QVBoxLayout, QWidget

from . import style

MARGIN = 16                    # pixels around the column of pages
GAP = 16                       # pixels between pages
DRAG = 4                       # pixels a press must move to become a drag
CACHE = 12                     # rendered pages kept in memory
ZOOM_STEP = 1.25
MIN_SCALE, MAX_SCALE = 0.25, 6.0
PX_PER_POINT = 96 / 72         # at 100%, a page shows at its printed size
NOTICE_MARGIN = 12             # pixels between a page's edge and its notice


@dataclass(frozen=True)
class Shape:
    """Something drawn over a page: where, how ("remove", "drawn", "kept", "ring", "select", "result-kept"),
    and an optional small label above it."""
    rect: pymupdf.Rect
    style: str
    label: str = ""


class Layer(Protocol):
    """What is drawn over the pages and what the mouse can do there."""
    interactive: bool                                            # clicks and drags are handled

    def shapes(self, page: int) -> list: ...
    def hit(self, page: int, point: pymupdf.Point) -> object: ...   # what a click there would act on, or None
    def selection(self, page: int, rect: pymupdf.Rect) -> list: ...  # what a drag over rect would pick (rects)
    def hint(self, page: int, target: object) -> str: ...            # tooltip over a target
    def notice(self, page: int) -> str: ...                          # a note shown at the top of a page, or ""


@dataclass(frozen=True)
class _Geometry:
    width: float               # the page as shown (turned), in points
    height: float
    shown: pymupdf.Matrix      # unrotated page -> page as shown
    unshown: pymupdf.Matrix    # page as shown -> unrotated page


class PageView(QScrollArea):
    """The column of pages. Emits currentPageChanged(page) when another page reaches the middle of the view,
    zoomChanged(percent), boxClicked(target) and areaDragged(page, rect, global position)."""

    currentPageChanged = Signal(int)
    zoomChanged = Signal(int)
    boxClicked = Signal(object)
    areaDragged = Signal(int, object, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setObjectName("Desk")
        column = QWidget()
        column.setObjectName("Desk")
        self._layout = QVBoxLayout(column)
        self._layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self._layout.setSpacing(GAP)
        self._layout.addStretch(1)
        self.setWidget(column)
        self.doc = None
        self.layer = None
        self._pages, self._geometry = [], []
        self._scale, self._fit = 1.0, True
        self._cache = OrderedDict()
        self._generation = 0               # tells the pages of one document from those of the next
        self._current = 0
        self._pinch = 1.0
        self.verticalScrollBar().valueChanged.connect(self._track_page)

    # ------------------------------------------------------------------ document and layer
    def set_document(self, doc, layer: Layer | None = None, keep_place=False):
        """Show `doc` (None for nothing) with `layer` drawn over it. With keep_place, stay on the same page."""
        place = self._place() if keep_place else (0, 0.0)
        for w in self._pages:
            self._layout.removeWidget(w)
            w.hide()                   # at once: Qt deletes it later, and it must not show until then
            w.deleteLater()
        self._pages, self._geometry = [], []
        self._cache.clear()
        self._generation += 1
        self.doc, self.layer = doc, layer
        if doc is not None:
            for i, page in enumerate(doc):
                self._geometry.append(_Geometry(page.rect.width, page.rect.height, page.rotation_matrix,
                                                page.derotation_matrix))
                w = PageWidget(self, i)
                self._pages.append(w)
                self._layout.insertWidget(i, w, 0, Qt.AlignmentFlag.AlignHCenter)
        self._current = -1
        self._apply_scale(place if self._pages else None)

    def set_layer(self, layer: Layer | None):
        self.layer = layer
        self.refresh()

    def refresh(self):
        """Draw the layer again (after the boxes changed)."""
        for w in self._pages:
            w.update()

    def page_widget(self, index):
        return self._pages[index]

    def page_count(self):
        return len(self._pages)

    def current_page(self):
        return max(self._current, 0)

    # ------------------------------------------------------------------ moving around
    def scroll_to(self, page, rect=None):
        """Bring a page into view: its top, or the area `rect` (PDF coordinates) in the middle of the view."""
        if not 0 <= page < len(self._pages):
            return
        w = self._pages[page]
        if rect is None:
            self.verticalScrollBar().setValue(w.y() - GAP // 2)
            return
        area = w.to_widget(rect).translated(QPointF(w.pos()))
        middle = area.center()
        self.ensureVisible(round(middle.x()), round(middle.y()), round(area.width() / 2) + 40,
                           max(40, self.viewport().height() // 3))

    def zoom_in(self):
        self._zoom(self._scale * ZOOM_STEP)

    def zoom_out(self):
        self._zoom(self._scale / ZOOM_STEP)

    def fit_width(self):
        self._fit = True
        self._apply_scale(self._place())

    def zoom_percent(self):
        return round(self._scale / PX_PER_POINT * 100)

    def _zoom(self, scale):
        self._fit = False
        self._scale = min(MAX_SCALE, max(MIN_SCALE, scale))
        self._apply_scale(self._place())

    def _apply_scale(self, place):
        if self._fit and self._geometry:
            widest = max(g.width for g in self._geometry)
            self._scale = min(MAX_SCALE, max(MIN_SCALE, self._available_width() / widest))
        for w, g in zip(self._pages, self._geometry):
            w.setFixedSize(round(g.width * self._scale), round(g.height * self._scale))
        self._cache.clear()
        self.zoomChanged.emit(self.zoom_percent())
        if place is not None:
            QTimer.singleShot(0, lambda: self._go_to_place(place))

    def _available_width(self):
        transient = self.style().styleHint(QStyle.StyleHint.SH_ScrollBar_Transient, None, self)
        bar = 0 if transient else self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, self)
        return max(100, self.width() - 2 * self.frameWidth() - bar - 2 * MARGIN)

    def _place(self):
        """(page, share of its height) at the top of the view, to come back to after a change of size."""
        top = self.verticalScrollBar().value()
        for i, w in enumerate(self._pages):
            if w.y() + w.height() + GAP > top:
                return i, max(0.0, (top - w.y()) / max(1, w.height()))
        return 0, 0.0

    def _go_to_place(self, place):
        page, share = place
        if page < len(self._pages):
            w = self._pages[page]
            self.verticalScrollBar().setValue(round(w.y() + share * w.height()))
        self._track_page()

    def _track_page(self, *_):
        middle = self.verticalScrollBar().value() + self.viewport().height() / 2
        page = next((i for i, w in enumerate(self._pages) if w.y() + w.height() + GAP / 2 >= middle),
                    len(self._pages) - 1)
        if page != self._current and self._pages:
            self._current = page
            self.currentPageChanged.emit(page)

    # ------------------------------------------------------------------ events
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit and self._pages:
            self._apply_scale(self._place())
        else:
            self._track_page()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:      # Ctrl (Cmd on a Mac) + wheel: zoom
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self._pinch *= 1 + event.value()                                # trackpad pinch
            if abs(self._pinch - 1) > 0.15:
                self._zoom(self._scale * self._pinch)
                self._pinch = 1.0
            return True
        return super().event(event)

    # ------------------------------------------------------------------ for the pages
    def page_geometry(self, index):
        """(the page's _Geometry, the current scale in pixels per point)."""
        return self._geometry[index], self._scale

    def pixmap(self, index):
        """The page rendered for the screen at the current zoom, or None if it can't be rendered."""
        ratio = self.devicePixelRatioF()
        key = (self._generation, index, self._scale, ratio)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        try:
            zoom = self._scale * ratio
            pix = self.doc[index].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(ratio)
        except Exception:
            pixmap = None
        self._cache[key] = pixmap
        while len(self._cache) > CACHE:
            self._cache.popitem(last=False)
        return pixmap


class PageWidget(QWidget):
    """One page: its picture, the layer's shapes, and the mouse on it."""

    def __init__(self, view, index):
        super().__init__()
        self._view, self.index = view, index
        self._press = None             # where the left button went down (widget coordinates)
        self._drag = None              # the rectangle being dragged
        self._picked = []              # what that rectangle would select
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ------------------------------------------------------------------ coordinates
    def to_widget(self, rect):
        """A rectangle of the unrotated page (PDF coordinates) on this widget, as a QRectF."""
        g, s = self._view.page_geometry(self.index)
        r = pymupdf.Rect(rect) * g.shown
        return QRectF(r.x0 * s, r.y0 * s, r.width * s, r.height * s)

    def to_pdf(self, rect):
        """A QRectF of this widget as a rectangle of the unrotated page."""
        g, s = self._view.page_geometry(self.index)
        return pymupdf.Rect(rect.left() / s, rect.top() / s, rect.right() / s, rect.bottom() / s) * g.unshown

    def _point(self, pos):
        g, s = self._view.page_geometry(self.index)
        return pymupdf.Point(pos.x() / s, pos.y() / s) * g.unshown

    def _layer(self):
        layer = self._view.layer
        return layer if layer is not None and layer.interactive else None

    # ------------------------------------------------------------------ drawing
    def paintEvent(self, event):
        p = QPainter(self)
        pixmap = self._view.pixmap(self.index)
        if pixmap is None:
            p.fillRect(self.rect(), style.UNREADABLE_PAGE)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "This page can't be shown")
        else:
            p.drawPixmap(0, 0, pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._view.layer is not None:
            for shape in self._view.layer.shapes(self.index):
                self._draw(p, shape)
            self._draw_notice(p, self._view.layer.notice(self.index))
        for r in self._picked:
            self._draw(p, Shape(r, "select"))
        if self._drag is not None:
            p.setPen(QPen(style.DRAG_EDGE, 1, Qt.PenStyle.DashLine))
            p.setBrush(style.DRAG_FILL)
            p.drawRect(self._drag)
        p.end()

    def _draw(self, p, shape):
        r = self.to_widget(shape.rect)
        if shape.style in ("remove", "drawn"):
            p.setPen(QPen(style.REMOVE_EDGE, 1))
            p.setBrush(style.REMOVE_FILL)
            p.drawRoundedRect(r, 2, 2)
        elif shape.style in ("kept", "result-kept"):
            edge = style.KEPT_EDGE if shape.style == "kept" else style.RESULT_KEPT_EDGE
            p.setPen(QPen(edge, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 2, 2)
        elif shape.style == "ring":
            p.setPen(QPen(style.RING, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 3, 3)
        elif shape.style == "select":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(style.SELECT_FILL)
            p.drawRect(r)
        if shape.label:
            font = p.font()
            font.setPixelSize(11)
            p.setFont(font)
            p.setPen(style.LABEL_TEXT)
            width = p.fontMetrics().horizontalAdvance(shape.label)
            p.drawText(QPointF(max(0.0, r.right() - width), max(11.0, r.top() - 4)), shape.label)

    def _draw_notice(self, p, text):
        """A note in an amber chip at the top of the page."""
        if not text:
            return
        font = p.font()
        font.setPixelSize(12)
        p.setFont(font)
        room = QRectF(NOTICE_MARGIN + 8, NOTICE_MARGIN + 5, max(40, self.width() - 2 * NOTICE_MARGIN - 16), 10_000)
        flags = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        used = p.fontMetrics().boundingRect(room.toRect(), int(flags), text)
        chip = QRectF(NOTICE_MARGIN, NOTICE_MARGIN, used.width() + 16, used.height() + 10)
        p.setPen(QPen(style.NOTICE_EDGE, 1))
        p.setBrush(style.NOTICE_FILL)
        p.drawRoundedRect(chip, 6, 6)
        p.setPen(style.NOTICE_TEXT)
        p.drawText(chip.adjusted(8, 5, -8, -5), int(flags), text)

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._layer():
            self._press, self._drag, self._picked = event.position(), None, []
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        layer = self._layer()
        if layer is None:
            return
        pos = event.position()
        if self._press is None:                                   # hovering: show what a click would do
            target = layer.hit(self.index, self._point(pos))
            self.setCursor(Qt.CursorShape.PointingHandCursor if target is not None else Qt.CursorShape.CrossCursor)
            self.setToolTip(layer.hint(self.index, target) if target is not None else "")
            return
        if self._drag is None and (pos - self._press).manhattanLength() < DRAG:
            return
        self._drag = QRectF(self._press, pos).normalized().intersected(QRectF(self.rect()))
        self._picked = layer.selection(self.index, self.to_pdf(self._drag))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        press, drag = self._press, self._drag
        self._press = self._drag = None
        self._picked = []
        self.update()
        layer = self._layer()
        if layer is None:
            return
        if drag is not None:
            self._view.areaDragged.emit(self.index, self.to_pdf(drag), event.globalPosition().toPoint())
            return
        target = layer.hit(self.index, self._point(press))
        if target is not None:
            self._view.boxClicked.emit(target)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._press is not None:       # cancel a drag
            self._press = self._drag = None
            self._picked = []
            self.update()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)
