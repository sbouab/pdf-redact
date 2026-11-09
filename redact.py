#!/usr/bin/env python3
"""Redact PDFs with your own list of things to remove, with a review step. See README.md.

    python redact.py mark  document.pdf --list list.txt
    python redact.py apply document.pdf --list list.txt
    python redact.py gui   [document.pdf]
"""
import sys

from pdfredact import offline

# First of all, before PyMuPDF or Qt is loaded: nothing in this process may use the network. This also runs in
# the app's helper processes, which import this file again (as __mp_main__) before the job's code.
offline.block_network()

from pdfredact.cli import main  # noqa: E402  (loads PyMuPDF)

if __name__ == "__main__":
    sys.exit(main())
