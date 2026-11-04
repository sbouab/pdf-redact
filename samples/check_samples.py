"""After running mark and apply on the samples, check the redacted files with a method independent of the
tool: no list item may be left anywhere in them, and the ordinary text must still be there.

    python samples/check_samples.py
"""
import json
import os
import re
import sys
import unicodedata

import pymupdf

FILES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")


def squash(s):
    """Strictest comparison: no accents, no case, no spaces or punctuation at all."""
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def all_text(doc):
    """Page text, plus every place outside the pages that can hold text."""
    parts = [p.get_text() for p in doc]
    parts += [json.dumps(doc.metadata, ensure_ascii=False), doc.get_xml_metadata() or "", json.dumps(doc.get_toc())]
    for p in doc:
        parts += [f"{a.info.get('content', '')} {a.info.get('title', '')} {a.info.get('subject', '')}" for a in p.annots()]
        parts += [str(w.field_value) for w in p.widgets()]
        parts += [ln.get("uri") or "" for ln in p.get_links()]
    return " ".join(parts)


def main():
    with open(os.path.join(FILES, "list.txt"), encoding="utf-8") as fh:
        items = [line.strip() for line in fh if line.strip()]
    with open(os.path.join(FILES, "truth.json"), encoding="utf-8") as fh:
        stay = json.load(fh)
    problems = 0
    for name, keep in stay.items():
        path = os.path.join(FILES, name[:-4] + " - REDACTED.pdf")
        if not os.path.exists(path):
            print(f"?    {name}: no REDACTED file (not applied yet, or its check failed)")
            problems += 1
            continue
        doc = pymupdf.open(path)
        everything, pages = squash(all_text(doc)), squash(" ".join(p.get_text() for p in doc))
        leaks = [i for i in items if squash(i) in everything]
        lost = [k for k in keep if squash(k) not in pages]
        no_text = not pages
        doc.close()
        status = "LOOK" if leaks or lost else "OK  "
        print(f"{status} {name}: {len(leaks)} list item(s) left, {len(lost)} ordinary text(s) missing"
              + ("  (no text at all: a scan; check it by eye)" if no_text else ""))
        for i in leaks:
            print(f"        still there: {i!r}")
        for k in lost:
            print(f"        missing: {k!r}")
        problems += len(leaks) + len(lost)
    print("\nAll list items are gone and the ordinary text is intact." if not problems else
          "\nSome items need a look (see above). A box you deleted on purpose shows up here too.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
