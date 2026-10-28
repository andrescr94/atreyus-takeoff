"""Golden tests for takeoff.geometry on sheet 1 of the Escondido plan.

Every number below was measured first-hand on 2026-10-27 with takeoff.geometry (pymupdf 2.0,
`uv run python -m takeoff.geometry plans/... 0 "870,90,1400,830" data/geometry_p1.png` and scratch
scripts). An earlier probe script reported 1748 segments and
193 components, but it pre-filtered drawings with Rect.intersects(), which returns False for the
degenerate rect of any horizontal or vertical line, so every axis-aligned stroke was silently
dropped. geometry.extract keeps them, hence the larger counts here. Change numbers only with a
new measurement.
"""
import pymupdf
import pytest

from takeoff import geometry

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"
PLAN_CLIP = pymupdf.Rect(870, 90, 1400, 830)  # sheet 1 electrical plan, PDF points
SEGMENTS = 4133  # thin (< 0.5 pt) line and rect-edge segments touching PLAN_CLIP
COMPONENTS = 419  # union-find groups at tol 0.6
SYMBOL_SIZED = 133  # symbol_sized(components) with defaults (3..120 pt, >= 4 segments)
RECEP_VS_SWITCH = 0.461  # signature_distance(receptacle, switch): measured 0.4608


@pytest.fixture(scope="module")
def geo():
    doc = pymupdf.open(PDF)
    return geometry.extract(doc[0], PLAN_CLIP)


def _at(geo, x, y, tol=0.5):
    """Components whose bbox top-left is within tol of (x, y)."""
    return [c for c in geo.components if abs(c.bbox[0] - x) <= tol and abs(c.bbox[1] - y) <= tol]


def _one(geo, x, y, n_segments=None):
    found = _at(geo, x, y)
    if n_segments is not None:
        found = [c for c in found if c.n_segments == n_segments]
    assert len(found) == 1, found
    return found[0]


def test_segment_and_component_counts(geo):
    assert sum(c.n_segments for c in geo.components) == SEGMENTS
    assert len(geo.components) == COMPONENTS


def test_bboxes_are_in_page_coordinates(geo):
    # the clip starts at x=870; clip-relative bboxes would all start near 0
    assert all(c.bbox[0] >= PLAN_CLIP.x0 - 1 and c.bbox[1] >= PLAN_CLIP.y0 - 1 for c in geo.components)
    assert all(w.bbox[0] >= PLAN_CLIP.x0 - 1 for w in geo.words)


def test_recessed_light_circle_is_one_component(geo):
    # Recessed downlight (12-gon circle; its 9 pt square is a separate component) next to the "GFI +42"" label, bbox
    # (1116.00, 404.16, 1124.76, 413.16): circle drawn as two 6-segment half arcs each overdrawn
    # 4 times (48 segments) + 2 joining verticals + 6 tick segments = 56 segments, 8.8 x 9.0 pt,
    # total stroke length 111.69. (Its 9.0 pt square is a separate 4-segment component: the
    # square's corners do not touch the arc endpoints.)
    c = _one(geo, 1116.0, 404.2, 56)
    assert c.n_segments == 56
    assert c.bbox == pytest.approx((1116.00, 404.16, 1124.76, 413.16), abs=0.01)
    assert c.total_length == pytest.approx(111.69, abs=0.02)


def test_words_are_horizontal_and_exclude_watermark(geo):
    # page.get_text("dict") on 2026-10-27: 14 GFI tokens in the clip, 7 on lines with dir (1,0)
    # and 7 on wall labels with dir (0,-1); only the horizontal 7 are kept. One of them is at
    # (1116.36, 231.77, 1127.19, 237.89). The watermark line "FOR USE IN THE CITY OF ESCONDIDO"
    # has dir (0.866, -0.5) and its tokens CITY / OF / ESCONDIDO fall inside the clip.
    gfi = [w for w in geo.words if w.text == "GFI"]
    assert len(gfi) == 7
    assert any(w.bbox == pytest.approx((1116.36, 231.77, 1127.19, 237.89), abs=0.01) for w in gfi)
    texts = {w.text for w in geo.words}
    assert "KITCHEN" in texts
    assert not texts & {"ESCONDIDO", "CITY", "FOR", "USE"}
    assert all(w.dir == (1.0, 0.0) for w in geo.words)
    assert len(geo.words) == 49


