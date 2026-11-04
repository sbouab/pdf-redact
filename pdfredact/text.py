"""The list of items to redact, and page text with the position of every character."""
import functools
import hashlib
import re
import unicodedata
from collections import defaultdict

import pymupdf

from . import UserError
from .geometry import union

FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP   # ligatures come out as separate letters

# ----------------------------------------------------------------------------- normalization
_SPACES = set("\t               　")
_SAME = {"‘": "'", "’": "'", "‛": "'", "ʼ": "'", "´": "'", "`": "'",
         "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-", "­": "-",
         "“": '"', "”": '"', "«": '"', "»": '"'}


@functools.lru_cache(maxsize=8192)
def _norm_char(c):
    if c in _SPACES:
        return " "
    if c in _SAME:
        return _SAME[c]
    d = unicodedata.normalize("NFD", c)
    base = d[0] if len(d) > 1 and all(unicodedata.combining(x) for x in d[1:]) else c
    low = base.lower()
    return low if len(low) == 1 else base


def normalize(text):
    """Lower case, no accents, one kind of space, quote and dash. The result has the same length as the
    input, so a position in the normalized text points at the same character in the original."""
    return "".join(_norm_char(c) for c in text)


# ----------------------------------------------------------------------------- the list
class Term:
    """One item to redact. Case, accents, and spaces or punctuation between its parts don't matter:
    '046 454 286' also finds '046-454-286' and '046454286', 'Côté-Tremblay' finds 'COTE TREMBLAY',
    'G1R 4T5' finds 'G1R4T5'. It is never found inside a longer word or number."""

    def __init__(self, text):
        self.text = text
        tokens = re.findall(r"[^\W\d_]+|\d+", normalize(text))      # runs of letters, runs of digits
        self.pattern = None
        if sum(map(len, tokens)) >= 2:
            body = r"[\W_]*".join(r"[\s./-]{0,3}".join(t) if t.isdigit() else re.escape(t) for t in tokens)
            start = r"(?<!\d)" if tokens[0].isdigit() else r"(?<![^\W\d_])"
            end = r"(?!\d)" if tokens[-1].isdigit() else r"(?![^\W\d_])"
            self.pattern = re.compile(start + body + end)

    def find(self, norm):
        """(start, end) of every occurrence in normalized text."""
        return [m.span() for m in self.pattern.finditer(norm)] if self.pattern else []


def list_fingerprint(terms):
    """Changes when an item is added, removed or edited; not when lines are reordered."""
    return hashlib.sha256("\n".join(sorted(t.pattern.pattern for t in terms)).encode()).hexdigest()


def load_list(path):
    """The items in a list file, one per line. Returns (terms, lines too short to search)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        raise UserError(f"can't read the list file {path}: {e.strerror}")
    if raw.lstrip().startswith(b"{\\rtf"):
        raise UserError(f"{path} is a rich-text (RTF) file, not plain text. In TextEdit choose "
                        "Format > Make Plain Text, then save it again.")
    if raw[:2] == b"PK" or raw[:4] in (b"%PDF", b"\xd0\xcf\x11\xe0"):
        raise UserError(f"{path} is not a plain text file (a Word document or a PDF?). "
                        "Save the list as plain text (.txt).")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")      # older Windows editors
    terms, short, seen = [], [], set()
    for line in text.splitlines():
        item = line.strip()
        if not item:
            continue
        term = Term(item)
        if term.pattern is None:
            short.append(item)
        elif term.pattern.pattern not in seen:
            seen.add(term.pattern.pattern)
            terms.append(term)
    if not terms:
        raise UserError(f"the list file {path} has nothing to look for (write one item per line)")
    return terms, short


# ----------------------------------------------------------------------------- page text
class PageText:
    """A page's text as one string, with each character's box (None for the line breaks added between
    lines) and the number of the line it belongs to."""

    def __init__(self, page):
        chars, self.boxes, self.lines, line = [], [], [], 0
        for block in page.get_text("rawdict", flags=FLAGS)["blocks"]:
            for ln in block.get("lines", []):
                if chars:
                    chars.append("\n")
                    self.boxes.append(None)
                    self.lines.append(-1)
                for span in ln["spans"]:
                    for ch in span["chars"]:
                        for c in ch["c"]:          # one character, normally
                            chars.append(c)
                            self.boxes.append(pymupdf.Rect(ch["bbox"]))
                            self.lines.append(line)
                line += 1
        self.text = "".join(chars)
        self.norm = normalize(self.text)

    def visible(self, i):
        return self.boxes[i] is not None and not self.text[i].isspace()

    def rects(self, start, end):
        """One rectangle per text line covering the characters in [start, end)."""
        by_line = defaultdict(list)
        for i in range(start, end):
            if self.visible(i):
                by_line[self.lines[i]].append(self.boxes[i])
        return [union(boxes) for boxes in by_line.values()]


_WORD = re.compile(r"[(\"'\[]*[^\W\d_]+(?:['.-][^\W\d_]+)*[.,:;!?)\"'\]]*")
_PUNCTUATION = set(".,:;'\"()[]-/$%&@#*+=€£¥«»?!°·–—’‘“”<>_|")


def looks_garbled(text):
    """True when a text layer is mostly not words: a font with a broken encoding looks fine on screen
    but its text comes out as gibberish, so nothing in it can be found."""
    n = sum(1 for c in text if not c.isspace())
    if n < 30:
        return False
    tokens = text.split()
    symbols = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in _PUNCTUATION))
    odd = sum(1 for c in text if c == "�" or "" <= c <= "" or (c < " " and c not in "\n\t"))
    if n / len(tokens) > 14 or symbols > 0.10 * n or odd > 0.05 * n:   # normal text: words of ~4-7 characters
        return True
    lettered = [t for t in tokens if any(c.isalpha() for c in t)]
    words = 0
    for t in lettered:
        t = normalize(t)
        if _WORD.fullmatch(t) and (set(t) & set("aeiouy") or any(ord(c) > 0x24F for c in t)):   # or a non-Latin script
            words += 1
    return len(lettered) >= 8 and words < 0.4 * len(lettered)


def page_problems(page, pt, images):
    """Reasons why items on this page could go unfound (to be boxed by hand)."""
    n = sum(1 for i in range(len(pt.text)) if pt.visible(i))
    area, full = abs(page.rect), page.rect * page.derotation_matrix
    out = []
    if n < 20 and images:
        out.append("has no text, only a picture (a scan?): nothing on it can be found, so box it by hand")
    elif n < 20 and len(page.get_drawings()) >= 30:
        out.append("seems to have its text drawn as shapes: nothing on it can be found, so box it by hand")
    elif looks_garbled(pt.text):
        out.append("has text that can't be read properly (broken font encoding): items on it may not be found, "
                   "so check it by eye")
    elif "�" in pt.text:
        out.append("has characters that can't be decoded: items containing them may not be found")
    if n >= 20 and any(abs(im & full) >= 0.5 * area for im in images):
        out.append("is a scan with a text layer: items are found through that layer, which can miss words or sit "
                   "slightly off the picture, so check that each box covers the visible words")
    elif n >= 20 and any(abs(im & full) >= 0.02 * area for im in images):
        out.append("has pictures: anything written inside a picture (an ID card, a signature, a stamp) "
                   "can't be found, so check them by eye")
    shown = len(page.get_text("text").split())
    everywhere = len(page.get_text("text", flags=0, clip=pymupdf.INFINITE_RECT()).split())
    if everywhere > shown:
        out.append("has hidden text outside the visible page, which can't be boxed")
    return out
