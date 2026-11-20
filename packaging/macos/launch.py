"""Starts PDF Redact.app, the desktop app built for macOS by build.sh: the window, with the PDF the app was started
with, if any (PDFs from "Open With" or a drop on the Dock icon come as events: pdfredact/gui/fileopen.py).
`--self-check` checks the built app instead (build.sh runs it).

The app's helper processes run this file again, and multiprocessing.freeze_support() takes them over. Nothing in
any of these processes may use the network, so it is blocked first of all, before PyMuPDF or Qt is loaded, as
redact.py does."""
import multiprocessing
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):          # run from the repository: the package is two folders up
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdfredact import offline  # noqa: E402

offline.block_network()

if __name__ == "__main__":
    multiprocessing.freeze_support()            # in a helper process: runs the job, then exits
    if "--self-check" in sys.argv[1:]:
        from pdfredact.gui.selfcheck import run
        sys.exit(run())
    from pdfredact.gui.app import main
    sys.exit(main(next((arg for arg in sys.argv[1:] if arg.lower().endswith(".pdf")), None)))
