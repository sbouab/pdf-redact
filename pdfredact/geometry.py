"""Small rectangle helpers."""
from functools import reduce

import pymupdf


def union(rects):
    return reduce(lambda a, b: a | b, rects[1:], pymupdf.Rect(rects[0]))


def center_in(box, rect):
    return rect.x0 <= (box.x0 + box.x1) / 2 <= rect.x1 and rect.y0 <= (box.y0 + box.y1) / 2 <= rect.y1


def near(a, b, tol):
    return abs(a.x0 - b.x0) <= tol and abs(a.y0 - b.y0) <= tol and abs(a.x1 - b.x1) <= tol and abs(a.y1 - b.y1) <= tol


def inside(a, b, tol=0.75):
    return a.x0 >= b.x0 - tol and a.y0 >= b.y0 - tol and a.x1 <= b.x1 + tol and a.y1 <= b.y1 + tol
