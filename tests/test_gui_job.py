"""The redaction job the app runs in a helper process: same code as the command line, boxes from the app.
Run from the repository folder:  uv run pytest"""
import dataclasses
import multiprocessing
from pathlib import Path

import pymupdf

from helpers import LIST, dark_share, encrypted, page_text, sample, scan_name_rect
from pdfredact.document import file_hash
from pdfredact.gui.job import child_main, run_job
from pdfredact.gui.session import Box, Session


def letter_session(tmp_path):
    path = sample(tmp_path, "letter_fr.pdf")
    s = Session(str(path), path.read_bytes())
    s.add_items(LIST)
    return path, s


def test_job_passes_and_matches_the_session(tmp_path):
    path, s = letter_session(tmp_path)
    spouse = next(b for b in s.boxes_on(1) if s.text_in(1, pymupdf.Rect(b.rect))[0] == "Marc-André Bélanger")
    s.set_kept(spouse, True)
    out = tmp_path / "out.pdf"
    calls = []
    outcome = run_job(s.job_request(str(out)), lambda *a: calls.append(a))
    s.close()
    assert outcome.error == "" and outcome.result.fails == []
    assert [(w.code, w.page, w.text) for w in outcome.result.warns] == [("kept-visible", 2, "Marc-André Bélanger")]
    assert outcome.sha256 == file_hash(out)
    assert [c[0] for c in calls] == ["redact", "redact", "check", "check"]       # the session already searched
    assert calls[-1] == ("check", 2, 2)
    text = page_text(out)
    assert "Marc-André Bélanger" in text and "Côté-Tremblay" not in text and "Nous avons examiné" in text


def test_job_refuses_a_changed_original(tmp_path):
    path, s = letter_session(tmp_path)
    path.write_bytes(path.read_bytes() + b"\n% edited\n")
    out = tmp_path / "out.pdf"
    outcome = run_job(s.job_request(str(out)))
    s.close()
    assert outcome.result is None and "changed since you opened it" in outcome.error
    assert not out.exists()


def test_job_reports_a_missing_original(tmp_path):
    path, s = letter_session(tmp_path)
    path.unlink()
    outcome = run_job(s.job_request(str(tmp_path / "out.pdf")))
    s.close()
    assert outcome.result is None and "can't read the original file" in outcome.error


def test_job_with_password(tmp_path):
    path = encrypted(tmp_path)
    s = Session(str(path), path.read_bytes(), password="s3cret")
    s.add_items(["046 454 286", "Tremblay"])
    out = tmp_path / "out.pdf"
    outcome = run_job(s.job_request(str(out)))
    s.close()
    assert outcome.error == "" and outcome.result.fails == []
    doc = pymupdf.open(out)
    assert not doc.needs_pass and "TREMBLAY" not in doc[0].get_text() and "Employer" in doc[0].get_text()
    doc.close()


def test_drawn_box_blacks_out_a_scan(tmp_path):
    path = sample(tmp_path, "scan_no_text.pdf")
    s = Session(str(path), path.read_bytes())
    name = scan_name_rect()
    s.add_drawn(Box.around(0, name + (-2, -2, 2, 2), "user"))
    out = tmp_path / "out.pdf"
    outcome = run_job(s.job_request(str(out)))
    s.close()
    assert outcome.error == "" and outcome.result.fails == [] and outcome.result.areas == 1
    assert dark_share(out, name) > 0.9


def test_helper_process_sends_progress_then_the_outcome(tmp_path):
    path, s = letter_session(tmp_path)
    request = s.job_request(str(tmp_path / "out.pdf"))
    s.close()
    ctx = multiprocessing.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    helper = ctx.Process(target=child_main, args=(writer, request), daemon=True)
    helper.start()
    writer.close()
    messages = []
    while True:
        try:
            messages.append(reader.recv())
        except EOFError:
            break
    helper.join(30)
    assert helper.exitcode == 0
    assert messages[0][0] == "progress" and messages[-1][0] == "done"
    outcome = messages[-1][1]
    assert outcome.error == "" and outcome.result.fails == [] and outcome.blocked == []


def test_an_item_left_out_of_the_boxes_fails_the_check(tmp_path):
    """Only a spot kept visible may still show a list item. One left out of the boxes any other way (a bug between
    the window and the job) must fail the check, not pass as if it had been kept."""
    path, s = letter_session(tmp_path)
    spouse = next(b for b in s.boxes_on(1) if s.text_in(1, pymupdf.Rect(b.rect))[0] == "Marc-André Bélanger")
    request = s.job_request(str(tmp_path / "out.pdf"))
    s.close()
    lost = {**request.boxes, 1: [(rect, kind) for rect, kind in request.boxes[1] if rect != spouse.rect]}
    outcome = run_job(dataclasses.replace(request, boxes=lost))
    assert [(f.code, f.page, f.text) for f in outcome.result.fails] == [("not-boxed", 2, "Marc-André Bélanger")]


def test_a_busy_output_does_not_hide_why_the_job_stopped(tmp_path, monkeypatch):
    """Deleting a half-written output can fail on Windows (an antivirus scan): the reason must still come back."""
    path, s = letter_session(tmp_path)
    path.write_bytes(path.read_bytes() + b"\n% edited\n")

    def refuse(self, missing_ok=False):
        raise PermissionError(13, "The process cannot access the file")
    monkeypatch.setattr(Path, "unlink", refuse)
    outcome = run_job(s.job_request(str(tmp_path / "out.pdf")))
    monkeypatch.undo()
    s.close()
    assert outcome.result is None and "changed since you opened it" in outcome.error
