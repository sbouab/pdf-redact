"""What a redacted copy must not carry besides the boxed text: everything no one sees when looking at its pages.

Removed from every redacted copy, whatever the list holds (a reviewer can only judge what is shown):
* document properties (the standard ones and any other), XMP metadata (anywhere in the file), and the
  metadata kept inside JPEG pictures (EXIF, XMP, IPTC, comments);
* bookmarks, the text of comments, sticky notes and hidden comments, links to anything outside the document;
* scripts and automatic actions (also those an action runs after itself), page thumbnails, structure tags
  (they can hold a copy of the text, or a description of a picture), application data, alternate versions of
  pictures, article threads, the default values and tooltips of form fields, and the values of the form
  fields that no page shows (hidden, or on no page);
* on every page, invisible text (such as the text layer of a scan, or text too small to read) and whatever lies
  outside the visible page
  (text, and the parts of pictures cut off by its edges).

Hidden layers are shown when a PDF is opened (open_pdf calls show_all_layers), so their content is seen,
searched and redacted like the rest of the page. The functions at the end tell what is left, for the checks.
"""
import re

import pymupdf

EDGE = 1.0          # points: what lies beyond the visible page by more than this is outside it
FAR = 1e6           # points: far enough to cover anything outside a page
TINY = 1.0          # points: text smaller than this can't be read, so it counts as invisible

# keys whose value is never shown on a page (in any object of the file)
_HIDDEN_KEYS = ("PieceInfo", "Thumb", "AA", "Alternates", "DV", "TU", "Metadata")
# actions that run a script, open another file or a website, or send data
_ACTIONS = {"/JavaScript", "/Launch", "/URI", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE", "/Rendition"}
_LINKS_KEPT = {pymupdf.LINK_GOTO, pymupdf.LINK_NAMED}     # links within the document carry no text
_ANNOT_TEXT = ("T", "Subj", "NM", "RC", "M", "CreationDate")   # author, subject, name, rich text, dates
_FIELD_CONTENT = ("V", "RV", "AP")     # a form field's value, its rich-text copy, and its appearance (shows it)
# JPEG segments a picture needs to be shown: APP0 (JFIF), APP2 (ICC colour profile), APP14 (Adobe colour
# transform). Every other application segment (EXIF, XMP, IPTC...) and comment only describes the picture.
_JPEG_KEPT = {0xE0, 0xE2, 0xEE}


def show_all_layers(doc):
    """Make the content of every layer show, hidden layers included: without its layer settings a PDF shows
    all of its optional content. Returns True if the document had layers."""
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "OCProperties")[0] == "null":
        return False
    doc.xref_set_key(catalog, "OCProperties", "null")
    return True


# ----------------------------------------------------------------------------- the whole document
def strip_document(doc):
    """Remove what is kept outside the pages and never shown on them (see the module's description). Run before
    the pages are redacted. Returns notes on what was removed."""
    notes = []
    meta = {k: v for k, v in (doc.metadata or {}).items() if v and k not in ("format", "encryption")}
    if meta or doc.xref_get_key(-1, "Info")[0] != "null":     # doc.metadata only tells the standard properties
        doc.set_metadata({})
        doc.xref_set_key(-1, "Info", "null")       # also when set_metadata leaves it (written in the trailer itself)
        notes.append("removed the document properties")
    toc = doc.get_toc(simple=True)
    if toc:
        doc.set_toc([])
        notes.append(f"removed {len(toc)} bookmark(s)")
    catalog = doc.pdf_catalog()
    found = set()
    for key in ("OpenAction", "Names/JavaScript", "StructTreeRoot", "MarkInfo", "Threads", "URI", "SpiderInfo"):
        if doc.xref_get_key(catalog, key)[0] != "null":
            doc.xref_set_key(catalog, key, "null")
            found.add(key)
    if doc.get_xml_metadata():
        doc.del_xml_metadata()
        found.add("Metadata")
    unseen = _form_fields(doc) - _fields_shown(doc)
    fields = pictures = 0
    for x in range(1, doc.xref_length()):
        keys = _keys(doc, x)
        for key in _HIDDEN_KEYS:
            if key in keys:
                doc.xref_set_key(x, key, "null")
                found.add(key)
        if "A" in keys and doc.xref_get_key(x, "A/S")[1] in _ACTIONS and doc.xref_get_key(x, "Subtype")[1] != "/Link":
            doc.xref_set_key(x, "A", "null")
            found.add("AA")
        if _strip_next_actions(doc, x, keys):
            found.add("AA")
        if "B" in keys and doc.xref_get_key(x, "Type")[1] == "/Page":
            doc.xref_set_key(x, "B", "null")              # the beads of article threads
        if x in unseen:
            content = [k for k in _FIELD_CONTENT if k in keys]
            for k in content:
                doc.xref_set_key(x, k, "null")
            fields += bool(content)
        if "Filter" in keys:
            pictures += _strip_jpeg_metadata(doc, x)
    notes += [text for key, text in (("Metadata", "removed the XMP metadata"),
                                     ("StructTreeRoot",
                                      "removed the structure tags (they can hold a copy of the text)"),
                                     ("Thumb", "removed the page thumbnails"),
                                     ("PieceInfo", "removed the data kept by the application that made the PDF"),
                                     ("Alternates", "removed alternate versions of pictures"),
                                     ("AA", "removed scripts and automatic actions"),
                                     ("OpenAction", "removed the action run when the PDF opens"),
                                     ("Names/JavaScript", "removed the document's scripts"),
                                     ("DV", "removed the default values of form fields"),
                                     ("TU", "removed the tooltips of form fields"))
              if key in found]
    if fields:
        notes.append(f"removed the values of {fields} form field(s) that no page shows")
    if pictures:
        notes.append(f"removed the metadata of {pictures} picture(s)")
    notes += _strip_comments_and_links(doc)
    return notes


