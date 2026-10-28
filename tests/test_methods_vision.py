"""The model-free parts of the vision methods: coordinate mapping, plan cropping, the consensus merge."""
import numpy as np
import pymupdf
import pytest

from takeoff.methods import vision


def test_fractions_map_to_points_over_the_clip():
    clip = pymupdf.Rect(100, 50, 500, 250)
    assert vision.to_points(0, 0, clip) == (100, 50)
    assert vision.to_points(1, 1, clip) == (500, 250)
    assert vision.to_points(0.25, 0.5, clip) == (200, 150)


def test_placed_drops_items_without_coordinates_and_maps_unknown_types_to_other():
    clip = pymupdf.Rect(0, 0, 200, 100)
    items = [{"type": "switch", "x": 0.5, "y": 0.5, "confidence": 0.9},
             {"type": "toaster", "x": "0.1", "y": 0.2},
             {"type": "switch", "x": None, "y": 0.2}]
    found = vision.placed(items, clip)
    assert [(d["slug"], d["cx"], d["cy"], d["confidence"]) for d in found] == [
        ("switch", 100, 50, 0.9), ("other", 20, 20, 0.5)]


def test_content_bbox_ignores_the_frame_border_and_full_width_lines():
    gray = np.full((200, 300), 255, dtype=np.uint8)
    gray[0:2, :] = gray[-2:, :] = 0  # frame border
    gray[:, 0:2] = gray[:, -2:] = 0
    gray[150, 5:295] = 0  # a full-width divider inside the frame
    gray[40:60, 100:180] = 0  # the drawing
    gray[100:120, 120:130] = 230  # a light watermark is not drawing
    assert vision.content_bbox(gray) == (100, 40, 180, 60)


def test_content_bbox_of_a_blank_image_is_none():
    assert vision.content_bbox(np.full((50, 50), 255, dtype=np.uint8)) is None


def test_tight_rect_finds_the_drawing_inside_a_bordered_frame():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    frame = pymupdf.Rect(50, 50, 550, 350)
    page.draw_rect(frame, color=(0, 0, 0), width=1.5)
    page.draw_rect(pymupdf.Rect(200, 150, 300, 220), color=(0, 0, 0), fill=(0, 0, 0))
    tight = vision.tight_rect(page, frame, margin=15)
    assert tight.x0 == pytest.approx(185, abs=3) and tight.y0 == pytest.approx(135, abs=3)
    assert tight.x1 == pytest.approx(315, abs=3) and tight.y1 == pytest.approx(235, abs=3)


def _d(slug, cx, cy):
    return {"slug": slug, "cx": cx, "cy": cy, "item": {"type": slug}, "confidence": 0.8}


def test_merge_keeps_everything_and_grades_agreement():
    run0 = [_d("switch", 100, 100), _d("receptacle", 200, 200), _d("light", 300, 300)]
    run1 = [_d("switch", 105, 103), _d("receptacle_gfi", 202, 198), _d("smoke_detector", 400, 400)]
    by_slug = {d["slug"]: d for d in vision.merge(run0, run1)}
    assert set(by_slug) == {"switch", "receptacle", "light", "smoke_detector"}
    assert by_slug["switch"]["confidence"] == 0.95
    assert by_slug["switch"]["evidence"] == {"agreement": "2/2"}
    assert by_slug["receptacle"]["confidence"] == 0.5
    assert by_slug["receptacle"]["evidence"] == {"agreement": "type differs", "alternatives": ["receptacle_gfi"]}
    for only in ("light", "smoke_detector"):
        assert by_slug[only]["confidence"] == 0.4
        assert by_slug[only]["evidence"] == {"agreement": "1/2"}


def test_merge_pairs_one_to_one_nearest_first():
    run0 = [_d("switch", 100, 100), _d("switch", 108, 100)]
    run1 = [_d("switch", 107, 100)]
    merged = vision.merge(run0, run1)
    agreed = [d for d in merged if d["evidence"]["agreement"] == "2/2"]
    assert len(merged) == 2 and len(agreed) == 1 and agreed[0]["cx"] == 108


def test_methods_are_registered_on_their_rungs():
    assert {name: m.rung for name, m in vision.METHODS.items()} == {
        "vision-page": 1, "vision-crop": 5, "vision-consensus": 6}
    assert all(m.needs == "an image" for m in vision.METHODS.values())
