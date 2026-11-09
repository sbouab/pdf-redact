"""A redacted copy carries nothing no one can see on its pages: no metadata, no hidden text, no hidden data,
whatever the list holds. Run from the repository folder:  uv run pytest"""
import unicodedata
import zlib

import pymupdf
import pytest

from pdfredact import redaction
from pdfredact.document import open_pdf
from pdfredact.redaction import redact
from pdfredact.review import boxes_from, find_matches
from pdfredact.sanitize import metadata_left, pdf_strings
from pdfredact.text import Term

SECRET = "Quartermaine"          # never on a page anyone sees: it must be nowhere in the redacted copy
ITEM = "Zorblax Nomen"           # on the list, written on a hidden layer
LAYER_TEXT = "Layer words"       # on a hidden layer too, not on the list: shown, so it stays


def leaky_pdf(path):
    """A PDF keeping SECRET in every hidden place known to leak from redacted documents."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 100), "Visible heading of the letter")
    page.insert_text((72, 200), f"{SECRET} invisible", render_mode=3)                # like a scan's text layer
    page.insert_text((72, 230), f"{SECRET} too small to see", fontsize=0.5)
    page.insert_text((72, 700), f"{SECRET} below the visible page")                 # cut off by the crop box
    page.insert_text((-300, 300), f"{SECRET} left of the page")                     # outside the media box
    hidden = doc.add_ocg("Hidden layer", on=False)
    page.insert_text((72, 400), f"{ITEM} {LAYER_TEXT}", oc=hidden)
    page.set_cropbox(pymupdf.Rect(0, 0, 600, 600))

    doc.set_metadata({k: SECRET for k in ("title", "author", "subject", "keywords", "creator", "producer")})
    xmp = f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><dc:creator xmlns:dc='dc'>{SECRET}</dc:creator></x:xmpmeta>"
    doc.set_xml_metadata(xmp)
    doc.set_toc([[1, f"About {SECRET} and {ITEM}", 1]])
    note = page.add_text_annot((400, 50), f"{SECRET} sticky note")
    note.set_info(title=SECRET)
    note.update()
    mark = page.add_highlight_annot(page.search_for("Visible heading")[0])
    mark.set_info(title=SECRET, content=f"{SECRET} comment", subject=SECRET)
    mark.update()
    box = page.add_freetext_annot(pymupdf.Rect(300, 300, 550, 330), f"{SECRET} hidden box")
    box.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
    box.update()
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 250, 105),
                      "uri": f"https://example.com/{SECRET}"})
    field = pymupdf.Widget()
    field.field_type, field.field_name, field.field_value = pymupdf.PDF_WIDGET_TYPE_TEXT, "who", "a visible value"
    field.rect = pymupdf.Rect(72, 500, 300, 520)
    page.add_widget(field)
    doc.save(path)
    doc.close()

    doc = pymupdf.open(path)          # what PyMuPDF can't write directly
    page, catalog = doc[0], doc.pdf_catalog()
    widget = next(page.widgets())
    doc.xref_set_key(widget.xref, "DV", pymupdf.get_pdf_str(SECRET))
    doc.xref_set_key(widget.xref, "TU", pymupdf.get_pdf_str(f"Tooltip for {ITEM}"))
    script = doc.get_new_xref()
    doc.update_object(script, f"<< /S /JavaScript /JS {pymupdf.get_pdf_str(SECRET)} >>")
    doc.xref_set_key(catalog, "Names", f"<< /JavaScript << /Names [(run) {script} 0 R] >> >>")
    doc.xref_set_key(catalog, "OpenAction", f"{script} 0 R")
    doc.xref_set_key(page.xref, "AA", f"<< /O {script} 0 R >>")
    tags = doc.get_new_xref()
    doc.update_object(tags, f"<< /Type /StructTreeRoot /K << /S /Figure /Alt {pymupdf.get_pdf_str(SECRET)} >> >>")
    doc.xref_set_key(catalog, "StructTreeRoot", f"{tags} 0 R")
    doc.xref_set_key(page.xref, "PieceInfo", f"<< /Editor << /Private {pymupdf.get_pdf_str(SECRET)} >> >>")
    doc.xref_set_key(page.xref, "Thumb", f"<< /Note {pymupdf.get_pdf_str(SECRET)} >>")
    meta = doc.get_new_xref()
    doc.update_object(meta, "<< /Type /Metadata /Subtype /XML >>")
    doc.update_stream(meta, xmp.encode())
    doc.xref_set_key(page.xref, "Metadata", f"{meta} 0 R")
    doc.saveIncr()
    doc.close()
    return path


def everywhere(path):
    """Every byte a reader could get out of the file: each object and each stream, decompressed, plus the text
    of the pages wherever it lies, visible or not."""
    doc = pymupdf.open(path)
    parts = [doc.xref_object(x, compressed=False) for x in range(1, doc.xref_length())]
    for x in range(1, doc.xref_length()):
        try:
            parts.append((doc.xref_stream(x) or b"").decode("latin-1"))
        except Exception:
            pass
    parts += [p.get_text("text", flags=0, clip=pymupdf.INFINITE_RECT()) for p in doc]
    doc.close()
    raw = open(path, "rb").read()
    for chunk in raw.split(b"stream")[1:]:              # streams as stored, in case one escaped the above
        try:
            parts.append(zlib.decompressobj().decompress(chunk.lstrip(b"\r\n")).decode("latin-1"))
        except zlib.error:
            pass
    text = "\n".join(parts)
    return text + "\n" + text.encode("latin-1", "replace").decode("utf-16-be", "replace")


def redacted(src, items=(ITEM,)):
    """`src` redacted with `items` on the list, every match boxed: (the redacted copy, the Result, the matches)."""
    terms = [Term(x) for x in items]
    doc = open_pdf(str(src))
    matches = find_matches(doc, terms)
    boxes = {pno: [(r, kind or "text") for r, kind, _ in found] for pno, found in boxes_from(matches).items()}
    out = src.with_name(src.stem + "-redacted.pdf")
    r = redact(doc, boxes, terms, str(out))
    doc.close()
    return out, r, matches


def redact_leaky(tmp_path):
    return redacted(leaky_pdf(tmp_path / "leaky.pdf"))


def test_nothing_hidden_is_left(tmp_path):
    out, r, matches = redact_leaky(tmp_path)
    assert len(matches) == 1                           # the item on the hidden layer was found
    assert r.fails == [], r.fails
    text = everywhere(out)
    assert SECRET.lower() not in text.lower() and "zorblax" not in text.lower()
    doc = pymupdf.open(out)
    assert LAYER_TEXT in doc[0].get_text()             # a hidden layer is shown: what it holds was reviewed
    assert not any(v for k, v in doc.metadata.items() if k not in ("format", "encryption"))
    assert doc.get_xml_metadata() == "" and doc.get_toc() == [] and doc[0].get_links() == []
    assert [a.type[0] for a in doc[0].annots()] == [pymupdf.PDF_ANNOT_HIGHLIGHT]   # shown: stays, with no text
    assert next(doc[0].widgets()).field_value == "a visible value"
    doc.close()
    notes = " | ".join(r.notes)
    for removed in ("document properties", "XMP metadata", "bookmark", "sticky note", "hidden comment",
                    "link(s) to outside", "structure tags", "scripts", "invisible text", "outside the visible page"):
        assert removed in notes, (removed, notes)


def test_the_checks_catch_what_is_left_hidden(tmp_path, monkeypatch):
    """Without the removal, the checks fail: the redacted copy could not be saved or shared."""
    monkeypatch.setattr(redaction, "strip_document", lambda doc: [])
    monkeypatch.setattr(redaction, "strip_page", lambda page, areas: False)
    out, r, _ = redact_leaky(tmp_path)
    codes = {f.code for f in r.fails}
    assert {"metadata-left", "invisible-text", "outside-text", "hidden-place"} <= codes, r.fails


def test_a_hidden_layer_is_shown_when_opened(tmp_path):
    src = leaky_pdf(tmp_path / "leaky.pdf")
    doc = open_pdf(str(src))
    assert LAYER_TEXT in doc[0].get_text() and doc.get_ocgs() == {}
    doc.close()
    raw = pymupdf.open(src)                             # the file itself is not changed
    assert raw.get_ocgs() and LAYER_TEXT not in raw[0].get_text()
    raw.close()


def test_hidden_strings_are_read_in_each_text_encoding():
    """PDFDocEncoding (not Latin-1: 0x9C is œ, 0x90 a right quote), UTF-16 and UTF-8 (PDF 2.0)."""
    doc = pymupdf.open()
    doc.new_page()
    x = doc.get_new_xref()
    doc.update_object(x, "<< /A <439C7572204F90427269656E> /B <FEFF004C00E90061> /C <EFBBBF436166C3A9> >>")
    found = {key: text for key, text in pdf_strings(doc)}
    assert found == {"the PDF's hidden data (/A)": "Cœur O’Brien", "the PDF's hidden data (/B)": "Léa",
                     "the PDF's hidden data (/C)": "Café"}


@pytest.mark.parametrize("name", ["letter_fr.pdf", "t4_slip.pdf", "form_fillable.pdf", "scan_with_text_layer.pdf",
                                  "rotated_page.pdf"])
def test_samples_redact_clean(tmp_path, name):
    """The usual documents: nothing of the list anywhere in the file, and every check passes."""
    from helpers import LIST, sample
    terms = [Term(x) for x in LIST]
    doc = open_pdf(str(sample(tmp_path, name)))
    matches = find_matches(doc, terms)
    boxes = {pno: [(r, kind or "text") for r, kind, _ in found] for pno, found in boxes_from(matches).items()}
    out = tmp_path / "out.pdf"
    r = redact(doc, boxes, terms, str(out))
    doc.close()
    assert r.fails == [], r.fails
    text = plain(everywhere(out))
    for item in LIST:
        assert plain(item) not in text, (name, item)


# accented Latin letters to their plain letter; a table, as normalizing the megabytes of a scan's picture, read
# as text, takes minutes
_FOLD = {c: unicodedata.normalize("NFD", chr(c))[0] for c in range(0xC0, 0x250)}


def plain(text):
    """Lower case, accents dropped, then every other non-ASCII character."""
    return text.lower().translate(_FOLD).encode("ascii", "ignore").decode()


# ----------------------------------------------------------------------------- more places no one sees
def one_page(path, change=None):
    """A PDF of one page with a line of visible text, changed by `change(doc)` before it is saved."""
    doc = pymupdf.open()
    doc.new_page(width=600, height=800).insert_text((72, 100), "Visible heading of the letter")
    if change:
        change(doc)
    doc.save(path)
    doc.close()
    return path


def is_metadata_left(path):
    doc = pymupdf.open(path)
    try:
        return metadata_left(doc)
    finally:
        doc.close()


def custom_properties(doc):
    """Document properties with none of the standard keys (title, author...), as some applications write them."""
    info = doc.get_new_xref()
    doc.update_object(info, f"<< /Company {pymupdf.get_pdf_str(SECRET)} >>")
    doc.xref_set_key(-1, "Info", f"{info} 0 R")


def test_custom_document_properties_are_removed(tmp_path):
    src = one_page(tmp_path / "custom.pdf", custom_properties)
    assert is_metadata_left(src)                         # the check sees them
    out, r, _ = redacted(src)
    assert r.fails == [], r.fails
    assert "removed the document properties" in r.notes and SECRET.lower() not in everywhere(out).lower()


def jpeg_with_metadata():
    """A small JPEG picture whose EXIF data and comment hold SECRET, as a photo holds who took it and where."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
    pix.set_rect(pix.irect, (200, 120, 40))
    jpeg = pix.tobytes("jpeg")

    def segment(marker, body):
        return bytes([0xFF, marker]) + (len(body) + 2).to_bytes(2, "big") + body
    exif = segment(0xE1, b"Exif" + bytes(2) + f"Artist={SECRET}".encode())
    comment = segment(0xFE, f"{SECRET} took this picture".encode())
    return jpeg[:2] + exif + comment + jpeg[2:]


