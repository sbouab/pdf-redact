"""List-based PDF redaction with a review step. See README.md."""
import os
import sys
from dataclasses import dataclass

# This runs before anything imports PyMuPDF. PyMuPDF 2.x imports Microsoft's onnxruntime for a layout
# feature this tool doesn't use, and its native code was seen opening a telemetry connection that
# Python-level blocking can't stop, so that module is never loaded. Proxy settings are dropped so that
# nothing could be relayed through a local proxy either.
sys.modules.setdefault("onnxruntime", None)
for _name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_name, None)

TAG = "redact_pdf"   # author name on every annotation this tool adds to a review copy


class UserError(Exception):
    """A problem the user can fix (a wrong path, a missing review copy...). Shown without a traceback."""


class PasswordError(UserError):
    """The PDF is password-protected and no password, or a wrong one, was given."""


@dataclass(frozen=True)
class Finding:
    """Something found by the checks or by a look at a document. str() is the message the command line prints;
    the other fields let the app word it its own way and offer a fix."""
    code: str                   # the kind of finding, e.g. "kept-visible" or "attachments"
    message: str
    page: int | None = None     # 1-based page number, when it is about one page
    text: str = ""              # the list item or the text concerned
    name: str = ""              # a form field's name, or the place outside the pages
    count: int = 0

    def __str__(self):
        return self.message