def _strip_next_actions(doc, xref, keys):
    """Remove the actions run after an action (its /Next, where a script can follow a harmless 'go to page'): of
    an action that is an object of its own, or of the one written in the object's /A. True if there were any."""
    removed = False
    if "S" in keys and "Next" in keys:
        doc.xref_set_key(xref, "Next", "null")
        removed = True
    if "A" in keys and doc.xref_get_key(xref, "A")[0] == "dict" and doc.xref_get_key(xref, "A/Next")[0] != "null":
        doc.xref_set_key(xref, "A/Next", "null")
        removed = True
    return removed


def _strip_comments_and_links(doc):
    """Delete sticky notes and comments that are never shown; strip the others of their hidden text (author,
    note, dates); delete the links to anything outside the document."""
    notes, invisible, cleaned, links = 0, 0, 0, 0
    for page in doc:
        for xref, kind, _ in page.annot_xrefs():
            if kind in (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_POPUP):
                continue
            hidden = _hidden(doc, xref)
            if hidden or kind == pymupdf.PDF_ANNOT_TEXT:          # not shown at all, or only a note's icon
                page.delete_annot(page.load_annot(xref))          # (its pop-up goes with it)
                invisible += bool(hidden)
                notes += not hidden
                continue
            keys = _keys(doc, xref)
            dropped = [k for k in _ANNOT_TEXT if k in keys]
            if "Contents" in keys and kind != pymupdf.PDF_ANNOT_FREE_TEXT:       # a free text box shows its text
                dropped.append("Contents")
            for k in dropped:
                doc.xref_set_key(xref, k, "null")
            cleaned += bool(dropped)
        for ln in page.get_links():
            if ln["kind"] not in _LINKS_KEPT:
                page.delete_link(ln)
                links += 1
    return [text for n, text in ((notes, f"removed {notes} sticky note(s)"),
                                 (invisible, f"removed {invisible} hidden comment(s)"),
                                 (cleaned, f"removed the hidden text of {cleaned} comment(s) (author, notes, dates)"),
                                 (links, f"removed {links} link(s) to outside the document"))
            if n]


def _keys(doc, xref):
    try:
        return set(doc.xref_get_keys(xref))
    except Exception:            # a free or broken object
        return set()


def _hidden(doc, xref):
    """Whether the annotation `xref` is flagged hidden: never shown, never printed."""
    t, v = doc.xref_get_key(xref, "F")
    return t == "int" and bool(int(v) & pymupdf.PDF_ANNOT_IS_HIDDEN)


# ----------------------------------------------------------------------------- form fields
def _fields_shown(doc):
    """The object numbers of the form fields the pages show: their widgets that aren't hidden, and the fields
    these belong to (the value of a field with several widgets is kept by the field)."""
    shown, last = set(), doc.xref_length()
    for page in doc:
        for xref, kind, _ in page.annot_xrefs():
            if kind != pymupdf.PDF_ANNOT_WIDGET or _hidden(doc, xref):
                continue
            while 0 < xref < last and xref not in shown:
                shown.add(xref)
                t, v = doc.xref_get_key(xref, "Parent")
                xref = int(v.split()[0]) if t == "xref" else 0
    return shown


def _form_fields(doc):
    """The object numbers of every field of the document's form and of their widgets (the tree under
    AcroForm/Fields), whether a page shows them or not."""
    out, todo = set(), _references(doc, doc.pdf_catalog(), "AcroForm/Fields")
    while todo:
        x = todo.pop()
        if x not in out:
            out.add(x)
            todo += _references(doc, x, "Kids")
    return out


