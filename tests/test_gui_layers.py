"""What is drawn over the pages in each view. Run from the repository folder:  uv run pytest"""
from helpers import sample
from pdfredact.gui.layers import ResultLayer, ReviewLayer
from pdfredact.gui.session import Box, Session


def test_review_layer_marks_pages_to_check_by_eye(tmp_path):
    path = sample(tmp_path, "scan_no_text.pdf")
    s = Session(str(path), path.read_bytes())
    layer = ReviewLayer(s, lambda: None)
    assert layer.notice(0).startswith("Check by eye: this page has no text, only a picture")
    assert ResultLayer(s).notice(0) == ""
    s.add_drawn(Box(0, (10.0, 10.0, 50.0, 50.0), "user"))
    assert [shape.style for shape in layer.shapes(0)] == ["drawn"]
    s.close()