def test_picture_metadata_is_removed(tmp_path):
    jpeg = jpeg_with_metadata()
    src = one_page(tmp_path / "photo.pdf", lambda doc: doc[0].insert_image(pymupdf.Rect(72, 200, 200, 328),
                                                                           stream=jpeg))
    assert is_metadata_left(src)                         # the check sees it
    doc = pymupdf.open(src)
    before = pymupdf.Pixmap(doc, doc[0].get_images()[0][0]).samples
    doc.close()
    out, r, _ = redacted(src)
    assert r.fails == [] and "removed the metadata of 1 picture(s)" in r.notes, (r.fails, r.notes)
    assert SECRET.encode() not in out.read_bytes() and SECRET.lower() not in everywhere(out).lower()
    doc = pymupdf.open(out)
    [image] = doc[0].get_images()
    assert doc.xref_get_key(image[0], "Filter")[1] == "/DCTDecode"           # still the same JPEG picture
    assert pymupdf.Pixmap(doc, image[0]).samples == before
    doc.close()


def chained_scripts(doc):
    """Two links that go to the page, then run a script (an action's /Next): one written in the link, one apart."""
    page = doc[0]
    for y in (90, 120):
        page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, y, 250, y + 15), "page": 0})
    first, second = [x for x, kind, _ in page.annot_xrefs() if kind == pymupdf.PDF_ANNOT_LINK]
    script = f"<< /S /JavaScript /JS {pymupdf.get_pdf_str(SECRET)} >>"
    go = f"/S /GoTo /D [{page.xref} 0 R /Fit]"
    doc.xref_set_key(first, "A", f"<< {go} /Next {script} >>")
    action = doc.get_new_xref()
    doc.update_object(action, f"<< {go} /Next [{script}] >>")
    doc.xref_set_key(second, "A", f"{action} 0 R")


