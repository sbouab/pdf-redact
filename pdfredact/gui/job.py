"""The redaction job. It runs in a helper process (PyMuPDF can't be used from two threads at once, and the
window must stay responsive): it reads the original again, makes sure it is the file the app opened, blacks out
the boxes with the same code as the command line, checks the result and reports back. No Qt here.
"""
import contextlib
import hashlib
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .. import UserError, offline
from ..document import XFA_HELP, file_hash, is_dynamic_xfa, open_pdf
from ..redaction import Result, redact
from ..text import Term

CHANGED = "the original file changed since you opened it: open it again"
UNREADABLE = "can't read the original file anymore ({why}): was it moved, renamed or deleted?"


@dataclass(frozen=True)
class JobRequest:
    """Everything the job needs, sent to the helper process through a pipe (never written to disk)."""
    source: str                  # path of the original
    source_hash: str             # SHA-256 of the bytes the app opened
    password: str | None
    items: tuple                 # the list
    boxes: dict                  # {page: [((x0, y0, x1, y1), kind)]} to black out, as Session.job_boxes gives them
    kept: tuple                  # the list's matches (review.Match) in spots kept visible, as Session.kept_matches
    drop_attachments: bool
    out_path: str                # where to write the result, in the app's private folder


@dataclass
class JobOutcome:
    result: Result | None = None
    path: str = ""               # the redacted copy, when there is a result
    sha256: str = ""             # of that file
    error: str = ""              # what went wrong, when there is no result
    blocked: list = field(default_factory=list)      # network attempts blocked in the helper process


def run_job(request, progress=None):
    """Redact and check as asked. Never raises: a problem comes back as `error`. `progress(stage, done, total)`
    is called after each page is redacted ("redact") and checked ("check").

    A list item may stay visible only in a spot kept visible (`request.kept`): anywhere else the check fails,
    whatever the reason it was left out of the boxes."""
    out = Path(request.out_path)
    try:
        try:
            data = Path(request.source).read_bytes()
        except OSError as e:
            raise UserError(UNREADABLE.format(why=e.strerror or e))
        if hashlib.sha256(data).hexdigest() != request.source_hash:
            raise UserError(CHANGED)
        doc = open_pdf(Path(request.source).name, request.password, data=data)
        try:
            if is_dynamic_xfa(doc):
                raise UserError(XFA_HELP)
            boxes = {pno: [(pymupdf.Rect(rect), kind) for rect, kind in page_boxes]
                     for pno, page_boxes in request.boxes.items()}
            terms = [Term(item) for item in request.items]
            result = redact(doc, boxes, terms, str(out), request.kept, request.drop_attachments, progress)
        finally:
            doc.close()
        return JobOutcome(result, str(out), file_hash(out))
    except UserError as e:
        _discard(out)
        return JobOutcome(error=str(e))
    except Exception as e:           # a bug: report it instead of leaving the window waiting
        traceback.print_exc()
        _discard(out)
        return JobOutcome(error=f"unexpected error ({type(e).__name__}: {e})")


def _discard(path):
    """Delete a half-made output if possible; one still busy goes with the app's private folder."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def child_main(conn, request):
    """Entry point of the helper process: block the network, run the job, send ("progress", stage, done, total)
    messages and then ("done", outcome). Stops quietly when the app no longer listens."""
    offline.block_network()

    def progress(stage, done, total):
        try:
            conn.send(("progress", stage, done, total))
        except OSError:
            raise SystemExit(0)          # the app closed the pipe: the job was cancelled
    try:
        outcome = run_job(request, progress)
        outcome.blocked = list(offline.BLOCKED)
        conn.send(("done", outcome))
    except OSError:
        pass
    finally:
        conn.close()
