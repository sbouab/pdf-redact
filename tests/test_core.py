"""Tests of the core building blocks the app relies on: check results as data, opening PDFs from memory,
searching page by page, and redacting with boxes given directly. Run from the repository folder:  uv run pytest"""
import pymupdf
import pytest

from helpers import LIST, encrypted, sample, with_attachment
from pdfredact import Finding, PasswordError, UserError
from pdfredact.document import document_warnings, file_hash, open_pdf
from pdfredact.redaction import apply, redact
from pdfredact.review import boxes_from, find_matches, mark, page_matches, scan_page
from pdfredact.text import Term

TERMS = [Term(x) for x in LIST]


# ----------------------------------------------------------------------------- findings
def test_finding_prints_its_message():
    f = Finding("kept-visible", "page 2: 'Paul' is still visible where you deleted its box", page=2, text="Paul")
    assert str(f) == "page 2: 'Paul' is still visible where you deleted its box"
    assert f"x {f}" == "x page 2: 'Paul' is still visible where you deleted its box"
    same = Finding("kept-visible", "page 2: 'Paul' is still visible where you deleted its box", page=2, text="Paul")
    assert list(dict.fromkeys([f, same])) == [f]


def test_document_warnings_have_codes(tmp_path):
    path = sample(tmp_path, "t4_slip.pdf")
    with_attachment(path)
    doc = pymupdf.open(path)
    warnings = document_warnings(doc)
    doc.close()
    assert [w.code for w in warnings] == ["attached-files"]
    assert warnings[0].count == 1
    assert str(warnings[0]) == ("1 attached file(s) inside the PDF can't be checked: apply fails unless you add "
                                "--drop-attachments")


def test_check_failures_have_codes(tmp_path):
    path = sample(tmp_path, "t4_slip.pdf")
    with_attachment(path)
    review, out = tmp_path / "review.pdf", tmp_path / "out.pdf"
    mark(str(path), TERMS, str(review), file_hash(path))
    r = apply(str(path), str(review), TERMS, file_hash(path), str(out))
    assert [(f.code, f.page, f.count) for f in r.fails] == [("attachments", None, 1)]
    assert str(r.fails[0]) == "files attached inside the PDF were not checked (add --drop-attachments to remove them)"


