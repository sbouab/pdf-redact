"""How the app words what the checks and the reading of a document found, and the fixes it offers.

The command line has its own messages (a finding's str()), which mention its commands and options; the app
words the same findings from their code and fields, in terms of what can be done in the window.
"""
from dataclasses import dataclass

SHORT = 40          # characters of a text quoted in a sentence or a menu; a longer one is cut with "…"

# what sanitize.py removes, whatever the list holds
ALWAYS_REMOVED = ("Every redacted copy also loses what no one sees on its pages: the document's properties and "
                  "metadata, bookmarks, the hidden text of comments, links to outside the document, scripts, "
                  "the values of hidden form fields, invisible text (such as a scan's text layer, or text too small to read) and anything outside "
                  "the visible page.")


@dataclass(frozen=True)
class Fix:
    """An action offered next to a finding: "page" goes to a page (1-based), "drop-attachments" removes the
    attached files and redacts again, "add-item" adds `text` to the list."""
    kind: str
    label: str
    page: int | None = None
    text: str = ""


def quoted(text, limit=SHORT):
    """“text”, cut short with “…” when it is longer than `limit` characters."""
    return f"“{text if len(text) <= limit else text[:limit - 1] + '…'}”"


def plural(count, word, many=None):
    """'1 box', '2 boxes'."""
    return f"{count} {word if count == 1 else many or word + 's'}"


def sentence(text):
    """`text` with a capital first and a period last (none added after "." or "…")."""
    text = text.strip()
    return text[:1].upper() + text[1:] + ("" if text.endswith((".", "…")) else ".")


_CHECKS = {
    "page-count": lambda f: "The number of pages changed while redacting",
    "text-lost": lambda f: f"Page {f.page}: text outside the boxes was removed too",
    "text-left": lambda f: f"Page {f.page}: text under a black box can still be copied out",
    "text-moved": lambda f: f"Page {f.page}: {plural(f.count, 'character')} outside the boxes moved",
    "not-boxed": lambda f: f"Page {f.page}: {quoted(f.text)} is on your list but still visible there",
    "hidden-text": lambda f: f"Page {f.page}: hidden text (cut off, or outside the visible page) still contains "
                             f"{quoted(f.text)}",
    "invisible-text": lambda f: f"Page {f.page}: invisible text is still there ({plural(f.count, 'character')})",
    "outside-text": lambda f: f"Page {f.page}: text outside the visible page is still there "
                              f"({plural(f.count, 'character')})",
    "shapes-left": lambda f: f"Page {f.page}: {plural(f.count, 'drawn shape')} still inside a blacked-out area",
    "fields-changed": lambda f: f"Page {f.page}: the number of form fields changed",
    "comments-changed": lambda f: f"Page {f.page}: the number of comments changed",
    "field-not-boxed": lambda f: f"Page {f.page}: the form field {quoted(f.name)} still contains {quoted(f.text)}",
    "links-changed": lambda f: "The number of links changed unexpectedly",
    "hidden-place": lambda f: f"{f.name} still contains {quoted(f.text)}",
    "attachments": lambda f: f"This PDF carries {plural(f.count, 'attached file')}. Attached files can't be checked, "
                             "so they must go",
    "xfa-left": lambda f: "Hidden XFA form data is still in the file",
    "metadata-left": lambda f: "The document's properties or metadata are still in the file",
    # warnings
    "kept-visible": lambda f: f"Page {f.page}: {quoted(f.text)} is still visible where you kept it",
    "removed-elsewhere": lambda f: f"Page {f.page}: {quoted(f.text)} was removed under a box elsewhere but is still "
                                   "visible here",
    "field-kept": lambda f: f"Page {f.page}: the form field {quoted(f.name)} still shows {quoted(f.text)}, as you "
                            "kept it",
}

_DOCUMENT = {
    "xfa-form": lambda f: "This is an XFA form: its hidden copy of the form data is removed (the regular form "
                          "fields stay)",
    "attached-files": lambda f: f"This PDF carries {plural(f.count, 'attached file')}. Attached files can't be "
                                "checked, so the redacted copy can only be saved without them",
    "signed": lambda f: "This PDF is digitally signed: redacting it invalidates the signature, which also holds "
                        "the signer's identity",
}

_SHOWN = {"kept-visible", "field-kept"}          # warnings about a choice the user made: just show where


def finding_text(f):
    """A check result as the app says it."""
    return sentence(_CHECKS[f.code](f) if f.code in _CHECKS else str(f))


def finding_fixes(f):
    """The actions to offer next to a check result, the main one first."""
    if f.code == "attachments":
        return [Fix("drop-attachments", "Remove attached files and redo")]
    if f.code == "removed-elsewhere":
        return [Fix("add-item", "Add to list", text=f.text), Fix("page", "Show", page=f.page)]
    if f.page is None:
        return []
    return [Fix("page", "Show" if f.code in _SHOWN else f"Go to page {f.page}", page=f.page)]


def warning_text(f):
    """A warning about the whole document (document_warnings) as the app says it."""
    return sentence(_DOCUMENT[f.code](f) if f.code in _DOCUMENT else str(f))


def problem_text(page, problem):
    """Why items on a page (1-based) could go unfound, as the app says it."""
    return sentence(f"Page {page} {problem}")


def summary(result):
    """One line on what a redaction did."""
    if not result.areas and not result.fields:
        return "Nothing was blacked out."
    text = f"{plural(result.areas, 'area')} blacked out on {plural(result.pages, 'page')}"
    if result.fields:
        text += f", and {plural(result.fields, 'form field')} cleaned"
    return sentence(text)


def stage_text(stage, done, total):
    """What the redaction job is doing, for its progress bar."""
    if stage == "redact":
        return f"Blacking out page {done} of {total}…"
    return f"Checking page {done} of {total}…"
