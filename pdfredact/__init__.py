"""List-based PDF redaction with a review step. See README.md."""
import os
import sys

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