def test_scripts_run_after_another_action_are_removed(tmp_path):
    out, r, _ = redacted(one_page(tmp_path / "links.pdf", chained_scripts))
    assert r.fails == [] and "removed scripts and automatic actions" in r.notes, (r.fails, r.notes)
    assert SECRET.lower() not in everywhere(out).lower()
    doc = pymupdf.open(out)
    assert len(doc[0].get_links()) == 2                  # the links within the document stay
    doc.close()


def unseen_fields(doc):
    """A form field on no page and a hidden one, both holding SECRET, next to a field shown on the page."""
    page = doc[0]
    for name, value, y in (("shown", "a visible value", 500), ("hidden", SECRET, 540)):
        field = pymupdf.Widget()
        field.field_type, field.field_name, field.field_value = pymupdf.PDF_WIDGET_TYPE_TEXT, name, value
        field.rect = pymupdf.Rect(72, y, 300, y + 20)
        page.add_widget(field)
    hidden = next(w for w in page.widgets() if w.field_name == "hidden")
    doc.xref_set_key(hidden.xref, "F", str(pymupdf.PDF_ANNOT_IS_HIDDEN))
    orphan = doc.get_new_xref()
    doc.update_object(orphan, f"<< /FT /Tx /T (old) /V {pymupdf.get_pdf_str(SECRET)} >>")
    fields = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")[1]
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", f"{fields[:-1]} {orphan} 0 R]")