def _references(doc, xref, key):
    """The objects listed in the array at `key` of object `xref` (the array may be an object of its own);
    references to objects the file doesn't have are left out."""
    t, v = doc.xref_get_key(xref, key)
    if t == "xref":
        v = doc.xref_object(int(v.split()[0]), compressed=True)
    elif t != "array":
        return []
    last = doc.xref_length()
    return [n for n in map(int, re.findall(r"([0-9]+) [0-9]+ R", v)) if 0 < n < last]


# ----------------------------------------------------------------------------- JPEG pictures
def _is_jpeg(doc, xref):
    """Whether object `xref` is a JPEG picture, stored as it is (its only filter is DCTDecode)."""
    return doc.xref_get_key(xref, "Filter")[1].replace(" ", "") in ("/DCTDecode", "[/DCTDecode]")


def _jpeg_without_metadata(data):
    """`data`, a JPEG picture, without the segments that only describe it (see _JPEG_KEPT): EXIF (camera, place,
    date, often a small copy of the picture), XMP, IPTC, comments. None when it has none, or when it isn't laid
    out as this expects (then it is left as it is). The picture data itself is kept byte for byte."""
    if data[:2] != bytes([0xFF, 0xD8]):
        return None
    kept, i, dropped = [data[:2]], 2, False
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xFF:                                   # a fill byte before the marker
            i += 1
        elif marker == 0xDA:                                 # start of the picture data, which runs to the end
            return b"".join(kept) + data[i:] if dropped else None
        elif marker == 0x01 or 0xD0 <= marker <= 0xD9:      # markers without a length: unexpected here
            return None
        else:
            end = i + 2 + int.from_bytes(data[i + 2:i + 4], "big")
            if marker == 0xFE or (0xE0 <= marker <= 0xEF and marker not in _JPEG_KEPT):
                dropped = True
            else:
                kept.append(data[i:end])
            i = end
    return None


def _strip_jpeg_metadata(doc, xref):
    """Remove the metadata of object `xref` if it is a JPEG picture that has any. True if it had."""
    if not _is_jpeg(doc, xref):
        return False
    clean = _jpeg_without_metadata(doc.xref_stream_raw(xref))
    if clean is None:
        return False
    parms = doc.xref_get_key(xref, "DecodeParms")
    doc.update_stream(xref, clean, compress=False)     # this also drops /Filter and /DecodeParms, which still apply
    doc.xref_set_key(xref, "Filter", "/DCTDecode")
    if parms[0] != "null":
        doc.xref_set_key(xref, "DecodeParms", parms[1])
    return True


# ----------------------------------------------------------------------------- one page
def visible_area(page):
    """The visible part of a page, in the coordinates of the unrotated page (those of its text and annotations)."""
    return page.rect * page.derotation_matrix


def hidden_chars(page, pt):
    """Indices of the characters of `pt` (the page's PageText) that no one sees: invisible ones (see _unseen)
    and those cut off by the page's edges. Call it before the page is changed."""
    invisible = {}
    for span in page.get_texttrace():
        if _unseen(span):
            for c in span["chars"]:
                invisible.setdefault(chr(c[0]), []).append(pymupdf.Rect(c[3]))
    edge = visible_area(page) + (-EDGE, -EDGE, EDGE, EDGE)
    out = set()
    for i, b in enumerate(pt.boxes):
        if b is None or pt.text[i].isspace():
            continue
        if not _within(b, edge) or any(_same_place(b, r) for r in invisible.get(pt.text[i], ())):
            out.add(i)
    return out


def _within(box, area):
    """Whether `box` (x0, y0, x1, y1) lies within `area`, judged by its corners alone: unlike Rect's own test, an
    empty box far off the page is outside too. And it is much faster, which counts on every character."""
    return area.x0 <= box[0] and area.y0 <= box[1] and box[2] <= area.x1 and box[3] <= area.y1


def _unseen(span):
    """Whether a span of get_texttrace() is drawn so that no one sees it: invisibly (like the text layer of a
    scan), fully transparent, or too small to read."""
    return span["type"] == 3 or span["opacity"] == 0 or span["size"] < TINY


def _same_place(a, b):
    """True for the boxes of one character as two extractions give them (they differ in height only)."""
    return abs(a.x0 - b.x0) < 0.05 and abs(a.x1 - b.x1) < 0.05 and abs((a.y0 + a.y1) - (b.y0 + b.y1)) / 2 < 3


