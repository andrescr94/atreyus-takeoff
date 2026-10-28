"""Phase 1 (Ingest): split the plan set into sheets, locate the ELECTRICAL PLAN and LEGEND frames.

Expected numbers come from measuring plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf with
pymupdf (page.get_text("words") for titles, page.get_drawings() for the sheet grid lines) and
checking rendered clips by eye. Frames are the grid cell that holds the title, trimmed to the
side of the title where the content sits (see takeoff.ingest.find_frame).
"""

import pytest

from takeoff import ingest as ing

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"


@pytest.fixture(scope="module")
def sheets(tmp_path_factory):
    out = tmp_path_factory.mktemp("sheets")
    return ing.ingest(PDF, out_dir=out, dpi=36)


def test_four_sheets_with_sheet_ids(sheets):
    assert [s.id for s in sheets] == ["A1-111", "A2-111", "A3-111", "A4-111"]
    assert [s.page_index for s in sheets] == [0, 1, 2, 3]


def test_page_size_is_36_by_24_inches(sheets):
    for s in sheets:
        assert (s.width_pt, s.height_pt) == (2592.0, 1728.0)


def _close(frame, expected, tol=3.0):
    got = (frame.x0, frame.y0, frame.x1, frame.y1)
    return all(abs(g - e) <= tol for g, e in zip(got, expected)), got


def test_sheet_1_plan_frame_is_the_cell_above_the_title(sheets):
    # Sheet A1-111: grid lines at x=854.4 and x=1439.0, top border y=37.1; the title
    # "1 ELECTRICAL PLAN - PLAN 1" starts at y=845.7 and the drawing sits above it.
    # The brief estimated the drawing content itself at x 870-1400, y 90-830; the cell is wider.
    ok, got = _close(sheets[0].plan_frame, (854.4, 37.1, 1439.0, 845.7))
    assert ok, got


def test_sheet_1_legend_frame_is_the_cell_below_the_title(sheets):
    # Sheet A1-111: grid lines at x=1439.0 and x=1870.7, bottom y=901.3; the title "LEGEND"
    # ends at y=91.6 and the symbols sit below it (brief estimate: x 1440-1870, y 55-700).
    ok, got = _close(sheets[0].legend_frame, (1439.0, 91.6, 1870.7, 901.3))
    assert ok, got


# Measured with find_frame and checked by eye on rendered clips (each frame holds exactly the
# electrical plan with its ceiling fans, or exactly the legend symbol table).
MEASURED_FRAMES = {
    "A1-111": ((854.4, 37.1, 1439.0, 845.7), (1439.0, 91.6, 1870.7, 901.3)),
    "A2-111": ((143.9, 37.1, 1439.0, 532.3), (1439.0, 1092.7, 1870.7, 1692.0)),
    "A3-111": ((818.4, 37.1, 1439.0, 845.6), (1439.0, 91.0, 1870.7, 901.3)),
    "A4-111": ((1007.3, 37.1, 1870.7, 687.5), (1007.3, 802.2, 1439.0, 1414.4)),
}


def test_every_sheet_has_the_measured_plan_and_legend_frames(sheets):
    for s in sheets:
        plan, legend = MEASURED_FRAMES[s.id]
        ok, got = _close(s.plan_frame, plan)
        assert ok, (s.id, "plan", got)
        ok, got = _close(s.legend_frame, legend)
        assert ok, (s.id, "legend", got)


def test_frames_are_larger_than_a_small_detail_and_do_not_overlap(sheets):
    for s in sheets:
        for f in (s.plan_frame, s.legend_frame):
            assert f.x1 - f.x0 > 200 and f.y1 - f.y0 > 200, (s.id, f)
        p, g = s.plan_frame, s.legend_frame
        assert p.x1 <= g.x0 or g.x1 <= p.x0 or p.y1 <= g.y0 or g.y1 <= p.y0, (s.id, p, g)


def test_scale_is_quarter_inch_per_foot(sheets):
    # Every plan title reads SCALE: 1/4" = 1'-0", i.e. 0.25 in * 72 pt/in = 18 pt per foot.
    for s in sheets:
        assert s.scale_pt_per_ft == pytest.approx(18.0)
        assert s.scale_source == "text"


def test_read_scale_defaults_to_18_when_no_scale_text(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    assert ing.find_scale(page) is None
    assert ing.read_scale(page) == 18.0


def test_ingest_writes_one_pdf_png_and_json_per_sheet(sheets):
    import json

    import pymupdf

    for s in sheets:
        assert s.dpi == 36
        assert s.pdf_path.name == "page.pdf" and s.pdf_path.parent.name == s.id
        assert s.png_path.name == "page.png" and s.png_path.parent == s.pdf_path.parent
        assert len(pymupdf.open(s.pdf_path)) == 1
        pix = pymupdf.Pixmap(str(s.png_path))
        assert (pix.width, pix.height) == (1296, 864)  # 2592x1728 pt at 36 dpi
        meta = json.loads((s.pdf_path.parent / "sheet.json").read_text())
        assert meta["id"] == s.id
        assert meta["plan_frame"] == {"x0": s.plan_frame.x0, "y0": s.plan_frame.y0, "x1": s.plan_frame.x1, "y1": s.plan_frame.y1}
        assert meta["scale_pt_per_ft"] == 18.0


def test_load_sheet_round_trips(sheets):
    for s in sheets:
        assert ing.load_sheet(s.id, out_dir=s.pdf_path.parent.parent) == s


def test_main_prints_one_line_per_sheet_with_both_frames(tmp_path, capsys):
    assert ing.main([PDF, "--out", str(tmp_path), "--dpi", "36"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("A1-111")
    assert "plan=(854.4, 37.1, 1439.0, 845.7)" in lines[0]
    assert "legend=(1439.0, 91.6, 1870.7, 901.3)" in lines[0]
    assert "18.0 pt/ft" in lines[0]