def test_form_fields_no_page_shows_lose_their_content(tmp_path):
    out, r, _ = redacted(one_page(tmp_path / "form.pdf", unseen_fields))
    assert r.fails == [], r.fails
    assert "removed the values of 2 form field(s) that no page shows" in r.notes, r.notes
    assert SECRET.lower() not in everywhere(out).lower()
    doc = pymupdf.open(out)
    assert {w.field_name: w.field_value for w in doc[0].widgets()}["shown"] == "a visible value"
    doc.close()


def test_the_checks_catch_a_list_item_in_a_field_no_page_shows(tmp_path, monkeypatch):
    monkeypatch.setattr(redaction, "strip_document", lambda doc: [])
    _, r, _ = redacted(one_page(tmp_path / "form.pdf", unseen_fields), items=[SECRET])
    assert ("hidden-place", "the PDF's hidden data (/V)") in {(f.code, f.name) for f in r.fails}, r.fails


def clipped_text(doc):
    """SECRET drawn on the page but cut off by a clipping path: no one sees it, yet most text tools read it."""
    xref = doc[0].get_contents()[0]
    hidden = f" q 0 0 1 1 re W n BT /helv 12 Tf 72 500 Td ({SECRET} cut off) Tj ET Q"
    doc.update_stream(xref, doc.xref_stream(xref) + hidden.encode())


def test_a_list_item_cut_off_by_a_clipping_path_fails_the_check(tmp_path):
    """Search sees only what is shown, so the item isn't boxed; it stays in the file: the check must fail."""
    src = one_page(tmp_path / "clipped.pdf", clipped_text)
    _, r, matches = redacted(src, items=[SECRET])
    assert matches == [] and [(f.code, f.page, f.text) for f in r.fails] == [("hidden-text", 1, SECRET)], r.fails
    _, r, _ = redacted(src)                              # not on the list: it stays, as text of another colour does
    assert r.fails == [], r.fails
