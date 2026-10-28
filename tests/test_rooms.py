"""Phase 5 (Rooms): room regions from the grey wall fills on the Escondido plan, sheet 1.

Every golden below was measured first-hand on 2026-10-27 against
plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf with pymupdf 2.0: wall rectangles come from
page.get_drawings() (fill (0.754, 0.754, 0.754), listed with a scratch script), room labels from
page.get_text("dict") (Arial,Bold 9.6 pt spans), and the points were chosen from those numbers and
checked by eye on the overlay data/rooms_A1-111.png. Change a number only with a new measurement.
"""

import pymupdf
import pytest

from takeoff import ingest, rooms
from takeoff.rooms import EXTERIOR

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"


@pytest.fixture(scope="module")
def page():
    return pymupdf.open(PDF)[0]


@pytest.fixture(scope="module")
def plan_frame(page):
    f = ingest.find_frame(page, "ELECTRICAL PLAN")  # (854.4, 37.1, 1439.0, 845.7), see test_ingest
    return pymupdf.Rect(f.x0, f.y0, f.x1, f.y1)


@pytest.fixture(scope="module")
def sheets_dir(tmp_path_factory):
    """Ingest output in a temp folder so the test does not depend on the gitignored data/ tree."""
    out = tmp_path_factory.mktemp("sheets")
    ingest.ingest(PDF, out_dir=out, dpi=36)
    return out


@pytest.fixture(scope="module")
def sheet1_rooms(page, plan_frame):
    return rooms.find_rooms(page, plan_frame)


def test_sheet_1_has_every_labelled_room(sheet1_rooms):
    # Arial,Bold 9.6 pt spans inside the plan frame on sheet 1 (measured 2026-10-27):
    # BEDROOM / STUDIO, LIVING, KITCHEN / DINING, BATH, CL (x2), LNDRY; "54" and "A-901" are the
    # detail callout above the plan, not rooms.
    names = {r.name for r in sheet1_rooms}
    assert {"KITCHEN / DINING", "LIVING", "BATH", "BEDROOM / STUDIO", "LNDRY"} <= names
    assert "Exterior" in names
    assert not names & {"54", "A-901"}


def test_point_inside_the_bath_is_assigned_inside(sheet1_rooms):
    # (1320, 300): sheet 1 BATH is bounded by the wall rectangles x 1194.7-1201.0 (left),
    # 1342.9-1352.2 (right), y 206.8-216.0 (top), 368.8-373.9 (bottom, shared with the CL below);
    # its unlabelled top-right closet is walled off at y <= 270 (x 1292-1344). So 1320 is 23 pt
    # inside the right wall and 300 is 30 pt below that closet: open bath floor, next to the tub.
    assert rooms.assign_room((1320.0, 300.0), sheet1_rooms) == ("BATH", "inside")


def test_point_outside_the_building_is_exterior(sheet1_rooms):
    # (900, 150): inside the plan frame (x from 854.4, y from 37.1) but above-left of the exterior
    # wall ring, whose outer edge is x 955.8-1354.2, y 204.7-603.1 (union of the grey rectangles).
    assert rooms.assign_room((900.0, 150.0), sheet1_rooms) == (EXTERIOR, "inside")
    # a point outside the plan frame altogether is Exterior too
    assert rooms.assign_room((10.0, 10.0), sheet1_rooms) == (EXTERIOR, "inside")


def test_point_on_the_wall_between_bath_and_closet_is_nudged(sheet1_rooms):
    # (1270, 371.5): the centre line of the wall rectangle (1200.0, 368.8, 1343.9, 373.9) that
    # separates BATH (above) from the CL below it; wall-mounted switches sit on this fill.
    name, how = rooms.assign_room((1270.0, 371.5), sheet1_rooms)
    assert how == "nudged"
    assert name in {"BATH", "CL"}
    # a bbox is assigned by its centre: the 9 x 9 pt box around the same point
    assert rooms.assign_room((1265.5, 367.0, 1274.5, 376.0), sheet1_rooms) == (name, "nudged")


def test_point_in_the_unlabelled_bath_closet_falls_back_to_nearest_label(sheet1_rooms):
    # (1320, 240): inside the small unlabelled closet in the bath's top-right corner, walled by
    # x 1292.2-1298.4 and y 262.8-270.0 (its region bbox is (1299.9, 217.1, 1340.4, 262.1));
    # the nearest labelled room pixel is more than 12 pt away, so the fallback picks the closest
    # label centre, BATH at (1267.1, 325.2) vs LNDRY at (1117.9, 259.4).
    assert rooms.assign_room((1320.0, 240.0), sheet1_rooms) == ("BATH", "nearest-label")


def test_main_prints_the_rooms_and_writes_the_overlay(sheets_dir, tmp_path, capsys):
    out = tmp_path / "rooms_A1-111.png"
    assert rooms.main(["A1-111", "--sheets", str(sheets_dir), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    for name in ("KITCHEN / DINING", "LIVING", "BATH", "BEDROOM / STUDIO", "LNDRY", "CL", EXTERIOR):
        assert name in text
    pix = pymupdf.Pixmap(str(out))
    # the sheet 1 plan frame is 584.6 x 808.7 pt, rendered at 100 dpi (pymupdf rounds up)
    assert abs(pix.width - 584.6 * 100 / 72) <= 2 and abs(pix.height - 808.7 * 100 / 72) <= 2


def test_render_rooms_writes_a_png_of_the_plan_frame(page, plan_frame, sheet1_rooms, tmp_path):
    out = tmp_path / "overlay.png"
    rooms.render_rooms(page, plan_frame, sheet1_rooms, out, dpi=50)
    pix = pymupdf.Pixmap(str(out))
    assert abs(pix.width - plan_frame.width * 50 / 72) <= 2
