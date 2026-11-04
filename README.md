# pdf-redact

Redact PDFs using your own list of things to remove, with a review step before anything is
blacked out. Everything runs on your computer: the tool never uses the network, and your original
files are never modified.

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

## Install (once)

1. Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/).
   On Windows, tick **Add python.exe to PATH** in the installer.
2. Open a terminal in this folder and run:

   **Windows** (PowerShell)
   ```
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
   **macOS** (Terminal)
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   In every new terminal, run the `activate` line again before using the tool. If PowerShell refuses
   to run `activate`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Use

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
* Use Notepad (Windows) or TextEdit (macOS). In TextEdit, choose **Format > Make Plain Text** before saving.

**2. Mark.**
```
python redact.py mark "document.pdf" --list "list.txt"
```
It prints how often each item was found, and which were **NOT FOUND**. An item not found is either
misspelled in the list or sits where its text can't be read (a scan, a picture): box it by hand.

**3. Review.** Open `document - REVIEW (not redacted).pdf` in your PDF viewer:
* to keep something visible, select its box and press Delete;
* to remove something more, draw a rectangle over it (Adobe Acrobat Reader: *Comment > Draw
  rectangle*; macOS Preview: *Markup toolbar > Shapes > Rectangle*) or highlight it. Do this for
  signatures, photos and anything inside pictures too;
* save it with the same name, in the same folder.

**4. Apply**, with the same list:
```
python redact.py apply "document.pdf" --list "list.txt"
```
This writes `document - REDACTED.pdf` and prints **PASS** or **FAIL**. Instead of the original, you can
also pass the review copy. Both commands also accept a folder: every PDF inside it, subfolders included.

When you are done, delete the review copy: it holds everything the original holds.

## What gets checked

After redacting, `apply` reopens the new file and checks that:
* nothing under a box can still be extracted, and every character outside the boxes is still there,
  at the same place;
* no item of the list is left anywhere: on the pages (except where you deleted its box, which is
  reported), in form fields, document properties, XMP metadata, bookmarks, comments, links, or as
  hidden text outside the page;
* no attached file or XFA form data is left unchecked.

Text it removed under one box that is still visible somewhere else is reported too. A file that fails
is named `document - FAILED CHECK (do not share).pdf`, never `REDACTED`.

Before sharing a file, look at every page, search it (Ctrl+F or Cmd+F) for a name from the list, and
try to select the text under a black box.

## Offline

The tool needs no network. It blocks any network connection its code attempts, and a trace of a full
run shows none. With `--require-offline` (or the environment variable `REDACT_REQUIRE_OFFLINE=1`) it
refuses to start if the computer can reach the internet: turn off Wi-Fi or unplug the cable first.

## Limits

* It finds only what is on your list, written as it appears in the document. The review is the safety net.
* No OCR: text inside pictures and scans can't be found, so box it by hand. Scans that already have a
  text layer (searchable PDFs) work through that layer; check that each box covers the visible words.
* Some government fillable forms are Adobe XFA forms, whose data isn't on the pages. Open them in
  Adobe Acrobat Reader, print them to PDF, and use the printed copy.
* Files attached inside a PDF can't be checked: `apply` fails unless you add `--drop-attachments`.
* The redacted file is saved without a password even if the original had one (`--password` opens it).
* Redacting a digitally signed PDF invalidates its signature.

## Try it with fake documents

```
python samples/make_samples.py
python redact.py mark samples/files --list samples/files/list.txt
python redact.py apply samples/files --list samples/files/list.txt
python samples/check_samples.py
```

## For developers

```
pip install pytest
python -m pytest
```

| File | Role |
|---|---|
| `redact.py` | entry point |
| `pdfredact/cli.py` | commands, file names, messages |
| `pdfredact/text.py` | the list and how items are matched; page text with character positions |
| `pdfredact/review.py` | `mark`: finding items, writing the review copy, reading boxes back |
| `pdfredact/redaction.py` | `apply`: redacting from the original, cleaning text outside the pages |
| `pdfredact/verify.py` | the checks on the redacted file |
| `pdfredact/document.py` | opening PDFs, form fields, XFA, text kept outside the pages |
| `pdfredact/offline.py` | blocking network access |

PyMuPDF is pinned to 1.26.x: version 2.x shifts the text next to each redaction, and it loads
Microsoft's onnxruntime, which was seen trying to reach a telemetry server.

This project uses [PyMuPDF](https://pymupdf.readthedocs.io), which is available under the AGPL-3.0 or a
commercial license from Artifex.