# ----------------------------------------------------------------------------- opening
def test_open_pdf_from_bytes(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    doc = open_pdf("letter.pdf", data=path.read_bytes())
    assert len(doc) == 2 and "Côté-Tremblay" in doc[0].get_text()
    doc.close()
    with pytest.raises(UserError):
        open_pdf("notes.pdf", data=b"just some text, not a PDF")


def test_open_pdf_password(tmp_path):
    data = encrypted(tmp_path).read_bytes()
    with pytest.raises(PasswordError):
        open_pdf("locked.pdf", data=data)
    with pytest.raises(PasswordError):
        open_pdf("locked.pdf", "wrong", data=data)
    doc = open_pdf("locked.pdf", "s3cret", data=data)
    assert "Statement of Remuneration Paid" in doc[0].get_text()
    doc.close()


def test_open_pdf_closes_the_document_when_it_fails(tmp_path, monkeypatch):
    """An error after the file is opened (here, showing its layers) must not leave the document open."""
    from pdfredact import document
    path = sample(tmp_path, "letter_fr.pdf")
    opened, real_open = [], pymupdf.open

    def record(*args, **kwargs):
        opened.append(real_open(*args, **kwargs))
        return opened[-1]

    def broken(doc):
        raise RuntimeError("broken layers")
    monkeypatch.setattr(pymupdf, "open", record)
    monkeypatch.setattr(document, "show_all_layers", broken)
    with pytest.raises(RuntimeError):
        open_pdf(str(path))
    assert len(opened) == 1 and opened[0].is_closed


# ----------------------------------------------------------------------------- searching page by page
def test_a_page_is_read_once_and_searched_again(tmp_path):
    doc = pymupdf.open(sample(tmp_path, "letter_fr.pdf"))
    scan = scan_page(doc[0])
    doc.close()                                  # the scan no longer needs the document
    found = page_matches(0, scan, [Term("Côté-Tremblay")])
    assert [(m.page, m.term, m.kind) for m in found] == [(0, 0, ""), (0, 0, "")]
    assert len(page_matches(0, scan, [Term("514 555-0199"), Term("Paul")])) == 1


def test_page_matches_finds_form_field_values(tmp_path):
    doc = pymupdf.open(sample(tmp_path, "form_fillable.pdf"))
    found = page_matches(0, scan_page(doc[0]), [Term("046 454 286"), Term("Montréal")])
    doc.close()
    # (the values also show in the page text, through the fields' appearance)
    assert sorted((m.term, m.field) for m in found if m.kind == "field") == [(0, "sin"), (1, "city")]


def test_text_on_a_scan_is_marked_as_lying_on_a_picture(tmp_path):
    doc = pymupdf.open(sample(tmp_path, "scan_with_text_layer.pdf"))
    found = page_matches(0, scan_page(doc[0]), [Term("046 454 286")])
    doc.close()
    assert [m.kind for m in found] == ["image"]


# ----------------------------------------------------------------------------- redacting with boxes given directly
def boxes_without(matches, item):
    """The boxes mark would offer, minus those of one list item (as if the reviewer kept it visible)."""
    return {pno: [(r, kind or "text") for r, kind, ti in items if TERMS[ti].text != item]
            for pno, items in boxes_from(matches).items()}


def test_redact_with_boxes_given_directly(tmp_path):
    path, out = sample(tmp_path, "letter_fr.pdf"), tmp_path / "out.pdf"
    doc = open_pdf(str(path))
    matches = find_matches(doc, TERMS)
    r = redact(doc, boxes_without(matches, "Marc-André Bélanger"), TERMS, str(out), marked=matches)
    doc.close()
    assert r.fails == []
    assert [(w.code, w.page, w.text) for w in r.warns] == [("kept-visible", 2, "Marc-André Bélanger")]
    result = pymupdf.open(out)
    text = " ".join(p.get_text() for p in result)
    result.close()
    assert "Marc-André Bélanger" in text
    assert "Côté-Tremblay" not in text and "046-454-286" not in text and "Nous avons examiné" in text


def test_redact_without_marked_boxes_fails_on_a_visible_item(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    doc = open_pdf(str(path))
    boxes = boxes_without(find_matches(doc, TERMS), "Marc-André Bélanger")
    r = redact(doc, boxes, TERMS, str(tmp_path / "out.pdf"))
    doc.close()
    assert [(f.code, f.page, f.text) for f in r.fails] == [("not-boxed", 2, "Marc-André Bélanger")]


def test_redact_reports_progress(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    calls = []
    doc = open_pdf(str(path))
    matches = find_matches(doc, TERMS)
    redact(doc, boxes_without(matches, ""), TERMS, str(tmp_path / "out.pdf"), marked=matches,
           progress=lambda *a: calls.append(a))
    doc.close()
    assert calls == [("redact", 1, 2), ("redact", 2, 2), ("check", 1, 2), ("check", 2, 2)]


def test_text_removed_elsewhere_is_given_whole(tmp_path):
    """A form field's value is removed whole, and reported whole: the app offers to add it to the list, where cut
    to 40 characters it would match only part of the text, or nothing. The command line still shows it cut."""
    value = "Apartment 12, 1234 Maple Street West, Springfield"
    src, where = tmp_path / "form.pdf", pymupdf.Rect(72, 150, 420, 172)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), value)                  # also printed on the page, where no box covers it
    field = pymupdf.Widget()
    field.field_type, field.field_name, field.field_value = pymupdf.PDF_WIDGET_TYPE_TEXT, "address", value
    field.rect = where
    page.add_widget(field)
    doc.save(src)
    doc.close()
    doc = open_pdf(str(src))
    r = redact(doc, {0: [(where, "field")]}, [], str(tmp_path / "out.pdf"))
    doc.close()
    assert [(w.code, w.page, w.text) for w in r.warns] == [("removed-elsewhere", 1, value)]
    assert repr(value[:40]) in str(r.warns[0]) and value not in str(r.warns[0])
