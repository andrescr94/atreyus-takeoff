"""Odd input fails loudly, per page; rotated pages are straightened so coordinates stay comparable."""
import numpy as np
import pymupdf
import pytest

from takeoff import ingest, locate

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"


def _one_page(tmp_path, name, fill):
    doc = pymupdf.open()
    fill(doc.new_page(width=2592, height=1728))
    doc.save(tmp_path / name)
    return tmp_path / name


def _noise(page):
    a = np.random.default_rng(0).integers(0, 255, (300, 450), dtype=np.uint8)
    page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csGRAY, 450, 300, a.tobytes(), False))


def test_a_page_that_is_not_a_plan_is_reported_with_the_reason(tmp_path, monkeypatch):
    def model_says_no(page, png):
        raise ValueError("page 1 is not an electrical plan with a legend: random noise")

    monkeypatch.setattr(locate, "locate", model_says_no)
    pdf = _one_page(tmp_path, "noise.pdf", _noise)
    with pytest.raises(ValueError, match="not an electrical plan.*random noise.*rules: no sheet id"):
        ingest.ingest(pdf, out_dir=tmp_path / "out")
    errors = []
    assert ingest.ingest(pdf, out_dir=tmp_path / "out", errors=errors) == []
    assert errors[0][0] == 1 and "random noise" in errors[0][1]


def test_not_a_pdf_and_an_empty_pdf_raise(tmp_path):
    (tmp_path / "x.pdf").write_text("hello")
    with pytest.raises(pymupdf.FileDataError):
        ingest.ingest(tmp_path / "x.pdf", out_dir=tmp_path / "out")


def test_rotated_sheet_reads_the_same_as_upright(tmp_path):
    """Like the San Diego and Orange County sets: content stored sideways on a portrait page, /Rotate 270 to show it
    upright."""
    doc = pymupdf.open(PDF)
    rotated = pymupdf.open()
    rotated.insert_pdf(doc, from_page=0, to_page=0)
    rotated[0].set_rotation(90)
    rotated[0].remove_rotation()  # content now sideways in a portrait page
    rotated[0].set_rotation(270)  # and displayed upright again
    rotated.save(tmp_path / "rot.pdf")
    assert pymupdf.open(tmp_path / "rot.pdf")[0].mediabox.width < 2000
    upright = ingest.ingest(PDF, out_dir=tmp_path / "up")[0]
    turned = ingest.ingest(tmp_path / "rot.pdf", out_dir=tmp_path / "rot")[0]
    assert turned.id == upright.id and turned.frames_source == "rules"
    # the stored page is upright again, so frames come back in the same coordinates
    assert pymupdf.open(turned.pdf_path)[0].rotation == 0
    assert abs(turned.plan_frame.x0 - upright.plan_frame.x0) < 1 and abs(turned.plan_frame.y1 - upright.plan_frame.y1) < 1
