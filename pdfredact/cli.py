"""Command line: python redact.py mark|apply ... (see --help)."""
import argparse
import glob
import os
import sys

import pymupdf

from . import UserError, offline
from .document import file_hash
from .redaction import apply
from .review import mark
from .text import load_list

REVIEW = " - REVIEW (not redacted)"
REDACTED = " - REDACTED"
FAILED = " - FAILED CHECK (do not share)"

DESCRIPTION = """\
Redact PDFs using your own list of things to remove. The original is never modified.

  1. python redact.py mark document.pdf --list list.txt
       finds every item of the list and writes "document - REVIEW (not redacted).pdf"
       with a red box on each one
  2. open that review copy, delete the boxes over anything that should stay visible,
     draw rectangles over anything else to remove, and save it (same name, same folder)
  3. python redact.py apply document.pdf --list list.txt
       blacks out the boxed areas in a fresh copy of the original, checks the result
       and writes "document - REDACTED.pdf"

The list is a plain text file with one item per line: a name, a number, an address...
Case, accents and spacing don't matter.
"""


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")       # accented names on any console
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)

    route = offline.internet_route()                # asked before the network is blocked; sends nothing
    if args.require_offline or os.environ.get("REDACT_REQUIRE_OFFLINE") == "1":
        if route:
            print("STOPPED: this computer can reach the internet and offline mode was required, so no file was "
                  "opened. Turn off Wi-Fi or unplug the cable, then run the command again.", file=sys.stderr)
            return 2
        print("network: offline confirmed", file=sys.stderr)
    offline.block_network()
    if int(pymupdf.VersionBind.split(".")[0]) >= 2:
        print(f"WARNING: PyMuPDF {pymupdf.VersionBind} moves the text next to each redaction. Install the "
              'tested version:  pip install "pymupdf>=1.26.7,<1.27"', file=sys.stderr)
    try:
        terms, too_short = load_list(args.list)
        files = originals(args.pdfs)
    except UserError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if too_short:
        print(f"note: list line(s) too short to search for, box them by hand: {', '.join(too_short)}")
    if not files:
        print(f"No PDF files found in: {', '.join(args.pdfs)}")
        return 1
    status = (run_mark if args.command == "mark" else run_apply)(args, terms, files)
    if offline.BLOCKED:
        print(f"\nnote: blocked {len(offline.BLOCKED)} network attempt(s) to {sorted(set(offline.BLOCKED))}",
              file=sys.stderr)
    return status


def _parser():
    p = argparse.ArgumentParser(prog="redact.py", description=DESCRIPTION,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True, metavar="{mark,apply}")
    mk = sub.add_parser("mark", help="make a review copy with a box on every list item")
    ap = sub.add_parser("apply", help="black out the boxed areas in a new file and check it")
    for s, what in ((mk, "PDF files or folders"), (ap, "PDF files, their review copies, or folders")):
        s.add_argument("pdfs", nargs="+", metavar="PDF", help=what)
        s.add_argument("--list", required=True, metavar="FILE", help="plain text file with one item to redact per line")
        s.add_argument("--password", help="password of protected PDFs")
        s.add_argument("--require-offline", action="store_true",
                       help="stop unless this computer is offline (or set REDACT_REQUIRE_OFFLINE=1)")
    mk.add_argument("--overwrite", action="store_true", help="replace an existing review copy (its box edits are lost)")
    ap.add_argument("--drop-attachments", action="store_true",
                    help="remove files attached inside the PDFs (they can't be checked)")
    return p


# ----------------------------------------------------------------------------- file names
def _split(path):
    """(folder, base name, the tool's suffix or None) of a PDF path."""
    folder, name = os.path.split(os.path.abspath(path))
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    for suffix in (REVIEW, REDACTED, FAILED):
        if stem.lower().endswith(suffix.lower()):
            return folder, stem[:-len(suffix)], suffix
    return folder, stem, None


def output_path(original, suffix):
    folder, stem, _ = _split(original)
    return os.path.join(folder, stem + suffix + ".pdf")