def test_signature_matches_identical_symbols_and_separates_different_ones(geo):
    # Positions (bbox x0, y0) measured 2026-10-27:
    #   recessed lights (1116.0, 404.2) and (1116.0, 518.0): both 56 segs, 8.8x9.0, length 111.7
    #   switches     (1204.3, 274.3) and (1204.3, 490.6): both 15 segs, 10.1x5.3, length 20.91
    #   (the switch at (1204.3, 284.0) has one segment angle on a bin edge: distance 0.014)
    #   rotated switch (1076.5, 579.4): 15 segs, 5.3x10.1 (on a horizontal wall), length 20.97
    recep_a, recep_b = _one(geo, 1116.0, 404.2, 56), _one(geo, 1116.0, 518.0, 56)
    switch_a, switch_b = _one(geo, 1204.3, 274.3), _one(geo, 1204.3, 490.6)
    switch_rot = _one(geo, 1076.5, 579.4)
    assert recep_a.signature == geometry.signature(recep_a)
    assert recep_a.signature[:4] == (56, 8.8, 9.0, 111.7)
    assert switch_a.signature[:4] == (15, 5.3, 10.1, 20.9)
    assert geometry.signature_distance(recep_a, recep_b) == 0.0
    assert geometry.signature_distance(switch_a, switch_b) == 0.0
    # same symbol rotated 90 degrees: only float noise in the exploded segment lengths remains
    assert geometry.signature_distance(switch_a, switch_rot) < 0.01
    d = geometry.signature_distance(recep_a, switch_a)
    assert d == pytest.approx(RECEP_VS_SWITCH, abs=0.005)
    assert d > 0.2


def test_canonical_histogram_is_rotation_and_mirror_invariant():
    h = (0.5, 0.1, 0.0, 0.0, 0.2, 0.0, 0.0, 0.2)
    rot90 = tuple(h[(i + 4) % 8] for i in range(8))
    mirror = tuple(h[(-i) % 8] for i in range(8))
    assert geometry.canonical_histogram(h) == geometry.canonical_histogram(rot90)
    assert geometry.canonical_histogram(h) == geometry.canonical_histogram(mirror)
    assert geometry.canonical_histogram(h) == min(h, rot90, mirror, tuple(rot90[(-i) % 8] for i in range(8)))


def test_symbol_sized_admits_fan_and_symbols_but_not_walls(geo):
    # The ceiling fan on sheet 1 is 114.8 pt across: hub + three blades are one 92-segment
    # component with bbox (1219.2, 448.8, 1334.0, 513.2); the fourth blade is a separate
    # 18-segment component at (1269.6, 510.7, 1283.6, 563.6). So the default max side is 120.
    fan = _one(geo, 1219.2, 448.8)
    assert fan.n_segments == 92
    assert (fan.bbox[2] - fan.bbox[0], fan.bbox[3] - fan.bbox[1]) == pytest.approx((114.8, 64.4), abs=0.1)
    kept = geometry.symbol_sized(geo.components)
    assert fan in kept
    assert _one(geo, 1269.6, 510.7) in kept  # fourth blade
    assert _one(geo, 1116.0, 404.2, 56) in kept  # recessed light (circle), first labelled receptacle in error, corrected 2026-10-27
    assert _one(geo, 1204.3, 274.3) in kept  # switch
    assert len(kept) == SYMBOL_SIZED
    for c in kept:
        w, h = c.bbox[2] - c.bbox[0], c.bbox[3] - c.bbox[1]
        assert 3 <= min(w, h) and max(w, h) <= 120 and c.n_segments >= 4
    assert fan not in geometry.symbol_sized(geo.components, max_pt=70)
    assert len(geometry.symbol_sized(geo.components, max_pt=70)) == 127


def test_main_prints_counts_and_writes_overlay(tmp_path, capsys):
    out = tmp_path / "geometry_p1.png"
    rc = geometry.main([PDF, "0", "870,90,1400,830", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert f"segments: {SEGMENTS}" in text
    assert f"components: {COMPONENTS}" in text
    assert f"symbol-sized: {SYMBOL_SIZED}" in text
    assert "(15, 5.3, 10.1, 20.9" in text  # the switch signature group is listed
    pix = pymupdf.Pixmap(str(out))
    # clip is 530 x 740 pt rendered at 150 dpi (pymupdf rounds the pixel size up: 1105 x 1542)
    assert abs(pix.width - 530 * 150 / 72) <= 1 and abs(pix.height - 740 * 150 / 72) <= 1
