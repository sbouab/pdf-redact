"""mark: find every list item and write the review copy. Also reads the boxes back after the review."""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pymupdf

from . import TAG, UserError
from .document import (XFA_HELP, document_warnings, hidden_texts, is_dynamic_xfa, open_pdf, set_review_metadata,
                       stamp, text_fields, widget_value)
from .geometry import inside, near
from .text import PageText, list_fingerprint, normalize, page_problems

MARGIN = (-1.5, -1.5, 1.5, 1.5)   # room around text lying on a picture: a scan's text layer rarely fits the ink exactly
COLOR = (0.9, 0.1, 0.1)
BANNER = "REVIEW COPY - NOT REDACTED - CONTAINS SENSITIVE DATA  /  COPIE DE RÉVISION - NON CAVIARDÉE"
BOX_TYPES = {pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_CIRCLE, pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE,
             pymupdf.PDF_ANNOT_INK, pymupdf.PDF_ANNOT_REDACT, pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE,
             pymupdf.PDF_ANNOT_SQUIGGLY, pymupdf.PDF_ANNOT_STRIKE_OUT}
MARKUP = {pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_SQUIGGLY, pymupdf.PDF_ANNOT_STRIKE_OUT}
_TAG_RX = re.compile(r"\[(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\](?: (image|field))?")


@dataclass
class Match:
    page: int            # 0-based
    term: int            # index of the list item
    rects: list          # one per text line, or the form field's rectangle
    kind: str = ""       # "" text, "image" text lying on a picture (a scan's text layer), "field" a form field value
    xref: int = 0        # for kind "field": the form field's object number
    field: str = ""      # and its name


def find_matches(doc, terms, problems=None):
    """Every occurrence of every list item, on the pages and in form fields. If `problems` is a list,
    reasons why items could go unfound on a page are added to it."""
    out = []
    for pno, page in enumerate(doc):
        pt = PageText(page)
        images = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
        if problems is not None:
            problems += [f"page {pno + 1} {p}" for p in page_problems(page, pt, images)]
        for ti, term in enumerate(terms):
            for start, end in term.find(pt.norm):
                rects = pt.rects(start, end)
                if rects:
                    on_image = any(r.intersects(im) for r in rects for im in images)
                    out.append(Match(pno, ti, rects, "image" if on_image else ""))
        for w in text_fields(page):
            value = normalize(widget_value(w))
            out += [Match(pno, ti, [pymupdf.Rect(w.rect)], "field", w.xref, w.field_name or "")
                    for ti, t in enumerate(terms) if t.find(value)]
    return out


def boxes_from(matches):
    """One box per text line of every match, per page; boxes inside a bigger box are dropped
    (an item that is also part of a longer item)."""
    per_page = defaultdict(list)
    for m in matches:
        per_page[m.page] += [(r, m.kind, m.term) for r in m.rects]
    out = {}
    for pno, items in per_page.items():
        keep = []
        for item in sorted(items, key=lambda it: -abs(it[0])):
            if not any(inside(item[0], k[0], 0.1) for k in keep):
                keep.append(item)
        out[pno] = keep
    return out


@dataclass
class MarkReport:
    found: Counter                 # term index -> occurrences
    boxes: int = 0
    pages: int = 0                 # pages with at least one box
    fields: int = 0                # form fields with a list item
    hidden: list = field(default_factory=list)     # places outside the pages that hold list items
    problems: list = field(default_factory=list)   # pages where items could go unfound
    warnings: list = field(default_factory=list)


def mark(src, terms, out_path, source_hash, password=None):
    """Write the review copy of `src`: the original with a box on every list item."""
    doc = open_pdf(src, password)
    try:
        if is_dynamic_xfa(doc):
            raise UserError(XFA_HELP)
        problems = []
        matches = find_matches(doc, terms, problems)
        report = MarkReport(found=Counter(m.term for m in matches), problems=problems,
                            warnings=document_warnings(doc))
        report.fields = len({(m.page, m.xref) for m in matches if m.kind == "field"})
        report.hidden = sorted({where for where, text in hidden_texts(doc)
                                if any(t.find(normalize(text)) for t in terms)})
        boxes = boxes_from(matches)
        report.boxes, report.pages = sum(map(len, boxes.values())), len(boxes)
        made_from = stamp(source_hash, list_fingerprint(terms))
        for page in doc:
            _add_banner(page, made_from)
        for pno, items in boxes.items():
            for rect, kind, ti in items:
                _add_box(doc[pno], rect, kind, f"on your list: {terms[ti].text}")
        notes = report.problems + report.warnings
        if notes:
            note = doc[0].add_text_annot((8, 8), "redact_pdf:\n- " + "\n- ".join(notes))
            note.set_info(title=TAG)
            note.update()
        set_review_metadata(doc, made_from)
        try:
            doc.save(out_path, garbage=1, deflate=True)
        except Exception as e:
            raise UserError(f"can't write the review copy ({e}). If it is open in a PDF viewer, close it and try again.")
        return report
    finally:
        doc.close()


def _add_banner(page, made_from):
    shown = page.rect      # as displayed (rotation applied); annotations use unrotated coordinates
    strip = pymupdf.Rect(shown.x0 + 4, shown.y0 + 2, shown.x1 - 4, shown.y0 + 13) * page.derotation_matrix
    b = page.add_freetext_annot(strip, BANNER, fontsize=7, text_color=(1, 1, 1), fill_color=(0.8, 0, 0), align=1,
                                rotate=page.rotation)
    b.set_info(title=TAG, subject=made_from)
    b.update(text_color=(1, 1, 1), fill_color=(0.8, 0, 0))


def _add_box(page, rect, kind, content):
    a = page.add_rect_annot(rect + MARGIN if kind == "image" else rect)
    a.set_colors(stroke=COLOR, fill=COLOR)
    a.set_border(width=0.5)
    a.set_opacity(0.3)
    tag = f"[{rect.x0:.2f} {rect.y0:.2f} {rect.x1:.2f} {rect.y1:.2f}]" + (f" {kind}" if kind else "")
    a.set_info(title=TAG, subject=tag, content=content)
    a.update()


def read_boxes(doc, rev):
    """The boxes in a reviewed copy, per page: [(rect, kind)]. kind is "text", "image" or "field" for a
    box left as mark made it (its exact position is used), "user" for one drawn, moved or resized."""
    boxes = defaultdict(list)
    for pno in range(len(doc)):
        original = [(a.type[0], pymupdf.Rect(a.rect), a.info.get("content", "")) for a in doc[pno].annots()
                    if a.type[0] != pymupdf.PDF_ANNOT_REDACT]
        for a in rev[pno].annots():
            t = a.type[0]
            if t not in BOX_TYPES:
                continue
            r = pymupdf.Rect(a.rect)
            if any(t == ot and near(r, orr, 1.5) and a.info.get("content", "") == oc for ot, orr, oc in original):
                continue                                     # a comment that was already in the original
            kind = "user"
            m = _TAG_RX.search(a.info.get("subject") or "")
            if m and a.info.get("title") == TAG:
                exact = pymupdf.Rect(*map(float, m.groups()[:4]))
                if near(exact, r, 3.0):
                    r, kind = exact, m.group(5) or "text"
            if t in MARKUP and a.vertices:
                v = a.vertices
                rects = [pymupdf.Quad(v[i:i + 4]).rect for i in range(0, len(v) - 3, 4)]
            else:
                rects = [r]
            boxes[pno] += [(x, kind) for x in rects]
    return boxes
