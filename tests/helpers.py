"""Fake documents for the tests (every name and number is invented), built in a test's temporary folder."""
from pathlib import Path

import make_samples as S          # samples/ is on the path (pyproject.toml, [tool.pytest.ini_options])
import pymupdf

LIST = S.LIST


def sample(folder, name):
    """One of the sample PDFs (see samples/make_samples.py), written in `folder`."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    path = Path(folder) / name
    S.SAMPLES[name][0](str(path))
    return path


def with_attachment(path):
    """Add a file attached inside the PDF at `path`."""
    doc = pymupdf.open(path)
    doc.embfile_add("notes.txt", b"call back Monday")
    doc.save(str(path) + ".tmp")
    doc.close()
    Path(str(path) + ".tmp").replace(path)


def encrypted(folder, password="s3cret"):
    """The T4 sample saved with a password (AES-256)."""
    doc = pymupdf.open(sample(folder, "t4_slip.pdf"))
    out = Path(folder) / "locked.pdf"
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")
    doc.close()
    return out


def scan_name_rect():
    """Where the name is drawn in the scan samples (they are pictures of this exact layout)."""
    src = pymupdf.open()
    S.write_lines(src.new_page(), S.SCAN_LINES, size=12)
    rect = src[0].search_for("Geneviève Côté-Tremblay")[0]
    src.close()
    return rect


def dark_share(path, rect, pno=0):
    """Share of dark pixels inside rect (PDF coordinates) on the rendered page."""
    doc = pymupdf.open(path)
    pix = doc[pno].get_pixmap(dpi=72, clip=rect)
    doc.close()
    n = pix.width * pix.height
    return sum(1 for i in range(n) if sum(pix.samples[i * pix.n:i * pix.n + 3]) < 150) / n


def page_text(path):
    doc = pymupdf.open(path)
    text = " ".join(p.get_text() for p in doc)
    doc.close()
    return text


def wait_until(predicate, timeout=60.0):
    """Let Qt process events until predicate() is true; fail after `timeout` seconds."""
    import time
    from PySide6.QtCore import QCoreApplication, QEventLoop
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError(f"still waiting after {timeout} s")
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def let_events_run(seconds):
    """Let Qt process events for a while (to show that something does NOT happen)."""
    import time
    end = time.monotonic() + seconds
    wait_until(lambda: time.monotonic() > end, timeout=seconds + 5)
