"""End-to-end tests on fake documents: mark, a simulated review, apply, and checks that don't rely on the
tool's own code. Run from the repository folder:  python -m pytest"""
import hashlib
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))
import make_samples as S                         # noqa: E402
from pdfredact.text import Term, normalize       # noqa: E402

REVIEW, REDACTED, FAILED = " - REVIEW (not redacted).pdf", " - REDACTED.pdf", " - FAILED CHECK (do not share).pdf"


def run(*args):
    p = subprocess.run([sys.executable, str(ROOT / "redact.py"), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def squash(s):
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def everything(path):
    """All text a reader could get out of a PDF: pages, properties, XMP, bookmarks, comments, fields, links."""
    doc = pymupdf.open(path)
    parts = [p.get_text() for p in doc] + [str(doc.metadata), doc.get_xml_metadata() or "", str(doc.get_toc())]
    for p in doc:
        parts += [str(a.info) for a in p.annots()] + [str(w.field_value) for w in p.widgets()]
        parts += [ln.get("uri") or "" for ln in p.get_links()]
    doc.close()
    if shutil.which("pdftotext"):                # a second, unrelated text extractor
        parts.append(subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True).stdout)
    return squash(" ".join(parts))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dark_share(path, rect, pno=0):
    """Share of dark pixels inside rect (PDF coordinates) on the rendered page."""
    doc = pymupdf.open(path)
    pix = doc[pno].get_pixmap(dpi=72, clip=rect)
    n = pix.width * pix.height
    dark = sum(1 for i in range(n) if sum(pix.samples[i * pix.n:i * pix.n + 3]) < 150)
    doc.close()
    return dark / n


def boxes(review):
    doc = pymupdf.open(review)
    out = [(pno, a.info.get("content", "")) for pno, p in enumerate(doc) for a in p.annots()
           if a.type[0] == pymupdf.PDF_ANNOT_SQUARE]
    doc.close()
    return out


def edit_review(review, change):
    doc = pymupdf.open(review)
    change(doc)
    doc.save(str(review) + ".tmp")
    doc.close()
    Path(str(review) + ".tmp").replace(review)


def make(tmp_path, *names):
    out = {}
    for name in names:
        S.SAMPLES[name][0](str(tmp_path / name))
        out[name] = tmp_path / name
    lst = tmp_path / "list.txt"
    lst.write_text("\n".join(S.LIST) + "\n", encoding="utf-8")
    return out, lst


# ----------------------------------------------------------------------------- matching
@pytest.mark.parametrize("item, text, found", [
    ("046 454 286", "NAS : 046-454-286.", True),
    ("046 454 286", "SIN046454286", True),
    ("046 454 286", "0 4 6   4 5 4   2 8 6", True),
    ("046 454 286", "1046454286", False),
    ("046454286", "046 454 286", True),
    ("Côté-Tremblay", "MME COTE TREMBLAY", True),
    ("Côté-Tremblay", "Côté-Tremblays", False),
    ("G1R 4T5", "Québec G1R4T5", True),
    ("Paul", "Pauline", False),
    ("Paul", "Paul's file", True),
    ("o.whitfield@example.co.uk", "mail: O.Whitfield@Example.co.uk", True),
    ("Jean Tremblay", "Jean\nTremblay", True),
    ("123456789RP0001", "123456789 RP 0001", True),
])
def test_matching(item, text, found):
    assert bool(Term(item).find(normalize(text))) is found


def test_too_short_items_are_not_searched():
    assert Term("A").pattern is None and Term("-").pattern is None


# ----------------------------------------------------------------------------- the whole workflow
def test_mark_review_apply(tmp_path):
    files, lst = make(tmp_path, *S.SAMPLES)
    before = {name: sha(p) for name, p in files.items()}
    code, out = run("mark", tmp_path, "--list", lst)
    assert code == 0, out
    assert "page 1 has no text, only a picture" in out                       # scan_no_text
    assert "page 1 is a scan with a text layer" in out                       # scan_with_text_layer
    review = {name: tmp_path / (name[:-4] + REVIEW) for name in files}
    assert all(p.exists() for p in review.values())

    # the reviewer keeps the spouse's name visible, and boxes more by hand
    def keep_spouse(doc):
        page = doc[1]
        a = next(a for a in page.annots() if "Marc-André" in a.info.get("content", ""))
        page.delete_annot(a)
    edit_review(review["letter_fr.pdf"], keep_spouse)

    def box_employer(doc):
        doc[0].add_rect_annot(doc[0].search_for("Acme Widgets Inc.")[0])
    edit_review(review["t4_slip.pdf"], box_employer)

    def highlight_subject(doc):
        doc[0].add_highlight_annot(doc[0].search_for("Avis de cotisation 2025")[0])
    edit_review(review["letter_fr.pdf"], highlight_subject)

    src = pymupdf.open()                          # where the name is drawn in the scan (same layout as the sample)
    S.write_lines(src.new_page(), S.SCAN_LINES, size=12)
    name_rect = src[0].search_for("Geneviève Côté-Tremblay")[0]
    edit_review(review["scan_no_text.pdf"], lambda doc: doc[0].add_rect_annot(name_rect + (-2, -2, 2, 2)))

    code, out = run("apply", tmp_path, "--list", lst)
    assert code == 0, out
    assert out.count("[PASS]") == len(files), out
    assert "'Marc-André Bélanger' is still visible where you deleted its box" in out

    deliberate = {"letter_fr.pdf": ["Marc-André Bélanger"]}
    gone_too = {"t4_slip.pdf": ["Acme Widgets"], "letter_fr.pdf": ["Avis de cotisation 2025"]}
    for name in files:
        text = everything(tmp_path / (name[:-4] + REDACTED))
        for item in S.LIST:
            if item not in deliberate.get(name, []):
                assert squash(item) not in text, (name, item)
        for item in deliberate.get(name, []):
            assert squash(item) in text, (name, item)
        for item in gone_too.get(name, []):
            assert squash(item) not in text, (name, item)
        for item in S.SAMPLES[name][1]:
            if item not in gone_too.get(name, []):
                assert squash(item) in text, (name, item)

    # pictures: the pixels are blacked out, not just covered
    assert dark_share(tmp_path / ("scan_no_text" + REDACTED), name_rect) > 0.9
    assert dark_share(tmp_path / ("scan_with_text_layer" + REDACTED), name_rect) > 0.9
    assert dark_share(tmp_path / ("scan_with_text_layer" + REDACTED), src[0].search_for("Montant")[0]) < 0.5
    # form fields: list items replaced, other values untouched
    doc = pymupdf.open(tmp_path / ("form_fillable" + REDACTED))
    values = {w.field_name: w.field_value for w in doc[0].widgets()}
    assert values == {"name": "XXXXX", "sin": "XXXXX", "city": "Montréal"}
    # the originals are untouched
    assert {name: sha(p) for name, p in files.items()} == before


def test_apply_accepts_the_review_copy(tmp_path):
    files, lst = make(tmp_path, "t4_slip.pdf")
    assert run("mark", files["t4_slip.pdf"], "--list", lst)[0] == 0
    code, out = run("apply", tmp_path / ("t4_slip" + REVIEW), "--list", lst)
    assert code == 0 and (tmp_path / ("t4_slip" + REDACTED)).exists(), out


# ----------------------------------------------------------------------------- refusals and failures
def test_list_changed_after_mark_is_refused(tmp_path):
    files, lst = make(tmp_path, "t4_slip.pdf")
    assert run("mark", files["t4_slip.pdf"], "--list", lst)[0] == 0
    longer = tmp_path / "longer.txt"
    longer.write_text(lst.read_text(encoding="utf-8") + "Acme Widgets\n", encoding="utf-8")
    code, out = run("apply", files["t4_slip.pdf"], "--list", longer)
    assert code == 1 and "the list is not the one mark used" in out, out
    assert not (tmp_path / ("t4_slip" + REDACTED)).exists() and not (tmp_path / ("t4_slip" + FAILED)).exists()
    reordered = tmp_path / "reordered.txt"            # same items in another order: fine
    reordered.write_text("\n".join(reversed(S.LIST)), encoding="utf-8")
    assert run("apply", files["t4_slip.pdf"], "--list", reordered)[0] == 0


def test_unboxed_list_item_fails_the_check(tmp_path):
    """Safety net: if a list item is visible in the output and no box was ever offered there, the file fails."""
    import pdfredact.redaction as red
    from pdfredact.document import file_hash
    files, lst = make(tmp_path, "t4_slip.pdf")
    assert run("mark", files["t4_slip.pdf"], "--list", lst)[0] == 0
    terms = [Term(x) for x in S.LIST]
    real = red.find_matches
    red.find_matches = lambda doc, ts, problems=None: [m for m in real(doc, ts) if ts[m.term].text != "H2X 1Y4"]
    try:
        def drop_postal(doc):                       # as if mark had never boxed the postal code
            page = doc[0]
            for a in [a for a in page.annots() if "H2X 1Y4" in a.info.get("content", "")]:
                page.delete_annot(a)
        edit_review(tmp_path / ("t4_slip" + REVIEW), drop_postal)
        r = red.apply(str(files["t4_slip.pdf"]), str(tmp_path / ("t4_slip" + REVIEW)), terms,
                      file_hash(files["t4_slip.pdf"]), str(tmp_path / "out.pdf"))
    finally:
        red.find_matches = real
    assert any("'H2X 1Y4' is on the list but was not boxed" in f for f in r.fails), r.fails


def test_review_copy_that_lost_its_boxes_is_refused(tmp_path):
    files, lst = make(tmp_path, "letter_fr.pdf")
    assert run("mark", files["letter_fr.pdf"], "--list", lst)[0] == 0

    def drop_all(doc):
        for page in doc:
            for x in [a.xref for a in page.annots()]:
                page.delete_annot(page.load_annot(x))
    edit_review(tmp_path / ("letter_fr" + REVIEW), drop_all)
    code, out = run("apply", files["letter_fr.pdf"], "--list", lst)
    assert code == 1 and "no boxes left" in out, out
    assert not (tmp_path / ("letter_fr" + REDACTED)).exists()


def test_review_copy_rewritten_by_a_viewer_gets_strict_checks(tmp_path):
    """A viewer that drops the review copy's properties and annotation subjects: apply still works, but a
    deleted box can no longer be told from a miss, so any list item left visible fails."""
    files, lst = make(tmp_path, "letter_fr.pdf")
    assert run("mark", files["letter_fr.pdf"], "--list", lst)[0] == 0
    review = tmp_path / ("letter_fr" + REVIEW)

    def forget(doc):
        doc.set_metadata({"title": "Saved by some viewer", "keywords": ""})
        for page in doc:
            for a in page.annots():
                doc.xref_set_key(a.xref, "Subj", "null")
    edit_review(review, forget)
    code, out = run("apply", files["letter_fr.pdf"], "--list", lst)
    assert code == 0 and "no longer records how it was made" in out, out
    run("mark", files["letter_fr.pdf"], "--list", lst, "--overwrite")

    def forget_and_keep_spouse(doc):
        forget(doc)
        page = doc[1]
        page.delete_annot(next(a for a in page.annots() if "Marc-André" in a.info.get("content", "")))
    edit_review(review, forget_and_keep_spouse)
    code, out = run("apply", files["letter_fr.pdf"], "--list", lst)
    assert code == 1 and "'Marc-André Bélanger' is on the list but was not boxed" in out, out


def test_changed_original_is_refused(tmp_path):
    files, lst = make(tmp_path, "t4_slip.pdf")
    assert run("mark", files["t4_slip.pdf"], "--list", lst)[0] == 0
    doc = pymupdf.open(files["t4_slip.pdf"])
    doc.set_metadata({"title": "changed"})
    doc.save(str(files["t4_slip.pdf"]) + ".tmp")
    doc.close()
    Path(str(files["t4_slip.pdf"]) + ".tmp").replace(files["t4_slip.pdf"])
    code, out = run("apply", files["t4_slip.pdf"], "--list", lst)
    assert code == 1 and "original changed" in out, out


def test_existing_review_copy_is_not_overwritten(tmp_path):
    files, lst = make(tmp_path, "t4_slip.pdf")
    assert run("mark", files["t4_slip.pdf"], "--list", lst)[0] == 0
    code, out = run("mark", files["t4_slip.pdf"], "--list", lst)
    assert code == 1 and "already exists" in out
    assert run("mark", files["t4_slip.pdf"], "--list", lst, "--overwrite")[0] == 0


def test_list_must_be_plain_text(tmp_path):
    files, _ = make(tmp_path, "t4_slip.pdf")
    rtf = tmp_path / "list.rtf"
    rtf.write_bytes(b"{\\rtf1\\ansi Tremblay}")
    code, out = run("mark", files["t4_slip.pdf"], "--list", rtf)
    assert code == 2 and "Make Plain Text" in out
    empty = tmp_path / "empty.txt"
    empty.write_text("\n\n", encoding="utf-8")
    assert run("mark", files["t4_slip.pdf"], "--list", empty)[0] == 2


def test_unreadable_pages_and_xfa_are_reported(tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text("Tremblay\n", encoding="utf-8")
    lines = ["AVIS DE COTISATION 2025", "Nom : Geneviève Côté-Tremblay", "Adresse : 1234, rue des Érables",
             "Revenu total : 64 210,00 $", "Nous avons examiné votre déclaration de revenus.",
             "Veuillez agréer nos salutations distinguées."]
    # text that extracts as gibberish (every character mapped to the wrong one)
    doc = pymupdf.open()
    S.write_lines(doc.new_page(), lines)
    font = doc[0].get_fonts()[0][0]
    cmap = "\n".join(f"<{c:02X}> <{(c - 32 + 37) % 95 + 32:04X}>" for c in range(32, 127))
    tounicode = doc.get_new_xref()
    doc.update_object(tounicode, "<<>>")
    doc.update_stream(tounicode, ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                                  "1 begincodespacerange <00> <FF> endcodespacerange\n"
                                  f"95 beginbfchar\n{cmap}\nendbfchar\nendcmap CMapName currentdict /CMap "
                                  "defineresource pop end end").encode())
    doc.xref_set_key(font, "ToUnicode", f"{tounicode} 0 R")
    doc.save(tmp_path / "garbled.pdf")
    # text drawn as shapes
    doc = pymupdf.open()
    S.write_lines(doc.new_page(), lines)
    svg = doc[0].get_svg_image(text_as_path=True)
    pymupdf.open("pdf", pymupdf.open("svg", svg.encode()).convert_to_pdf()).save(tmp_path / "shapes.pdf")
    # a dynamic XFA form: the page only says "Please wait..."
    doc = pymupdf.open()
    doc.new_page().insert_text((60, 90), "Please wait...")
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'><xfa:datasets><xfa:data><nom>Tremblay"
                         b"</nom></xfa:data></xfa:datasets></xdp:xdp>")
    af = doc.get_new_xref()
    doc.update_object(af, f"<< /Fields [] /XFA {x} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{af} 0 R")
    doc.save(tmp_path / "xfa.pdf")

    code, out = run("mark", tmp_path, "--list", lst)
    assert "garbled.pdf" in out and "can't be read properly" in out, out
    assert "shapes.pdf" in out and "drawn as shapes" in out, out
    assert "xfa.pdf: SKIPPED" in out and "XFA form" in out, out
    assert code == 1


def test_network_is_blocked():
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from pdfredact import offline\n"
            "offline.block_network()\n"
            "import socket, urllib.request\n"
            "for attempt in (lambda: socket.create_connection(('127.0.0.1', 9), timeout=2),\n"
            "                lambda: urllib.request.urlopen('http://example.com', timeout=2)):\n"
            "    try:\n"
            "        attempt(); print('CONNECTED')\n"
            "    except OSError as e:\n"
            "        print('blocked')\n"
            "print(sorted(set(offline.BLOCKED)))\n") % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
    assert "CONNECTED" not in out and out.count("blocked") == 2, out
