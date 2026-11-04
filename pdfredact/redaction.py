"""apply: redact the boxed areas in a fresh copy of the original, clean the text kept outside the pages,
save the result and check it."""
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import pymupdf

from . import UserError
from .document import (META_KEYS, XFA_HELP, has_xfa, is_dynamic_xfa, open_pdf, outline_xrefs, pdf_string,
                       read_stamp, text_fields, widget_value)
from .geometry import center_in, near, union
from .review import MARGIN, boxes_from, find_matches, read_boxes
from .text import Term, PageText, list_fingerprint, normalize
from .verify import check

PLACEHOLDER = "XXXXX"      # replaces removed text in form fields, document properties, bookmarks and comments


@dataclass
class Plan:
    """What was redacted, for the checks."""
    pages: list = field(default_factory=list)         # PageText of every original page
    taken: list = field(default_factory=list)         # per page: indexes of the characters removed
    field_rects: list = field(default_factory=list)   # per page: cleaned form fields (their area isn't compared)
    areas: list = field(default_factory=list)         # per page: every blacked-out area
    visual: list = field(default_factory=list)        # per page: areas blacked out on pictures and drawings
    before: list = field(default_factory=list)        # per page: counts of form fields, links and comments
    marked: dict = field(default_factory=lambda: defaultdict(list))   # where mark put boxes, per page
    marked_fields: set = field(default_factory=set)   # (page, field name) of the form fields mark boxed
    links_removed: int = 0


@dataclass
class Result:
    areas: int = 0
    pages: int = 0
    fields: int = 0
    deleted: int = 0     # boxes from mark that the reviewer removed (or moved)
    added: int = 0       # boxes the reviewer drew (or moved)
    notes: list = field(default_factory=list)
    fails: list = field(default_factory=list)
    warns: list = field(default_factory=list)