def strip_page(page, hidden_areas):
    """Remove the invisible text of a page (`hidden_areas`, made from hidden_chars) and everything outside its
    visible part: text, the parts of pictures beyond its edges, and drawings that lie wholly outside.
    Returns True when something lay outside the visible page."""
    for r in hidden_areas:
        page.add_redact_annot(r, fill=False)
    if hidden_areas:              # the text only: a scan's picture under its text layer stays as it is
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    visible = visible_area(page)
    if not _anything_outside(page, visible):
        return False
    v = visible + (-EDGE, -EDGE, EDGE, EDGE)
    for band in (pymupdf.Rect(-FAR, -FAR, FAR, v.y0), pymupdf.Rect(-FAR, v.y1, FAR, FAR),
                 pymupdf.Rect(-FAR, v.y0, v.x0, v.y1), pymupdf.Rect(v.x1, v.y0, FAR, v.y1)):
        page.add_redact_annot(band, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return True


def _anything_outside(page, visible):
    edge = visible + (-EDGE, -EDGE, EDGE, EDGE)
    for kind, bbox in page.get_bboxlog():
        if not kind.startswith("clip") and not pymupdf.Rect(bbox).is_empty and not _within(bbox, edge):
            return True
    return False


# ----------------------------------------------------------------------------- what is left, for the checks
def invisible_count(page):
    """How many invisible characters (other than spaces) the page still has."""
    return sum(1 for span in page.get_texttrace() if _unseen(span) for c in span["chars"] if not chr(c[0]).isspace())


def outside_count(page):
    """How many characters (other than spaces) lie outside the visible page."""
    edge = visible_area(page) + (-EDGE, -EDGE, EDGE, EDGE)
    raw = page.get_text("rawdict", flags=0, clip=pymupdf.INFINITE_RECT())
    return sum(1 for block in raw["blocks"] for line in block.get("lines", []) for span in line["spans"]
               for ch in span["chars"] if not ch["c"].isspace() and not _within(ch["bbox"], edge))


def metadata_left(doc):
    """True when the file still has document properties (standard or not), XMP metadata, or a JPEG picture's
    metadata."""
    if doc.xref_get_key(-1, "Info")[0] != "null":
        return True
    for x in range(1, doc.xref_length()):
        if doc.xref_get_key(x, "Metadata")[0] not in ("null", "") or doc.xref_get_key(x, "Type")[1] == "/Metadata":
            return True
        if _is_jpeg(doc, x) and _jpeg_without_metadata(doc.xref_stream_raw(x)) is not None:
            return True
    return False


_STRING = re.compile(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<[0-9A-Fa-f\s]*>", re.S)
_NAME = re.compile(rb"/([^\s/<>\[\]()]+)\s*$")
_SKIP_KEYS = {"ID", "DA", "DS", "FontFamily", "Lang", "O", "U", "OE", "UE", "Perms", "ByteRange"}


def pdf_strings(doc):
    """(where, text) for every text string in the objects of the file, except the values of the form fields the
    pages show (checked on their own), signatures and the strings that only name fonts or styles. The text of
    the pages is not here."""
    shown = _fields_shown(doc)
    for x in range(1, doc.xref_length()):
        try:
            source = doc.xref_object(x, compressed=False).encode("latin-1", "replace")
        except Exception:
            continue
        if b"/Type /Sig" in source or b"/Type/Sig" in source:
            continue
        for m in _STRING.finditer(source):
            if m.group().startswith(b"<<"):
                continue
            name = _NAME.search(source[:m.start()][-80:])
            key = name.group(1).decode("latin-1") if name else ""
            if key in _SKIP_KEYS or (key == "V" and x in shown):
                continue
            text = _decode(m.group())
            if text.strip():
                yield f"the PDF's hidden data (/{key})" if key else "the PDF's hidden data", text


def _decode(token):
    if token.startswith(b"<"):
        digits = re.sub(rb"\s", b"", token[1:-1])
        raw = bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())
    else:
        raw = re.sub(rb"\\([0-7]{1,3}|.)", lambda m: _unescape(m.group(1)), token[1:-1], flags=re.S)
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):                    # PDF 2.0
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("latin-1").translate(_PDFDOC)


# PDFDocEncoding, the encoding of text strings without a byte order mark, is Latin-1 but for these codes
# (PDF 32000-1, annex D.2); 0x9F and 0xAD are undefined
_PDFDOC = (dict(zip(range(0x18, 0x20), "˘ˇˆ˙˝˛˚˜"))
           | dict(zip(range(0x80, 0xA1), "•†‡…—–ƒ⁄‹›−‰„“”‘’‚™ﬁﬂŁŒŠŸŽıłœšž\ufffd€")))


def _unescape(code):
    if code[:1].isdigit():
        return bytes([int(code, 8) & 0xFF])
    return {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"\n": b"", b"\r": b""}.get(code, code)
