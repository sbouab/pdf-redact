"""The app's model of an open document and the edits made to it (no Qt). Run from the repository folder:
uv run pytest"""
import pymupdf
import pytest

from helpers import LIST, encrypted, sample
from pdfredact import PasswordError, UserError
from pdfredact.document import file_hash
from pdfredact.geometry import inside
from pdfredact.gui.session import Box, Cancelled, Session
from pdfredact.review import mark
from pdfredact.text import Term


def open_sample(tmp_path, name, **kw):
    path = sample(tmp_path, name)
    return Session(str(path), path.read_bytes(), **kw)


def where(path, text, pno=0):
    """Where `text` is drawn on a page of the PDF at `path`."""
    doc = pymupdf.open(path)
    rect = doc[pno].search_for(text)[0]
    doc.close()
    return rect


def test_counts_boxes_and_not_found(tmp_path):
    s = open_sample(tmp_path, "letter_fr.pdf")
    r = s.add_items(LIST + ["Jean Personne"])
    assert r.added == LIST + ["Jean Personne"] and not r.duplicates and not r.too_short
    assert s.found("Côté-Tremblay") == 3 and s.found("Tremblay") == 3 and s.found("Jean Personne") == 0
    # one box per line of every match; a box inside a bigger one (Tremblay in Côté-Tremblay) is dropped
    assert len(s.boxes_on(0)) == 7 and len(s.boxes_on(1)) == 5
    for box in s.boxes_on(0):
        others = [o for o in s.boxes_on(0) if o != box]
        assert not any(inside(pymupdf.Rect(box.rect), pymupdf.Rect(o.rect), 0.1) for o in others)
    assert s.removal_count() == 12 and s.has_work()
    assert [m.page for m in s.occurrences("Côté-Tremblay")] == [0, 0, 1]
    s.close()


def test_add_refuses_duplicates_and_too_short(tmp_path):
    s = open_sample(tmp_path, "letter_fr.pdf")
    s.add_items(["Côté-Tremblay"])
    gen = s.generation
    r = s.add_items(["cote-tremblay", "A", "  ", "Côté-Tremblay"])
    assert r.added == [] and r.duplicates == ["cote-tremblay", "Côté-Tremblay"] and r.too_short == ["A"]
    assert s.generation == gen and s.items == ["Côté-Tremblay"]
    r = s.add_items(["Paul", " paul "])
    assert r.added == ["Paul"] and r.duplicates == ["paul"] and s.generation == gen + 1
    s.close()


def test_rename_item(tmp_path):
    s = open_sample(tmp_path, "letter_fr.pdf")
    s.add_items(["Geneviève", "H2X 1Y4"])
    assert s.rename_item("Geneviève", "Geneviève Côté-Tremblay").added == ["Geneviève Côté-Tremblay"]
    # page 2 has "Geneviève" and "Côté-Tremblay" on two lines in a row: found there too
    assert s.items == ["Geneviève Côté-Tremblay", "H2X 1Y4"] and s.found("Geneviève Côté-Tremblay") == 2
    assert s.rename_item("Geneviève Côté-Tremblay", "h2x1y4").duplicates == ["h2x1y4"]
    assert s.rename_item("H2X 1Y4", "h2x 1y4").added == ["h2x 1y4"]     # same item, other spelling: fine
    assert s.items == ["Geneviève Côté-Tremblay", "h2x 1y4"]
    s.close()


def test_keep_and_drawn_boxes_change_job_boxes(tmp_path):
    s = open_sample(tmp_path, "letter_fr.pdf")
    s.add_items(["Marc-André Bélanger"])
    [box] = s.boxes_on(1)
    assert box.kind == "text" and s.job_boxes() == {1: [(box.rect, "text")]}
    gen = s.generation
    s.set_kept(box, True)
    assert s.generation == gen + 1 and s.kept_count("Marc-André Bélanger") == 1 and s.job_boxes() == {}
    drawn = Box(0, (50.0, 50.0, 150.0, 80.0), "user")
    s.add_drawn(drawn)
    assert s.job_boxes() == {0: [((50.0, 50.0, 150.0, 80.0), "user")]} and s.removal_count() == 1
    assert s.drawn_on(0) == [drawn] and s.drawn_on(1) == []
    before = s.snapshot()
    s.remove_item("Marc-André Bélanger")
    assert s.boxes_on(1) == [] and s.kept == set()
    s.restore(before)                                           # undo: the item comes back with its kept spot
    assert s.kept_count("Marc-André Bélanger") == 1
    s.remove_drawn(drawn)
    s.set_kept(box, False)
    assert s.job_boxes() == {1: [(box.rect, "text")]} and s.generation == gen + 6
    s.close()


