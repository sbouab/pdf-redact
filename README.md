# pdf-redact

Redact PDFs using your own list of things to remove, with a review step before anything is
blacked out. Everything runs on your computer: the tool never uses the network, and your original
files are never modified.

Use it as **an app** (one window: open a PDF, type what to remove, see it boxed at once, save the
checked result) or from **the command line** (two commands, also for whole folders). Both use the
same matching, redaction and checks.

## Install (once)

1. Install [uv](https://docs.astral.sh/uv/), then close and reopen the terminal.

   **Windows** (PowerShell)
   ```
   winget install --id=astral-sh.uv -e
   ```
   **macOS** (Terminal)
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   **Linux** (Terminal)
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   If the terminal says `curl` is not found, use this instead:
   ```
   wget -qO- https://astral.sh/uv/install.sh | sh
   ```
2. In a terminal opened in this folder, run:
   ```
   uv sync --no-offline
   ```

This creates a private `.venv` folder here with Python, PyMuPDF and Qt (for the app) at the versions
pinned in `uv.lock`. Nothing is installed on your system, and there is nothing to activate: uv
downloads Python if the computer has none. This is the only step that uses the network: this project
tells uv to work offline, and `--no-offline` lets this one command download (see [Offline](#offline)).
Every command below starts with `uv run` and must be run from this folder.

On Linux, the app also needs a system library that some distributions leave out. On Ubuntu or
Debian: `sudo apt install libxcb-cursor0`.

## The app

```
uv run redact.py gui
```

or, to open a PDF right away, `uv run redact.py gui "document.pdf"`.

1. **Open** a PDF: drop it on the window, or click *Open PDF…* (Cmd+O on a Mac, Ctrl+O elsewhere).
2. **Type what to remove** in the box on the right and press Enter: a name, a number, an address...
   Paste several lines to add them all, or drag over text on the page and choose *Add to the list*.
   A red box appears at once wherever an item is found, and each item shows how many times it was
   found. An item **not found** is misspelled, or sits where its text can't be read (a scan, a
   picture): drag a box over it.
3. **Fix single spots.** Click a red box to keep that spot visible (it turns into a dashed outline).
   Drag over anything else to box it: a signature, a photo, text in a scan. Click a box you drew to
   delete it. Undo works for everything (Cmd+Z, or Ctrl+Z).
4. **Look at the result**: click *Redacted*. The app blacks out a fresh copy of the original and
   runs every check listed under [What gets checked](#what-gets-checked). It starts as soon as you
   pause editing, so the result is usually ready when you switch.
5. **Save redacted PDF…** writes the file wherever you choose. It stays off until every check passes.

The app keeps nothing. The list is typed in the app and never saved. The redacted copy waits in
memory until you save it; while it is being made it sits in a private temporary folder that is
deleted when you close the app (or at the next start, after a crash). Opening another PDF keeps the
list, to redact several files about the same person in a row. Your system's Open and Save windows
may remember recent folders, as they do for every app.

## The command line

```
 document.pdf                                  the original, only read
      │
  1. mark  ──►  document - REVIEW (not redacted).pdf     a red box on every item of your list
      │
  2. you open the review copy, delete boxes, draw new ones, save
      │
  3. apply ──►  document - REDACTED.pdf                  the boxed areas blacked out, then checked
```

`apply` always starts again from the original. The review copy only tells it where the boxes are.

**1. Write the list.** A plain text file with one item per line: names, numbers, addresses, emails...

```
Geneviève Côté-Tremblay
Côté-Tremblay
Geneviève
046 454 286
1234, rue des Érables
H2X 1Y4
genevieve.cote@example.com
```

* Case, accents and the spacing or punctuation between parts don't matter: `046 454 286` also finds
  `046-454-286` and `046454286`, and `Côté-Tremblay` finds `COTE TREMBLAY`.
* An item is never found inside a longer word: `Paul` does not match `Pauline`.
* List every form a name takes in the document: the full name, the last name alone, the first name alone.
* Use Notepad (Windows), TextEdit (macOS) or Text Editor (Linux). In TextEdit, choose
  **Format > Make Plain Text** before saving.

**2. Mark.**
```
uv run redact.py mark "document.pdf" --list "list.txt"
```
It prints how often each item was found, and which were **NOT FOUND**. An item not found is either
misspelled in the list or sits where its text can't be read (a scan, a picture): box it by hand.

**3. Review.** Open `document - REVIEW (not redacted).pdf` in your PDF viewer:
* to keep something visible, select its box and press Delete;
* to remove something more, draw a rectangle over it (Adobe Acrobat Reader: *Comment > Draw
  rectangle*; macOS Preview: *Markup toolbar > Shapes > Rectangle*; Okular on Linux: press *F6*,
  then the rectangle tool) or highlight it. Do this for signatures, photos and anything inside
  pictures too;
* save it with the same name, in the same folder.

**4. Apply**, with the same list:
```
uv run redact.py apply "document.pdf" --list "list.txt"
```
This writes `document - REDACTED.pdf` and prints **PASS** or **FAIL**. Instead of the original, you can
also pass the review copy. Both commands also accept a folder: every PDF inside it, subfolders included.

When you are done, delete the review copy: it holds everything the original holds.

## What gets removed

Besides what is under the boxes, every redacted copy loses what no one sees when looking at its pages,
whatever the list holds, as a reviewer can only judge what is shown:
* document properties (title, author... and any other property an application added) and XMP metadata,
  anywhere in the file; the metadata inside JPEG pictures (EXIF, XMP, IPTC, comments: camera, place,
  date, often a small copy of the picture);
* bookmarks, sticky notes, hidden comments, and the hidden text of the other comments (author, notes,
  dates); links to anything outside the document (web pages, e-mail addresses, other files);
* scripts and automatic actions (also those set to run after another action, such as a link), page
  thumbnails, structure tags (they can hold a copy of the text or a description of a picture), data kept
  by the application that made the PDF, alternate versions of pictures, article threads, the default
  values and tooltips of form fields, the values of form fields that no page shows (hidden, or on no
  page), XFA form data;
* on every page, invisible text (such as the text layer of a scan, or text too small to read) and
  whatever lies outside the visible page: text, and the parts of pictures cut off by its edges.

Hidden layers are shown when a PDF is opened, so what they hold is seen, searched and redacted like the
rest of the page. Each removal is listed in the output of `apply` and in the app's Redacted view.

## What gets checked

After redacting, `apply` reopens the new file and checks that:
* nothing under a box can still be extracted, and every character outside the boxes is still there,
  at the same place;
* no item of the list is left anywhere: on the pages (except where you deleted its box, which is
  reported), in form fields, or in any text kept in the file's data (comments, links, names...);
* no document properties, XMP metadata, invisible text or text outside the visible page is left;
* no attached file or XFA form data is left unchecked.

Text it removed under one box that is still visible somewhere else is reported too. A file that fails
is named `document - FAILED CHECK (do not share).pdf`, never `REDACTED`.

Before sharing a file, look at every page, search it (Ctrl+F or Cmd+F) for a name from the list, and
try to select the text under a black box.

## Offline

The tool needs no network, and regular use never touches it:
* `redact.py` blocks every network connection as its very first step, before PyMuPDF or Qt is loaded,
  and so do the app's helper processes. Any attempt fails and is reported; a trace of a full run shows
  none.
* uv works offline for this project (`offline = true` in `pyproject.toml`): `uv run` uses what is
  installed and never looks for packages or updates. Only the install, `uv sync --no-offline`, downloads.

With `--require-offline` (or the environment variable `REDACT_REQUIRE_OFFLINE=1`) the tool refuses to
start if the computer can reach the internet: turn off Wi-Fi or unplug the cable first. The app works
the same way, and says in its status bar if anything was blocked.

Install first, then go offline:

```
uv run redact.py mark "document.pdf" --list "list.txt" --require-offline
uv run redact.py gui --require-offline
```

## Limits

* It finds only what is on your list, written as it appears in the document. The review is the safety net.
* No OCR: text inside pictures and scans can't be found, so box it by hand. Scans that already have a
  text layer (searchable PDFs) are searched through that layer; check that each box covers the visible
  words. That layer is invisible, so it is removed: a redacted scan is a picture only, no longer searchable.
* A PDF with layers is redacted with all of them shown; the redacted copy has no layers.
* Text that is on a page but can't be seen there, because it is drawn in the color of the background or
  covered by a picture, can't be told apart from visible text: it stays unless it holds an item of the
  list. Searching the redacted file (Ctrl+F or Cmd+F) shows it.
* Text cut off by a clipping path (drawn, but outside the area the page lets show) is not searched, as no
  one sees it. It stays in the file; if it holds an item of the list, the check fails (`hidden-text`).
  When part of the item shows, box that part; otherwise print the page to PDF and redact the printed copy.
* A picture or a block of content that a page keeps in its resources but never draws stays in the file
  and isn't checked (the checks read what the pages draw and the file's text strings).
* Some government fillable forms are Adobe XFA forms, whose data isn't on the pages. Open them in
  Adobe Acrobat Reader, print them to PDF, and use the printed copy.
* Files attached inside a PDF can't be checked: `apply` fails unless you add `--drop-attachments`.
* The redacted file is saved without a password even if the original had one (`--password` opens it;
  the app asks for it).
* The app works on one PDF at a time; for whole folders, use the command line.
* Redacting a digitally signed PDF invalidates its signature.

## Try it with fake documents

```
uv run samples/make_samples.py
uv run redact.py mark samples/files --list samples/files/list.txt
uv run redact.py apply samples/files --list samples/files/list.txt
uv run samples/check_samples.py
```

## For developers

`uv sync --no-offline` also installs pytest:

```
uv run pytest
```

The app's tests open no window: they run Qt without a screen (its "offscreen" platform).

Dependencies are declared in `pyproject.toml`. After changing them, run `uv lock --no-offline` and commit
`uv.lock`.

| File | Role |
|---|---|
| `pyproject.toml`, `uv.lock` | dependencies and their pinned versions |
| `redact.py` | entry point |
| `pdfredact/cli.py` | commands and messages |
| `pdfredact/naming.py` | the names of the files written |
| `pdfredact/text.py` | the list and how items are matched; page text with character positions |
| `pdfredact/review.py` | `mark`: finding items, writing the review copy, reading boxes back |
| `pdfredact/redaction.py` | `apply`: redacting from the original |
| `pdfredact/sanitize.py` | removing what no one sees on the pages: metadata, hidden text, hidden data |
| `pdfredact/verify.py` | the checks on the redacted file |
| `pdfredact/document.py` | opening PDFs (with every layer shown), form fields, XFA, text kept outside the pages |
| `pdfredact/offline.py` | blocking network access |
| `pdfredact/gui/app.py`, `window.py` | the app: start-up, then the window that wires the parts below |
| `pdfredact/gui/session.py` | the open PDF and the edits: the list, the spots kept, the boxes drawn |
| `pdfredact/gui/job.py`, `runner.py` | redacting and checking in a helper process, so the window never waits |
| `pdfredact/gui/workspace.py` | the private temporary folder, and deleting the ones a crash left |
| `pdfredact/gui/viewer.py`, `layers.py` | the pages, the boxes drawn over them, clicks and drags |
| `pdfredact/gui/review_panel.py`, `result_panel.py` | the two sidebars |
| `pdfredact/gui/commands.py`, `wording.py`, `widgets.py`, `style.py` | undo, the app's wording, small parts, the look |

PyMuPDF is pinned to 1.26.x: version 2.x shifts the text next to each redaction, and it loads
Microsoft's onnxruntime, which was seen trying to reach a telemetry server.

This project uses [PyMuPDF](https://pymupdf.readthedocs.io), which is available under the AGPL-3.0 or a
commercial license from Artifex, and [Qt for Python](https://doc.qt.io/qtforpython-6/) (PySide6), available
under the LGPL-3.0.
