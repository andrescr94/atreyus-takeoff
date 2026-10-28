"""Tests for takeoff.match on sheet 1 (A1-111) of the Escondido plan.

Every count was verified by eye on data/sheets/A1-111/detections.png on 2026-10-27 (see the
comment next to each number). Change a number only after looking at the overlay again.
"""
import collections
import json
import pathlib

import pymupdf
import pytest

from takeoff import ingest, legend, match

SHEET = "A1-111"


@pytest.fixture(scope="module")
def sheet():
    return ingest.load_sheet(SHEET)


@pytest.fixture(scope="module")
def page(sheet):
    return pymupdf.open(str(sheet.pdf_path))[0]


@pytest.fixture(scope="module")
def detections(page, sheet):
    templates = legend.parse_legend(page, sheet.legend_frame)
    return match.detect(page, sheet, templates)


def _inside(bbox, frame):
    return frame.x0 <= bbox[0] and bbox[2] <= frame.x1 and frame.y0 <= bbox[1] and bbox[3] <= frame.y1


def _near(detections, x, y, tol=3.0):
    found = [d for d in detections if abs(d.bbox[0] - x) <= tol and abs(d.bbox[1] - y) <= tol]
    assert len(found) == 1, found
    return found[0]


def test_every_detection_is_inside_the_plan_frame(detections, sheet):
    assert detections
    assert all(_inside(d.bbox, sheet.plan_frame) for d in detections)
    assert all(d.evidence["method"] == "vector-signature" for d in detections)
    assert len({d.id for d in detections}) == len(detections)


def test_exactly_one_ceiling_fan_and_its_junction_box_is_not_counted(detections):
    fans = [d for d in detections if d.slug == "ceiling_fan"]
    assert len(fans) == 1
    fan = fans[0]
    # the fan is 114.8 pt across: hub + three blades (92 segments) merged with the lower blade (18)
    assert (fan.bbox[2] - fan.bbox[0], fan.bbox[3] - fan.bbox[1]) == pytest.approx((114.8, 114.8), abs=0.5)
    inside = [d for d in detections if d is not fan and _inside(d.bbox, ingest.Frame(*fan.bbox))]
    assert not [d for d in inside if d.family == "junction"]


def test_receptacle_next_to_gfi_word_is_receptacle_gfi(detections):
    # kitchen counter receptacle with a vertical "GFI" label on the wall, bbox x0,y0 = (1297, 269)
    d = _near(detections, 1297.0, 269.0)
    assert d.family == "receptacle"
    assert d.slug == "receptacle_gfi"
    assert [w["text"] for w in d.evidence["qualifier_words"]] == ["GFI"]


def test_switch_next_to_3d_word_is_switch_3way_dimmer(detections):
    d = [d for d in detections if d.family == "switch" and "3D" in [w["text"] for w in d.evidence["qualifier_words"]]]
    assert d, "no switch with a 3D label"
    assert all(x.slug == "switch_3way_dimmer" for x in d)


# Counts verified on 2026-10-27 by reading data/sheets/A1-111/detections.png quadrant by quadrant
# at 220 dpi against the drawing. Two devices are known misses (see test_known_misses_are_absent):
# the plain "$" on the kitchen sink wall at (973, 356), glued by endpoint union-find into the 71
# segment sink outline, and the kitchen "240V GFI" receptacle at (967, 426), glued to a cooktop
# burner circle.
VERIFIED_COUNTS = {
    "ceiling_fan": 1,  # bedroom/studio, four blades + J hub, counted once
    "exhaust_fan": 2,  # bath (quarter-filled circle) and kitchen (next to MW GFI, label leader pruned)
    "iaq_fan": 1,  # laundry, "IAQ" label
    "light_recessed": 7,  # kitchen 4 (two with a dashed-leg stub), living 2, bath 1
    "light_wall_exterior": 1,  # "EXT +84" at the entry
    "light_wall": 1,  # keyless light in the IN ATTIC callout: circle + ticks, no label; the pendant
    # row is the same strokes with a "P", so the nearest unqualified light row is the wall light
    "receptacle_afi": 9,  # laundry 1, bath/CL 3, bedroom 5, all "AFI"
    "receptacle_gfi": 6,  # "GFI +42"": kitchen 4, laundry 1, bath 1
    "receptacle_240v": 3,  # "WP GFI 240V" exterior, laundry "240V GFI", bath "240V GFI"
    "receptacle_gfi_wp": 2,  # "WP GFI" exterior, two walls
    "receptacle_gfi_dw": 1,  # "GFI DW" kitchen
    "receptacle_gfi_mw": 1,  # "MW GFI" kitchen
    "receptacle": 2,  # fan coil unit outlets: only an "FC" label next to them
    "smoke_detector": 1,  # "SD" bedroom
    "smoke_co": 1,  # "SD/MA" kitchen/dining
    "switch": 1,  # attic switch in the IN ATTIC callout, no label
    "switch_3way_dimmer": 4,  # "3D 3D" laundry door and "3D 3D" living entry
    "switch_dimmer": 2,  # "D" bath, "D" bedroom
    "switch_fan": 3,  # "F" kitchen, bath, bedroom
    "switch_vacancy": 1,  # "VS" bath
    "switch_sensor": 1,  # "A" living entry
}


def test_counts_per_slug_match_the_visual_count(detections):
    counts = collections.Counter(d.slug for d in detections)
    assert dict(counts) == VERIFIED_COUNTS
    assert len(detections) == sum(VERIFIED_COUNTS.values()) == 51


def test_known_misses_are_absent(detections):
    # documented misses, not hidden: fix the geometry grouping and these two assertions flip
    assert not [d for d in detections if abs(d.bbox[0] - 973) < 4 and abs(d.bbox[1] - 356) < 4]
    assert not [d for d in detections if abs(d.bbox[0] - 967) < 2 and abs(d.bbox[1] - 426) < 2]


def test_fan_coil_outlets_fall_back_to_the_family_base_with_a_note(detections):
    base = [d for d in detections if d.slug == "receptacle"]
    assert len(base) == 2
    for d in base:
        assert d.confidence < 0.75  # (1 - distance) * AMBIGUOUS_FACTOR
        assert any("no qualifier" in n for n in d.evidence["notes"])
        assert d.evidence["qualifier_words"] == []


def test_evidence_carries_the_template_and_the_words(detections):
    d = _near(detections, 1297.0, 269.0)  # the GFI receptacle checked above
    assert d.evidence["template_row"] == "L23"
    assert d.evidence["template_slug"] == "receptacle_gfi"
    assert d.evidence["signature_distance"] < match.THRESHOLD
    assert d.confidence == pytest.approx(1 - d.evidence["signature_distance"], abs=0.001)
    assert d.evidence["qualifier_words"][0]["bbox"][0] > 1290


def test_main_writes_json_and_overlay_and_prints_counts(capsys):
    assert match.main([SHEET]) == 0
    out = capsys.readouterr().out
    assert "A1-111: 51 detections" in out
    assert "  1 ceiling_fan" in out
    data = json.loads((pathlib.Path("data/sheets") / SHEET / "detections.json").read_text())
    assert len(data) == 51
    assert data[0]["evidence"]["method"] == "vector-signature"
    assert pymupdf.Pixmap(f"data/sheets/{SHEET}/detections.png").width > 1000
