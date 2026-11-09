"""How the app words findings, warnings and progress, and the fix it offers. Run:  uv run pytest"""
import inspect
import re

import pytest

from pdfredact import Finding, document, verify
from pdfredact.gui.wording import Fix, finding_fixes, finding_text, problem_text, stage_text, summary, warning_text
from pdfredact.redaction import Result

CHECK_CODES = ["page-count", "text-lost", "text-left", "text-moved", "not-boxed", "hidden-text", "shapes-left",
               "fields-changed", "comments-changed", "field-not-boxed", "links-changed", "hidden-place",
               "attachments", "xfa-left", "invisible-text", "outside-text", "metadata-left",
               "kept-visible", "removed-elsewhere", "field-kept"]
DOCUMENT_CODES = ["xfa-form", "attached-files", "signed"]
COMMAND_LINE = ["--", "review copy", "mark", "apply", "deleted its box"]


def some(code):
    return Finding(code, "command-line message", page=2, text="Paul Tremblay", name="sin", count=3)


def codes_in(module):
    return set(re.findall(r'Finding\("([a-z-]+)"', inspect.getsource(module)))


def test_every_code_is_worded_for_the_app():
    """A new check or warning can't reach the window in command-line words."""
    assert codes_in(verify) == set(CHECK_CODES)
    assert codes_in(document) == set(DOCUMENT_CODES)


@pytest.mark.parametrize("code", CHECK_CODES)
def test_every_check_finding_reads_well_in_the_app(code):
    text = finding_text(some(code))
    assert text[0].isupper() and text.endswith(".") and "command-line message" not in text.lower()
    assert not any(word in text for word in COMMAND_LINE), text


@pytest.mark.parametrize("code", DOCUMENT_CODES)
def test_every_document_warning_reads_well_in_the_app(code):
    text = warning_text(Finding(code, "command-line message", count=2))
    assert text[0].isupper() and text.endswith(".") and "command-line message" not in text.lower()
    assert not any(word in text for word in COMMAND_LINE), text


def test_wording_from_the_mockups():
    assert finding_text(Finding("kept-visible", "", page=1, text="Geneviève")) == \
        "Page 1: “Geneviève” is still visible where you kept it."
    assert finding_text(Finding("attachments", "", count=1)) == \
        "This PDF carries 1 attached file. Attached files can't be checked, so they must go."
    assert finding_text(Finding("attachments", "", count=2)).startswith("This PDF carries 2 attached files.")


def test_fixes():
    assert finding_fixes(Finding("attachments", "", count=1)) == \
        [Fix("drop-attachments", "Remove attached files and redo")]
    assert finding_fixes(Finding("removed-elsewhere", "", page=4, text="Acme Widgets")) == \
        [Fix("add-item", "Add to list", text="Acme Widgets"), Fix("page", "Show", page=4)]
    assert finding_fixes(Finding("kept-visible", "", page=1, text="Paul")) == [Fix("page", "Show", page=1)]
    assert finding_fixes(Finding("not-boxed", "", page=3, text="Paul")) == [Fix("page", "Go to page 3", page=3)]
    assert finding_fixes(Finding("links-changed", "")) == []


def test_problem_text():
    assert problem_text(2, "has no text, only a picture (a scan?): nothing on it can be found, so box it by hand") == \
        "Page 2 has no text, only a picture (a scan?): nothing on it can be found, so box it by hand."


def test_summary():
    assert summary(Result(areas=12, pages=1)) == "12 areas blacked out on 1 page."
    assert summary(Result(areas=1, pages=1, fields=2)) == "1 area blacked out on 1 page, and 2 form fields cleaned."
    assert summary(Result()) == "Nothing was blacked out."


def test_stage_text():
    assert stage_text("redact", 37, 242) == "Blacking out page 37 of 242…"
    assert stage_text("check", 242, 242) == "Checking page 242 of 242…"


def test_long_text_is_cut_short_in_a_sentence():
    address = "Apartment 12, 1234 Maple Street West, Springfield"
    text = finding_text(Finding("removed-elsewhere", "", page=3, text=address))
    assert "“Apartment 12, 1234 Maple Street West, S…”" in text and address not in text
    [add, show] = finding_fixes(Finding("removed-elsewhere", "", page=3, text=address))
    assert add.text == address                                  # the whole text goes on the list