def originals(paths):
    """The original PDFs given on the command line. A folder means every PDF in it, subfolders included;
    a review copy stands for its original; the tool's other output files are skipped."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            found = sorted(f for f in glob.glob(os.path.join(glob.escape(p), "**", "*"), recursive=True)
                           if f.lower().endswith(".pdf") and os.path.isfile(f))
            out += [os.path.abspath(f) for f in found if _split(f)[2] is None]
            continue
        if not os.path.isfile(p):
            raise UserError(f"not found: {p}")
        folder, stem, suffix = _split(p)
        if suffix is None:
            out.append(os.path.abspath(p))
        elif suffix == REVIEW:
            same = [f for f in os.listdir(folder) if f.lower() == f"{stem}.pdf".lower()]
            if not same:
                raise UserError(f"can't find the original '{stem}.pdf' next to the review copy {p}. "
                                "Keep both in the same folder with their names unchanged.")
            out.append(os.path.join(folder, same[0]))
        else:
            raise UserError(f"{p} was made by this tool: give the original PDF or its review copy")
    return list(dict.fromkeys(out))


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _wrap(label, items, width=100):
    """Items after a label, separated by dots and wrapped between items (never inside one)."""
    lines, cur, empty = [], label, True
    for item in items:
        if not empty and len(cur) + 3 + len(item) > width:
            lines.append(cur + " ·")
            cur, empty = " " * len(label), True
        cur += item if empty else f" · {item}"
        empty = False
    return "\n".join(lines + [cur])


# ----------------------------------------------------------------------------- commands
def run_mark(args, terms, files):
    status = 0
    for src in files:
        name, review = os.path.basename(src), output_path(src, REVIEW)
        try:
            if os.path.exists(review) and not args.overwrite:
                raise UserError(f"a review copy already exists and may hold your edits:\n   {review}\n"
                                "   Delete it, or add --overwrite to replace it.")
            r = mark(src, terms, review, file_hash(src), args.password)
        except UserError as e:
            print(f"\n{name}: SKIPPED: {e}")
            status = 1
            continue
        print(f"\n{name}: {r.boxes} box(es) on {r.pages} page(s)" + (f", {r.fields} form field(s)" if r.fields else ""))
        found = [f"{t.text} ({r.found[i]})" for i, t in enumerate(terms) if r.found.get(i)]
        missing = [t.text for i, t in enumerate(terms) if not r.found.get(i)]
        if found:
            print(_wrap("   found:     ", found))
        if missing:
            print(_wrap("   NOT FOUND: ", missing))
            print("      Check the spelling. If you can see one of these in this file, its text can't be read "
                  "there: box it by hand.")
        for line in r.problems + r.warnings:
            print(f"   ! {line}")
        if r.hidden:
            print(f"   ! also in: {', '.join(r.hidden)} (apply cleans these)")
        print(f"   review copy: {review}")
    return status


def run_apply(args, terms, files):
    status = 0
    for src in files:
        folder, stem, _ = _split(src)
        name, review = os.path.basename(src), output_path(src, REVIEW)
        work = os.path.join(folder, f".{stem}.redacting.pdf")
        try:
            if not os.path.exists(review):
                raise UserError(f"no review copy ('{os.path.basename(review)}'): run mark first")
            r = apply(src, review, terms, file_hash(src), work, args.password, args.drop_attachments)
        except UserError as e:
            _remove(work)
            print(f"\n{name}: SKIPPED: {e}")
            status = 1
            continue
        except BaseException:
            _remove(work)
            raise
        final = output_path(src, FAILED if r.fails else REDACTED)
        try:
            _remove(output_path(src, REDACTED if r.fails else FAILED))    # an older result would be misleading
            os.replace(work, final)
        except OSError as e:
            _remove(work)
            print(f"\n{name}: SKIPPED: can't write {os.path.basename(final)} ({e.strerror}). "
                  "If an earlier result is open in a PDF viewer, close it and run apply again.")
            status = 1
            continue
        edits = (f"; you removed or moved {r.deleted} box(es) and drew {r.added}" if r.deleted or r.added else "")
        print(f"\n[{'FAIL' if r.fails else 'PASS'}] {name} -> {os.path.basename(final)}")
        print(f"   {r.areas} area(s) blacked out on {r.pages} page(s)"
              + (f", {r.fields} form field(s) cleaned" if r.fields else "") + edits)
        for prefix, lines in (("+", r.notes), ("x", r.fails), ("!", r.warns)):
            for line in lines:
                print(f"   {prefix} {line}")
        if r.fails:
            print("   Not safe to share. Fix the review copy (or the list) and run apply again.")
        status |= bool(r.fails)
    return status