def test_text_in_rect_and_count_matches(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    s = Session(str(path), path.read_bytes())
    text, boxes = s.text_in(0, where(path, "514 555-0199"))
    assert text == "514 555-0199" and len(boxes) == 11
    two_lines = where(path, "1234, rue des Érables") | where(path, "H2X 1Y4")
    assert s.text_in(0, two_lines)[0] == "1234, rue des Érables Montréal (Québec) H2X 1Y4"
    assert s.text_in(0, pymupdf.Rect(500, 700, 520, 720)) == ("", [])
    assert s.count_matches("514 555-0199") == 1 and s.count_matches("Côté-Tremblay") == 3
    assert s.count_matches("A") == 0
    s.close()


def test_box_at_prefers_drawn(tmp_path):
    s = open_sample(tmp_path, "letter_fr.pdf")
    s.add_items(["Marc-André Bélanger"])
    [box] = s.boxes_on(1)
    r = pymupdf.Rect(box.rect)
    assert s.box_at(1, (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) == box
    drawn = Box(1, (r.x0 - 5, r.y0 - 5, r.x1 + 5, r.y1 + 5), "user")
    s.add_drawn(drawn)
    assert s.box_at(1, (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) == drawn
    assert s.box_at(1, 5, 5) is None and s.box_at(0, (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) is None
    s.close()


def test_password_pdf(tmp_path):
    path = encrypted(tmp_path)
    with pytest.raises(PasswordError):
        Session(str(path), path.read_bytes())
    with pytest.raises(PasswordError):
        Session(str(path), path.read_bytes(), password="wrong")
    s = Session(str(path), path.read_bytes(), password="s3cret")
    assert s.page_count == 1 and s.add_items(["046 454 286"]).added and s.found("046 454 286") == 1
    s.close()


def test_review_copy_is_refused(tmp_path):
    src = sample(tmp_path, "t4_slip.pdf")
    review = tmp_path / "t4_slip - REVIEW (not redacted).pdf"
    mark(str(src), [Term(x) for x in LIST], str(review), file_hash(src))
    with pytest.raises(UserError, match="review copy"):
        Session(str(review), review.read_bytes())


def test_not_a_pdf_is_refused(tmp_path):
    with pytest.raises(UserError):
        Session(str(tmp_path / "notes.pdf"), b"just text")


def test_problems(tmp_path):
    scan = open_sample(tmp_path, "scan_no_text.pdf")
    assert len(scan.problems) == 1 and scan.problems[0][0] == 0
    assert scan.problems[0][1].startswith("has no text, only a picture")
    scan.close()


def test_progress_and_cancel(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    calls = []
    Session(str(path), path.read_bytes(), progress=lambda done, total: calls.append((done, total))).close()
    assert calls == [(1, 2), (2, 2)]

    def stop(done, total):
        raise Cancelled()
    with pytest.raises(Cancelled):
        Session(str(path), path.read_bytes(), progress=stop)


def test_a_kept_spot_goes_with_its_item(tmp_path):
    """Removing an item forgets its spots kept visible: typed again later, it is blacked out everywhere."""
    s = open_sample(tmp_path, "letter_fr.pdf")
    s.add_items(["Marc-André Bélanger"])
    [box] = s.boxes_on(1)
    s.set_kept(box, True)
    s.remove_item("Marc-André Bélanger")
    assert s.kept == set()
    s.add_items(["marc-andré  bélanger"])
    assert s.kept_count("marc-andré  bélanger") == 0 and s.job_boxes() == {1: [(box.rect, "text")]}
    s.close()


def test_kept_matches_are_those_in_a_kept_spot(tmp_path):
    s = open_sample(tmp_path, "letter_fr.pdf")
    s.add_items(["Geneviève Côté-Tremblay", "Côté-Tremblay"])
    two_lines = next(m for m in s.occurrences("Geneviève Côté-Tremblay") if len(m.rects) == 2)
    assert len(s.boxes_of(two_lines)) == 2                      # one box per line it spans
    second_line = next(b for b in s.boxes_of(two_lines) if s.text_in(1, pymupdf.Rect(b.rect))[0] == "Côté-Tremblay")
    s.set_kept(second_line, True)
    # the spot kept holds the second line of the long item, and the short item inside it
    assert {(m.page, len(m.rects)) for m in s.kept_matches()} == {(1, 2), (1, 1)}
    s.close()
