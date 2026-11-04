"""Make fake sample PDFs and a list to try the tool on. Every name, number and address is invented.

    python samples/make_samples.py      ->  samples/files/*.pdf and samples/files/list.txt
"""
import json
import os

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "files")

LIST = ["Geneviève Côté-Tremblay", "Côté-Tremblay", "Geneviève", "Tremblay", "046 454 286",
        "1234, rue des Érables", "H2X 1Y4", "514 555-0199", "genevieve.cote@example.com",
        "Marc-André Bélanger", "7781-2245"]


def write_lines(page, lines, x=60, y=80, step=20, size=11, **kw):
    for i, line in enumerate(lines):
        page.insert_text((x, y + i * step), line, fontsize=size, fontname="helv", **kw)


def t4_slip(path):
    doc = pymupdf.open()
    write_lines(doc.new_page(), [
        "FAKE SAMPLE - T4 2025 - Statement of Remuneration Paid",
        "Employer's name: Acme Widgets Inc.",
        "Employer's account number: 123456789RP0001",
        "Employee's name and address:",
        "TREMBLAY, GENEVIÈVE",
        "1234, rue des Érables, Montréal QC  H2X 1Y4",
        "Social insurance number: 0 4 6   4 5 4   2 8 6",        # spaced like boxed form digits
        "14 Employment income: 64 210.00",
        "22 Income tax deducted: 9 870.55"])
    doc.set_metadata({"title": "T4 2025 - Tremblay Genevieve", "author": "Acme Widgets Inc."})
    doc.save(path)


def letter_fr(path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    write_lines(p1, [
        "Montréal, le 3 mars 2026",
        "Madame Geneviève Côté-Tremblay",
        "1234, rue des Érables",
        "Montréal (Québec) H2X 1Y4",
        "Objet : Avis de cotisation 2025",
        "Madame Côté-Tremblay,",
        "Nous avons examiné votre déclaration de revenus. Votre numéro d'assurance sociale",
        "(NAS : 046-454-286) figure sur l'avis ci-joint. Pour toute question, écrivez à",
        "genevieve.cote@example.com ou téléphonez au 514 555-0199.",
        "Revenu total (ligne 15000) : 64 210,00 $",
        "Veuillez agréer, Madame, nos salutations distinguées."])
    email = p1.search_for("genevieve.cote@example.com")[0]
    p1.insert_link({"kind": pymupdf.LINK_URI, "from": email, "uri": "mailto:genevieve.cote@example.com"})
    write_lines(doc.new_page(), [
        "Annexe - Renseignements du conjoint",
        "Conjoint : Marc-André Bélanger",
        "Compte bancaire : 7781-2245",
        "Adresse de correspondance :",
        "Geneviève",
        "Côté-Tremblay",
        "1234, rue des Érables"])
    doc.set_metadata({"title": "Avis - Geneviève Côté-Tremblay", "author": "Marc-André Bélanger",
                      "subject": "NAS 046454286"})
    doc.set_toc([[1, "Lettre à Geneviève Côté-Tremblay", 1], [1, "Annexe", 2]])
    doc.save(path)


def form_fillable(path):
    doc = pymupdf.open()
    page = doc.new_page()
    write_lines(page, ["FAKE SAMPLE - Formulaire / Form", "Nom / Name :", "NAS / SIN :", "Ville / City :"], y=80, step=30)
    for name, value, y in (("name", "Geneviève Côté-Tremblay", 112), ("sin", "046454286", 142), ("city", "Montréal", 172)):
        w = pymupdf.Widget()
        w.field_type, w.field_name, w.field_value = pymupdf.PDF_WIDGET_TYPE_TEXT, name, value
        w.rect, w.text_fontsize = pymupdf.Rect(160, y - 14, 420, y + 4), 10
        page.add_widget(w)
    note = page.add_text_annot((450, 60), "Rappeler Marc-André Bélanger avant le 30 avril")
    note.set_info(title="Geneviève Côté-Tremblay")
    note.update()
    doc.save(path)


SCAN_LINES = ["RELEVÉ 1 - FAKE SAMPLE", "Bénéficiaire : Geneviève Côté-Tremblay", "NAS : 046 454 286",
              "Montant : 1 250,00 $"]


def _scan_page(doc):
    """A page that is only a picture of text, like a scan."""
    src = pymupdf.open()
    write_lines(src.new_page(), SCAN_LINES, size=12)
    pix = src[0].get_pixmap(dpi=150)
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_image(page.rect, pixmap=pix)
    return page


def scan_with_text_layer(path):
    doc = pymupdf.open()
    write_lines(_scan_page(doc), SCAN_LINES, size=12, render_mode=3)   # invisible text over the picture (scanner OCR)
    doc.save(path)


def scan_no_text(path):
    doc = pymupdf.open()
    _scan_page(doc)
    doc.save(path)


def rotated_page(path):
    doc = pymupdf.open()
    page = doc.new_page()
    write_lines(page, ["FAKE SAMPLE - page shown sideways", "Client: Geneviève Côté-Tremblay", "SIN: 046 454 286",
                       "Province: Quebec"])
    page.set_rotation(90)
    doc.save(path)


# file -> (maker, ordinary text that must still be there after redaction)
SAMPLES = {
    "t4_slip.pdf": (t4_slip, ["Statement of Remuneration Paid", "Acme Widgets", "123456789RP0001", "64 210.00"]),
    "letter_fr.pdf": (letter_fr, ["Avis de cotisation 2025", "Nous avons examiné", "64 210,00", "Conjoint"]),
    "form_fillable.pdf": (form_fillable, ["Nom / Name", "Ville / City"]),
    "scan_with_text_layer.pdf": (scan_with_text_layer, ["Montant"]),
    "scan_no_text.pdf": (scan_no_text, []),
    "rotated_page.pdf": (rotated_page, ["page shown sideways", "Province: Quebec"]),
}


def make(out_dir=OUT):
    os.makedirs(out_dir, exist_ok=True)
    for name, (maker, _) in SAMPLES.items():
        maker(os.path.join(out_dir, name))
    with open(os.path.join(out_dir, "list.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(LIST) + "\n")
    with open(os.path.join(out_dir, "truth.json"), "w", encoding="utf-8") as fh:
        json.dump({name: stay for name, (_, stay) in SAMPLES.items()}, fh, ensure_ascii=False, indent=1)
    return out_dir


if __name__ == "__main__":
    print(f"made {len(SAMPLES)} fake PDFs and list.txt in {make()}")
