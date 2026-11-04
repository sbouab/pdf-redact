#!/usr/bin/env python3
"""Redact PDFs with your own list of things to remove, with a review step. See README.md.

    python redact.py mark  document.pdf --list list.txt
    python redact.py apply document.pdf --list list.txt
"""
import sys

from pdfredact.cli import main

if __name__ == "__main__":
    sys.exit(main())
