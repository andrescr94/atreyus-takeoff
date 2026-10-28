"""Ingest on scans: the same sheets as one 150 DPI greyscale image (takeoff.evaluate.scan_copy), no text
layer, no strokes. Words come from OCR and grid lines from the image; the frames must land where the vector
sheets put them.

OCR takes about 15 s per sheet, so the four scans are ingested once per test session.
"""

import pymupdf
import pytest

from takeoff import ingest as ing
from takeoff.evaluate import scan_copy

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"
TOLERANCE_PT = 6.0


@pytest.fixture(scope="session")
def pairs(tmp_path_factory):
    """(vector sheet, scan sheet) for every sheet of the plan set."""
    vector = ing.ingest(PDF, out_dir=tmp_path_factory.mktemp("sheets"), dpi=36)
    return [(v, ing.ingest_page(scan_copy(v).pdf_path, dpi=36)) for v in vector]


def test_scan_pages_have_no_text_and_no_strokes(pairs):
    for _, s in pairs:
        page = pymupdf.open(s.pdf_path)[0]
        assert s.pdf_path.name == "page_scan.pdf"
        assert not page.get_text("text").strip() and not page.get_drawings()


def test_scan_sheet_ids_are_read_by_ocr(pairs):
    assert [s.id for _, s in pairs] == ["A1-111", "A2-111", "A3-111", "A4-111"]


def test_scan_frames_match_the_vector_frames(pairs):
    # Measured: grid-line edges within 0.2 pt; the plan frame's bottom (the top of the OCR'd title box)
    # sits 3-4.3 pt above the text-layer title, the legend frame's top within 1.2 pt.
    for v, s in pairs:
        for name in ("plan_frame", "legend_frame"):
            got, want = getattr(s, name), getattr(v, name)
            errors = [abs(getattr(got, k) - getattr(want, k)) for k in ("x0", "y0", "x1", "y1")]
            assert max(errors) <= TOLERANCE_PT, (v.id, name, got, want)


def test_scan_scale_is_quarter_inch_per_foot(pairs):
    for _, s in pairs:
        assert s.scale_pt_per_ft == pytest.approx(18.0)
        assert s.scale_source in ("text", "default")


def test_ingest_page_reads_in_place_and_renders_next_to_the_pdf(pairs):
    for _, s in pairs:
        assert s.page_index == 0 and s.dpi == 36
        assert s.png_path == s.pdf_path.with_suffix(".png")
        pix = pymupdf.Pixmap(str(s.png_path))
        assert (pix.width, pix.height) == (1296, 864)


@pytest.mark.parametrize("text, title, expected", [
    ("ELECTRICALPLAN-PLAN 1", "ELECTRICAL PLAN", True),
    ("ELECTRICAL PLAN - PLAN 2", "ELECTRICAL PLAN", True),
    ("Electrical  Plan", "ELECTRICAL PLAN", True),
    ("ELECTRIGAL PLAN - PLAN 3", "ELECTRICAL PLAN", True),  # one misread letter
    ("ELECTRICAL PLANS", "ELECTRICAL PLAN", False),
    ("ELECTRICALNOTES", "ELECTRICAL PLAN", False),
    ("ELECTRICAL", "ELECTRICAL PLAN", False),
    ("MECHANICAL PLAN - PLAN 1", "ELECTRICAL PLAN", False),
    ("LEGEND", "LEGEND", True),
    ("LEG END", "LEGEND", True),
    ("LEGENO", "LEGEND", False),  # too short to forgive a letter
    ("FLOORFINISHLEGEND", "LEGEND", False),
])
def test_ocr_title_matching(text, title, expected):
    assert ing._starts_with_title(text, title) is expected
