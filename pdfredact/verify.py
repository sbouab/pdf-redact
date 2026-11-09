"""Checks on the redacted file. Any failure means it must not be shared."""
from collections import Counter, defaultdict

import pymupdf

from . import Finding
from .document import has_xfa, hidden_texts, open_pdf, text_fields, widget_value
from .geometry import center_in, inside, near
from .sanitize import invisible_count, metadata_left, outside_count, pdf_strings
from .text import PageText, normalize


def match_chars(expected, got, tol=0.3):
    """Pair characters by value and position (within tol points). Returns (unmatched expected, unmatched got)."""
    pool = defaultdict(list)
    for c, x, y in got:
        pool[(c, round(y))].append([x, y, False])
    lost = []
    for c, x, y in expected:
        for key in ((c, round(y)), (c, round(y) - 1), (c, round(y) + 1)):
            hit = next((g for g in pool.get(key, []) if not g[2] and abs(g[0] - x) <= tol and abs(g[1] - y) <= tol), None)
            if hit:
                hit[2] = True
                break
        else:
            lost.append((c, x, y))
    extra = [(k[0], g[0], g[1]) for k, items in pool.items() for g in items if not g[2]]
    return lost, extra


def check(out_path, plan, terms, extra, drop_attachments=False, progress=None):
    """Returns (fails, warnings), as lists of Finding. `terms` is the list; `extra` is other text removed under the
    boxes. `progress(stage, done, total)`, if given, is called after each page is checked."""
    fails, warns = [], []
    out = open_pdf(out_path)
    try:
        if len(out) != len(plan.pages):
            return [Finding("page-count", f"the page count changed ({len(plan.pages)} -> {len(out)})")], warns
        links_now = 0
        for pno, page in enumerate(out):
            n, before, after, frects = pno + 1, plan.pages[pno], PageText(page), plan.field_rects[pno]

            # 1. every character outside the boxes is still there, at the same place; none inside is
            def chars(pt, skip):
                return [(pt.text[i], b.x0, b.y0) for i, b in enumerate(pt.boxes)
                        if pt.visible(i) and i not in skip and not any(center_in(b, r) for r in frects)]
            lost, gained = match_chars(chars(before, plan.taken[pno]), chars(after, set()))
            moved = Counter(c for c, _, _ in lost) & Counter(c for c, _, _ in gained)
            lost_only = Counter(c for c, _, _ in lost) - moved
            gained_only = Counter(c for c, _, _ in gained) - moved
            if lost_only:
                s = "".join(sorted(lost_only.elements()))[:60]
                fails.append(Finding("text-lost", f"page {n}: text outside the boxes was lost: {s!r}", page=n, text=s))
            if gained_only:
                s = "".join(sorted(gained_only.elements()))[:60]
                fails.append(Finding("text-left", f"page {n}: text inside the boxes is still there: {s!r}", page=n,
                                     text=s))
            if moved:
                k = sum(moved.values())
                fails.append(Finding("text-moved", f"page {n}: {k} character(s) outside the boxes moved "
                                                   "(a known PyMuPDF 2.x problem: install pymupdf 1.26.x)",
                                     page=n, count=k))

            # 2. list items and removed text that can still be found on the page
            for t in terms:
                for s, e in t.find(after.norm):
                    if any(r.intersects(m) for r in after.rects(s, e) for m in plan.marked.get(pno, [])):
                        warns.append(Finding("kept-visible", f"page {n}: {t.text!r} is still visible where you "
                                                             "deleted its box", page=n, text=t.text))
                    else:
                        fails.append(Finding("not-boxed", f"page {n}: {t.text!r} is on the list but was not boxed in "
                                                          "the review copy (was the list changed after mark? run mark "
                                                          "again with --overwrite)", page=n, text=t.text))
            for t in extra:
                if t.find(after.norm):
                    warns.append(Finding("removed-elsewhere", f"page {n}: {t.text[:40]!r} is still visible here but "
                                                              "was removed elsewhere: if it should go, add it to the "
                                                              "list or box it", page=n, text=t.text))
            # all the text a tool can read, against what the page shows: TEXT_MEDIABOX_CLIP also leaves out text
            # cut off by a clipping path, which the search (text.FLAGS) never saw, so it was never boxed
            everywhere = normalize(page.get_text("text", flags=0, clip=pymupdf.INFINITE_RECT()))
            shown = normalize(page.get_text("text", flags=pymupdf.TEXT_MEDIABOX_CLIP))
            for t in terms + extra:
                if len(t.find(everywhere)) > len(t.find(shown)):
                    fails.append(Finding("hidden-text", f"page {n}: hidden text (cut off, or outside the visible "
                                                        f"page) contains {t.text[:40]!r}", page=n, text=t.text))

            # 3. nothing drawn is left inside an area blacked out on pictures or drawings
            if plan.visual[pno]:
                left = sum(1 for d in page.get_drawings()
                           if any(inside(pymupdf.Rect(d["rect"]), v, 0.5) for v in plan.visual[pno])
                           and not any(near(pymupdf.Rect(d["rect"]), a, 0.8) for a in plan.areas[pno]))
                if left:
                    fails.append(Finding("shapes-left", f"page {n}: {left} drawn shape(s) are still inside blacked-out "
                                                        "areas", page=n, count=left))

            # 4. nothing else changed
            b = plan.before[pno]
            if len(list(page.widgets())) != b["fields"]:
                fails.append(Finding("fields-changed", f"page {n}: the number of form fields changed", page=n))
            if sum(1 for _ in page.annots()) != b["comments"] and not drop_attachments:
                fails.append(Finding("comments-changed", f"page {n}: the number of comments changed", page=n))
            links_now += len(page.get_links())
            for w in text_fields(page):
                hit = next((t for t in terms if t.find(normalize(widget_value(w)))), None)
                name = w.field_name or ""
                if hit and (pno, name) in plan.marked_fields:
                    warns.append(Finding("field-kept", f"page {n}: form field '{w.field_name}' still contains "
                                                       f"{hit.text!r} (you deleted its box)",
                                         page=n, text=hit.text, name=name))
                elif hit:
                    fails.append(Finding("field-not-boxed", f"page {n}: form field '{w.field_name}' contains "
                                                            f"{hit.text!r} and was not boxed",
                                         page=n, text=hit.text, name=name))
            # 5. no text that no one sees: invisible, or outside the visible page
            k = invisible_count(page)
            if k:
                fails.append(Finding("invisible-text", f"page {n}: {k} invisible character(s) are still there",
                                     page=n, count=k))
            k = outside_count(page)
            if k:
                fails.append(Finding("outside-text", f"page {n}: {k} character(s) outside the visible page are "
                                                     "still there", page=n, count=k))
            if progress:
                progress("check", n, len(out))
        if links_now != sum(b["links"] for b in plan.before) - plan.links_removed:
            fails.append(Finding("links-changed", "the number of links changed unexpectedly"))

        # 6. the places outside the pages: nothing of the list or of what was removed, no metadata at all
        for where, text in hidden_texts(out):
            norm = normalize(text)
            hit = next((t for t in terms + extra if t.find(norm)), None)
            if hit:
                fails.append(Finding("hidden-place", f"{where} still contains {hit.text[:40]!r}", text=hit.text,
                                     name=where))
        for where, text in pdf_strings(out):             # every other string in the file
            norm = normalize(text)
            hit = next((t for t in terms if t.find(norm)), None)
            if hit:
                fails.append(Finding("hidden-place", f"{where} still contains {hit.text[:40]!r}", text=hit.text,
                                     name=where))
        if metadata_left(out):
            fails.append(Finding("metadata-left", "document properties or metadata (XMP, or a picture's EXIF) are "
                                                  "still in the file"))
        attached = out.embfile_count() + sum(1 for p in out for a in p.annots()
                                             if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT)
        if attached:
            fails.append(Finding("attachments", "files attached inside the PDF were not checked (add --drop-attachments "
                                                "to remove them)", count=attached))
        if has_xfa(out):
            fails.append(Finding("xfa-left", "XFA form data is still present"))
    finally:
        out.close()
    return list(dict.fromkeys(fails)), list(dict.fromkeys(warns))
