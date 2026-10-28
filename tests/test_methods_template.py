"""Rung 3, template: rotation handling and NMS on a synthetic image, plus the ceiling fan on A1-111."""
import math

import cv2
import numpy as np
import pymupdf
import pytest

from takeoff import legend
from takeoff.ingest import load_sheet
from takeoff.methods import rect
from takeoff.methods import template as T


def glyph() -> np.ndarray:
    """An asymmetric stamp (circle on a stem with a foot to the right): all 8 orientations differ."""
    img = np.full((40, 26), 255, np.uint8)
    cv2.circle(img, (12, 10), 7, 0, 1)
    cv2.line(img, (12, 17), (12, 34), 0, 1)
    cv2.line(img, (12, 34), (22, 34), 0, 1)
    return img


def page_with(stamp_img: np.ndarray, x: int, y: int) -> np.ndarray:
    page = np.full((200, 240), 255, np.uint8)
    cv2.rectangle(page, (150, 20), (230, 180), 0, 1)  # unrelated drawing
    h, w = stamp_img.shape
    page[y : y + h, x : x + w] = stamp_img
    return page


def test_all_eight_orientations_of_an_asymmetric_glyph():
    assert len(T.orientations(glyph())) == 8


def test_symmetric_glyph_is_searched_once():
    img = np.full((31, 31), 255, np.uint8)
    cv2.circle(img, (15, 15), 10, 0, 1)
    assert len(T.orientations(img)) == 1


@pytest.mark.parametrize(
    "turns, mirrored", [(0, False), (1, False), (2, False), (3, False), (1, True)]
)
def test_finds_the_glyph_turned_and_mirrored(turns, mirrored):
    g = glyph()
    placed = np.rot90(cv2.flip(g, 1) if mirrored else g, -turns).copy()
    h, w = placed.shape
    page = page_with(placed, 40, 60)
    stamp = T.Stamp("thing", "L99", g)
    best = T.nms(T.search(page, stamp, 0.9, dpi=72))  # dpi 72: one pixel is one point
    assert len(best) == 1
    m = best[0]
    assert math.dist((m.x, m.y), (40 + w / 2, 60 + h / 2)) <= 1
    assert (m.rotation, m.mirrored) == (90 * turns, mirrored)
    assert m.score > 0.99


def test_nms_keeps_the_best_score_within_the_radius():
    s = T.Stamp("a", "L1", glyph())
    ms = [
        T.Match(0.70, 100, 100, s, 0, False),
        T.Match(0.90, 103, 102, s, 90, False),  # same spot, better score, other rotation
        T.Match(0.80, 100, 110, s, 0, False),  # 8 pt away: a second device
    ]
    kept = T.nms(ms, radius=6)
    assert [(m.score, m.rotation) for m in kept] == [(0.90, 90), (0.80, 0)]


@pytest.fixture(scope="module")
def a1():
    sheet = load_sheet("A1-111")
    page = pymupdf.open(str(sheet.pdf_path))[0]
    return sheet, page, T.stamps(legend.parse_legend(page, sheet.legend_frame), page)


def test_same_glyph_rows_collapse(a1):
    _, _, stamps = a1
    by_slug = {s.slug: s.row_id for s in stamps}
    assert by_slug["switch"] == "L01+L02+L03+L04+L05+L06"  # one "$" whatever the letters next to it
    assert by_slug["light_recessed"] == "L12+L13"
    assert by_slug["receptacle_half_hot"] == "L25"  # its half-filled circle is a glyph of its own


def test_ceiling_fan_found_on_a1(a1):
    sheet, page, stamps = a1
    fan = next(s for s in stamps if s.slug == "ceiling_fan")
    plan, ox, oy = T.render(page, rect(sheet.plan_frame))
    hits = T.nms(T.search(plan, fan, T.THRESHOLD, ox, oy))
    assert any(math.dist((m.x, m.y), (1275.9, 506.1)) <= 6 for m in hits)  # answer-key position