def apply(src, review_path, terms, source_hash, out_path, password=None, drop_attachments=False):
    """Redact `src` as boxed in `review_path`, save to `out_path`, and check the saved file."""
    doc = open_pdf(src, password)
    rev = None
    try:
        if is_dynamic_xfa(doc):
            raise UserError(XFA_HELP)
        rev = open_pdf(review_path, password)
        if len(rev) != len(doc) or any(not near(rev[i].rect, doc[i].rect, 0.5) for i in range(len(doc))):
            raise UserError("the review copy doesn't match the original (its pages differ): "
                            "run mark again with --overwrite")
        # Where mark put boxes is recomputed below, which is only right with the same original and the same list.
        made_from, list_used = read_stamp(rev)
        if made_from is not None and made_from != source_hash:
            raise UserError("the original changed after its review copy was made: run mark again with --overwrite")
        if made_from is not None and list_used != list_fingerprint(terms):
            raise UserError("the list is not the one mark used (an item was added, removed or changed). Use the same "
                            "list, and draw a box by hand over anything new; or run mark again with --overwrite "
                            "(the box edits in the review copy are then lost).")
        boxes = read_boxes(doc, rev)
        matches = find_matches(doc, terms)
        if matches and not any(boxes.values()):
            raise UserError("the review copy has no boxes left. If your PDF viewer removed or flattened them when "
                            "saving, run mark again with --overwrite and save your edits with another viewer. "
                            "If you deleted every box on purpose, there is nothing to redact.")

        result, plan = Result(), Plan()
        if made_from is None:     # a viewer rewrote the review copy: no leftover can be taken for a deliberate choice
            result.notes.append("the review copy no longer records how it was made (the viewer that saved it "
                                "rewrote it), so any list item left visible counts as an error")
        else:
            for m in matches:
                if m.kind == "field":
                    plan.marked_fields.add((m.page, m.field))
                else:
                    plan.marked[m.page] += m.rects
        kept = sum(1 for page_boxes in boxes.values() for _, kind in page_boxes if kind != "user")
        result.added = sum(len(page_boxes) for page_boxes in boxes.values()) - kept
        result.deleted = max(0, sum(map(len, boxes_from(matches).values())) - kept)

        removed = []                          # text taken out of the pages and form fields
        for pno, page in enumerate(doc):
            pt = PageText(page)
            plan.pages.append(pt)
            plan.before.append(dict(fields=len(list(page.widgets())), links=len(page.get_links()),
                                    comments=sum(1 for a in page.annots() if a.type[0] != pymupdf.PDF_ANNOT_REDACT)))
            # the original's own unapplied redaction marks: only the review copy decides what goes
            for x in [a.xref for a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT])]:
                page.delete_annot(page.load_annot(x))
            text_areas, visual, field_xrefs, taken, segments_out = plan_page(page, pt, boxes.get(pno, []))
            removed += segments_out
            frects, values = clean_fields(page, field_xrefs, terms)
            removed += values
            areas = text_areas + visual
            nearby = terms + [Term(s) for s in segments_out]
            for ln in page.get_links():
                uri = normalize(ln.get("uri") or "")
                if any(pymupdf.Rect(ln["from"]).intersects(a) for a in areas) or any(t.find(uri) for t in nearby):
                    page.delete_link(ln)
                    plan.links_removed += 1
            for r in text_areas:
                page.add_redact_annot(r, fill=(0, 0, 0))
            if text_areas:   # text goes; table lines crossing it stay
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                      text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            for r in visual:
                page.add_redact_annot(r, fill=(0, 0, 0))
            if visual:       # pixels and covered drawings go
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                      text=pymupdf.PDF_REDACT_TEXT_NONE)
            plan.taken.append(taken)
            plan.field_rects.append(frects)
            plan.areas.append(areas)
            plan.visual.append(visual)
            result.fields += len(frects)
            if areas:
                result.pages += 1
                result.areas += len(areas)

        extra = removed_terms(removed, terms)
        result.notes += clean_hidden(doc, terms + extra, drop_attachments)
        try:
            doc.save(out_path, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        except Exception as e:
            raise UserError(f"can't write in {os.path.dirname(out_path)}: {e}")
    finally:
        doc.close()
        if rev is not None:
            rev.close()
    result.fails, result.warns = check(out_path, plan, terms, extra, drop_attachments)
    return result


def plan_page(page, pt, boxes):
    """Turn a page's boxes into: text areas to remove (shrunk so they touch no other character), areas to
    black out on pictures and drawings, form fields to clean, and the text taken out."""
    fields = text_fields(page)
    images = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    drawings = None
    groups, visual, field_xrefs, taken, removed = [], [], set(), set(), []
    for r, kind in boxes:
        for w in fields:
            overlap = abs(r & w.rect)
            if overlap and overlap >= 0.25 * min(abs(r), abs(w.rect)):
                field_xrefs.add(w.xref)
        if kind == "field":
            continue
        idx = [i for i in range(len(pt.text)) if pt.visible(i) and center_in(pt.boxes[i], r)]
        removed += segments(pt, idx)
        groups += by_line(pt, idx)
        taken.update(idx)
        if kind == "image":                   # text layer of a scan: the visible words are in the picture
            visual.append(r + MARGIN)
        elif kind == "user":                  # drawn by the reviewer: also covers pictures, drawings, blank areas
            if drawings is None:
                drawings = [pymupdf.Rect(d["rect"]) for d in page.get_drawings()]
            if not idx or any(r.intersects(x) for x in images + drawings):
                visual.append(pymupdf.Rect(r))
    return [trim(pt, g, taken) for g in groups], visual, field_xrefs, taken, removed


def by_line(pt, idx):
    groups = defaultdict(list)
    for i in idx:
        groups[pt.lines[i]].append(i)
    return list(groups.values())


def segments(pt, idx):
    """The removed text as strings (one per run of consecutive characters)."""
    out, cur, last = [], [], None
    for i in sorted(idx):
        if last is not None and i != last + 1:
            out.append(cur)
            cur = []
        cur.append(i)
        last = i
    if cur:
        out.append(cur)
    return ["".join(pt.text[j] for j in run).strip() for run in out]


def trim(pt, group, taken):
    """The area covering a group of characters, shrunk so it touches no character that must stay:
    MuPDF removes every character a redaction area overlaps."""
    u = union([pt.boxes[i] for i in group])
    u = pymupdf.Rect(u.x0 + 0.3, u.y0, u.x1 - 0.3, u.y1)
    if u.x1 <= u.x0:
        c = (u.x0 + u.x1) / 2
        u.x0, u.x1 = c - 0.1, c + 0.1
    line, cx, cy = pt.lines[group[0]], (u.x0 + u.x1) / 2, (u.y0 + u.y1) / 2
    for j, bx in enumerate(pt.boxes):
        if bx is None or j in taken or pt.text[j].isspace():
            continue
        if not (bx.x0 < u.x1 and bx.x1 > u.x0 and bx.y0 < u.y1 and bx.y1 > u.y0):
            continue
        if pt.lines[j] == line:
            if (bx.x0 + bx.x1) / 2 < cx:
                u.x0 = max(u.x0, bx.x1 + 0.05)
            else:
                u.x1 = min(u.x1, bx.x0 - 0.05)
        elif (bx.y0 + bx.y1) / 2 < cy:
            u.y0 = max(u.y0, bx.y1 + 0.05)
        else:
            u.y1 = min(u.y1, bx.y0 - 0.05)
    if u.height < 1.0:
        u.y0, u.y1 = cy - 0.5, cy + 0.5
    return u


def replace_spans(text, spans, placeholder):
    merged = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    for s, e in reversed(merged):
        text = text[:s] + placeholder + text[e:]
    return text


def clean_fields(page, xrefs, terms):
    """Form fields under a box: list items in their value are replaced; a value with no list item
    (a field boxed by the reviewer) is replaced entirely. Returns (field rectangles, removed values)."""
    rects, removed = [], []
    for w in text_fields(page):
        if w.xref not in xrefs:
            continue
        value = widget_value(w)
        spans = [sp for t in terms for sp in t.find(normalize(value))]
        if spans:
            new, gone = replace_spans(value, spans, PLACEHOLDER), [value[s:e] for s, e in spans]
        else:
            new, gone = (PLACEHOLDER if value else ""), ([value] if value else [])
        w.field_value = new[:w.text_maxlen] if w.text_maxlen else new
        w.update()
        rects.append(pymupdf.Rect(w.rect))
        removed += gone
    return rects, removed


def removed_terms(removed, terms):
    """Text taken out under boxes that isn't simply a list item, as search terms (4+ letters or digits)."""
    listed = {t.pattern.pattern for t in terms}
    out = []
    for s in dict.fromkeys(removed):
        if sum(c.isalnum() for c in s) < 4:
            continue
        t = Term(s)
        if t.pattern is None or t.pattern.pattern in listed:
            continue
        if any(lt.pattern.fullmatch(normalize(s).strip(" .,;:")) for lt in terms):
            continue
        listed.add(t.pattern.pattern)
        out.append(t)
    return out


def clean_hidden(doc, secrets, drop_attachments):
    """Replace list items and removed text kept outside the pages: document properties, XMP metadata,
    bookmarks, comments. Attachments go with drop_attachments; an XFA copy of the form data always goes."""
    notes = []

    def scrub(s):
        spans = [sp for t in secrets for sp in t.find(normalize(s))]
        return (replace_spans(s, spans, PLACEHOLDER), True) if spans else (s, False)

    meta = dict(doc.metadata or {})
    changed = [k for k in META_KEYS if meta.get(k) and scrub(meta[k])[1]]
    if changed:
        meta.update({k: scrub(meta[k])[0] for k in changed})
        doc.set_metadata({k: v for k, v in meta.items() if k not in ("format", "encryption")})
        notes.append(f"cleaned document properties: {', '.join(changed)}")
    xmp = doc.get_xml_metadata()
    if xmp and xmp.strip() and scrub(re.sub(r"<[^>]+>", " ", xmp))[1]:
        doc.del_xml_metadata()
        notes.append("removed the XMP metadata (it contained list items)")
    n = 0
    for x in outline_xrefs(doc):
        new, hit = scrub(pdf_string(doc, x, "Title"))
        if hit:
            doc.xref_set_key(x, "Title", pymupdf.get_pdf_str(new))
            n += 1
    if n:
        notes.append(f"cleaned {n} bookmark(s)")
    n = 0
    for page in doc:
        for a in list(page.annots()):
            if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                if drop_attachments:
                    page.delete_annot(a)
                    notes.append("removed a file attachment annotation")
                continue
            upd = {}
            for k in ("content", "title", "subject"):
                if a.info.get(k):
                    new, hit = scrub(a.info[k])
                    if hit:
                        upd[k] = new
            if upd:
                a.set_info(**upd)
                if doc.xref_get_key(a.xref, "RC")[0] != "null":
                    doc.xref_set_key(a.xref, "RC", "null")        # the comment's rich-text copy
                a.update()
                n += 1
    if n:
        notes.append(f"cleaned {n} comment(s)")
    if drop_attachments and doc.embfile_count():
        for name in doc.embfile_names():
            doc.embfile_del(name)
        notes.append("removed the attached files")
    if has_xfa(doc):
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/XFA", "null")
        notes.append("removed the XFA copy of the form data (the regular form fields stay)")
    return notes
