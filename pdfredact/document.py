"""Opening PDFs, form fields, and the places where a PDF keeps text outside its pages."""
import hashlib
import re

import pymupdf

from . import TAG, UserError

TEXT_FIELDS = {pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX}
META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")
XFA_HELP = ("this is an Adobe XFA form: its filled-in data is not on the pages, so it can't be searched or "
            "redacted. Open it in Adobe Acrobat Reader, print it to PDF (macOS: Print > Save as PDF; "
            "Windows: Microsoft Print to PDF) and use the printed copy instead.")


def open_pdf(path, password=None):
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise UserError(f"can't open {path}: {e}")
    if not doc.is_pdf:
        raise UserError(f"{path} is not a PDF")
    if doc.needs_pass and not (password and doc.authenticate(password)):
        raise UserError(f"{path} is password-protected: add --password")
    return doc


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp(source_hash, list_hash):
    """What a review copy records about how it was made: fingerprints of the original and of the list."""
    return f"{TAG} source-sha256={source_hash} list-sha256={list_hash}"


def set_review_metadata(doc, stamp_text):
    meta = dict(doc.metadata or {})
    meta.update(title="REVIEW COPY - NOT REDACTED", keywords=stamp_text)
    doc.set_metadata({k: v for k, v in meta.items() if k not in ("format", "encryption")})


def read_stamp(doc):
    """(source hash, list hash) recorded in a review copy: in its properties, or on a page banner if a
    viewer dropped the properties. (None, None) if neither survived."""
    places = [(doc.metadata or {}).get("keywords") or ""]
    places += [a.info.get("subject") or "" for page in doc for a in page.annots() if a.info.get("title") == TAG]
    for text in places:
        m = re.search(r"source-sha256=([0-9a-f]{64}) list-sha256=([0-9a-f]{64})", text)
        if m:
            return m.group(1), m.group(2)
    return None, None


def text_fields(page):
    return [w for w in page.widgets() if w.field_type in TEXT_FIELDS]


def widget_value(w):
    v = w.field_value
    if isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    return "" if v in (None, False, "Off") else str(v)


# ----------------------------------------------------------------------------- XFA forms
def has_xfa(doc):
    return doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")[0] != "null"


def is_dynamic_xfa(doc):
    """An XFA form whose content isn't on the pages at all (they show 'Please wait...' or nothing)."""
    if not has_xfa(doc):
        return False
    no_fields = not any(True for page in doc for _ in page.widgets())
    placeholder = len(doc) > 0 and "please wait" in doc[0].get_text().lower()
    return no_fields or placeholder


def xfa_text(doc):
    """The filled-in data of an XFA form (its 'datasets' packet) as plain text."""
    t, v = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")
    xml = ""
    try:
        if t == "array":
            m = re.search(r"\(datasets\)\s*(\d+)\s+0\s+R", v)
            xml = doc.xref_stream(int(m.group(1))).decode("utf-8", "replace") if m else ""
        elif t == "xref":
            whole = doc.xref_stream(int(v.split()[0])).decode("utf-8", "replace")
            m = re.search(r"<xfa:datasets.*?</xfa:datasets>", whole, re.S)
            xml = m.group(0) if m else ""
    except Exception:
        xml = ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)).strip()


# ----------------------------------------------------------------------------- text outside the pages
def outline_xrefs(doc):
    """Every bookmark (outline item) of the document."""
    t, v = doc.xref_get_key(doc.pdf_catalog(), "Outlines")
    if t != "xref":
        return []
    out, seen = [], set()
    t, v = doc.xref_get_key(int(v.split()[0]), "First")
    stack = [int(v.split()[0])] if t == "xref" else []
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
        for key in ("Next", "First"):
            t, v = doc.xref_get_key(x, key)
            if t == "xref":
                stack.append(int(v.split()[0]))
    return out


def pdf_string(doc, xref, key):
    t, v = doc.xref_get_key(xref, key)
    return v if t == "string" else ""


def hidden_texts(doc):
    """(where, text) for text kept outside the page content: document properties, XMP metadata,
    bookmarks, comments, links, attachment names and XFA form data. Form fields are handled apart."""
    out = []
    meta = doc.metadata or {}
    out += [(f"document property '{k}'", meta[k]) for k in META_KEYS if meta.get(k)]
    xmp = doc.get_xml_metadata()
    if xmp and xmp.strip():
        out.append(("XMP metadata", re.sub(r"<[^>]+>", " ", xmp)))
    out += [("bookmark", pdf_string(doc, x, "Title")) for x in outline_xrefs(doc)]
    for pno, page in enumerate(doc, 1):
        for a in page.annots():
            if a.type[0] != pymupdf.PDF_ANNOT_REDACT:
                out += [(f"comment on page {pno}", a.info[k]) for k in ("content", "title", "subject") if a.info.get(k)]
        out += [(f"link on page {pno}", ln["uri"]) for ln in page.get_links() if ln.get("uri")]
    out += [("attachment name", name) for name in doc.embfile_names()]
    if has_xfa(doc):
        out.append(("XFA form data", xfa_text(doc)))
    return out


def document_warnings(doc):
    w = []
    if has_xfa(doc):
        w.append("XFA form: apply removes its hidden copy of the form data (the regular form fields stay)")
    attached = doc.embfile_count() + sum(1 for p in doc for a in p.annots() if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT)
    if attached:
        w.append(f"{attached} attached file(s) inside the PDF can't be checked: apply fails unless you add --drop-attachments")
    if any(wd.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE for p in doc for wd in p.widgets()):
        w.append("digitally signed: the signature holds the signer's identity, and redacting invalidates it")
    try:
        if doc.get_ocgs():
            w.append("has layers: text on hidden layers may not be found")
    except Exception:
        pass
    return w
