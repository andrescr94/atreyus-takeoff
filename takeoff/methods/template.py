"""Rung 3, template: classic computer vision, each legend symbol used as a stamp on the plan image.

What Bluebeam VisualSearch does: crop every electrical legend symbol, then slide it over the plan
(cv2.matchTemplate, normalised correlation) at 0/90/180/270 degrees and mirrored, and keep the best
match within NMS_PT of each spot. The legend crops and the plan both come from rendering
sheet.pdf_path at DPI.

Limit, stated plainly: WHERE the legend symbols are, and which legend rows share a glyph, is read
from the page's vector legend (takeoff.legend.parse_legend). A scan has no vector legend, so this
rung refuses to run on one and scores zero there. Finding legend symbols on a raster is a separate
problem that this rung does not solve.

A stamp sees a glyph, not the letters next to it. Legend rows drawn with the same glyph (the "$" of
every switch, the receptacle circle behind AFI/GFI/WP...) are collapsed into one stamp. It is
reported as the row drawn without letters when there is exactly one (the plain switch, the recessed
light), else as the family's base slug (receptacle, detector). So exact-type recall is low by
construction for switches and receptacles even when every device is found.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf

from takeoff import legend
from takeoff.ingest import Sheet
from takeoff.match import Detection
from takeoff.methods import Method, make_detection, rect

DPI = 200  # receptacle ~22x34 px: enough detail, and the plan frame stays ~1600x2250 px
MARGIN_PT = 1.0  # around the legend bbox; at 2 pt the "3D"/"IAQ" labels creep into the crop
SAME_ORIENTATION = 0.95  # blurred correlation above which a turned glyph is a repeat (symmetric glyphs)
# Tuned on A1-111 (vector) only. At 0.62 and below the "$" stamp fires along straight wall lines
# (its masked ink is mostly one vertical stroke): 113 found for 53 placed. From 0.63 to 0.65 the
# count is flat (53-54 found, 52 placed); above that real receptacles drop out, since labels and
# walls overlap them (true hits score 0.5-0.95). 0.65 sits at the top of that plateau.
THRESHOLD = 0.65
NMS_PT = 6.0
MASK_PX = 3  # 1 pt; 5 and 8 px tried on A1-111: fewer receptacles on walls kept, same wall false hits


@dataclass
class Stamp:
    slug: str
    row_id: str
    image: np.ndarray  # greyscale, legend orientation


@dataclass
class Match:
    score: float
    x: float  # centre, PDF points
    y: float
    stamp: Stamp
    rotation: int
    mirrored: bool


def render(page: pymupdf.Page, clip: pymupdf.Rect, dpi: int = DPI) -> tuple[np.ndarray, float, float]:
    """Greyscale pixels of clip, plus the PDF point of pixel (0, 0)'s top-left corner."""
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    return img.copy(), pix.x * 72 / dpi, pix.y * 72 / dpi


def similarity(a: np.ndarray, b: np.ndarray, shift: int = 3) -> float:
    """Best normalised correlation of two blurred crops of about the same size, b slid up to
    `shift` px over a: 1 px strokes a pixel off the grid would otherwise correlate at ~0.6."""
    b = cv2.GaussianBlur(cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA), (5, 5), 0)
    a = cv2.copyMakeBorder(cv2.GaussianBlur(a, (5, 5), 0), shift, shift, shift, shift, cv2.BORDER_REPLICATE)
    return float(np.nan_to_num(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)).max())


def same_glyph(a: legend.Template, b: legend.Template) -> bool:
    """Two legend rows drawn with the same glyph: same family, stroke count, fills and stroke length.

    Read from the vector legend, like the boxes. Comparing the pixel crops was tried first and is
    not reliable at this size: blurred crops of one "$" correlate 0.93-0.99 while the plain light
    circle against the recessed light (the same circle inside a square) reaches 0.90 on A1-111 and
    crosses over on A4-111."""
    return (
        a.family == b.family
        and a.signature[0] == b.signature[0]
        and a.fills == b.fills
        and abs(a.signature[3] - b.signature[3]) <= 1.0
    )


def stamps(templates: list[legend.Template], page: pymupdf.Page, dpi: int = DPI) -> list[Stamp]:
    """One stamp per distinct electrical glyph, cut from page's pixels and named as the module
    docstring says."""
    groups: list[list[legend.Template]] = []
    for t in templates:
        if not t.is_electrical:
            continue
        group = next((g for g in groups if same_glyph(g[0], t)), None)
        if group:
            group.append(t)
        else:
            groups.append([t])
    out = []
    for members in groups:
        x0, y0, x1, y1 = members[0].bbox
        img, _, _ = render(page, pymupdf.Rect(x0 - MARGIN_PT, y0 - MARGIN_PT, x1 + MARGIN_PT, y1 + MARGIN_PT), dpi)
        plain = [m for m in members if not m.qualifiers]
        slug = members[0].slug if len(members) == 1 else plain[0].slug if len(plain) == 1 else members[0].family
        out.append(Stamp(slug, "+".join(m.row_id for m in members), img))
    return out


def orientations(img: np.ndarray) -> list[tuple[int, bool, np.ndarray]]:
    """The glyph turned 0/90/180/270 degrees, plain and mirrored, without repeats: a symmetric
    glyph (the recessed light, the ceiling fan) is searched once, not eight times."""
    out: list[tuple[int, bool, np.ndarray]] = []
    for mirrored in (False, True):
        base = cv2.flip(img, 1) if mirrored else img
        for k in range(4):
            v = np.rot90(base, -k).copy()  # k quarter turns clockwise
            if not any(o.shape == v.shape and similarity(o, v) >= SAME_ORIENTATION for _, _, o in out):
                out.append((90 * k, mirrored, v))
    return out


def ink_mask(tpl: np.ndarray) -> np.ndarray:
    """The glyph's strokes plus MASK_PX around them: only these pixels are compared, so a wall,
    a room line or a label inside the stamp's box does not drag the score down."""
    return cv2.dilate((tpl < 160).astype(np.uint8), np.ones((2 * MASK_PX + 1,) * 2, np.uint8)).astype(np.float32)


def search(plan: np.ndarray, stamp: Stamp, threshold: float, ox: float = 0.0, oy: float = 0.0,
           dpi: int = DPI) -> list[Match]:
    """Local maxima of the correlation map above threshold, for every orientation of the stamp."""
    pt = 72 / dpi
    found = []
    for rotation, mirrored, tpl in orientations(stamp.image):
        if tpl.shape[0] > plan.shape[0] or tpl.shape[1] > plan.shape[1]:
            continue
        res = cv2.matchTemplate(plan, tpl, cv2.TM_CCOEFF_NORMED, mask=ink_mask(tpl))
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        peaks = (res >= threshold) & (res == cv2.dilate(res, np.ones((5, 5), np.uint8)))
        h, w = tpl.shape
        for y, x in zip(*np.nonzero(peaks)):
            cx, cy = ox + (x + w / 2) * pt, oy + (y + h / 2) * pt
            found.append(Match(float(res[y, x]), cx, cy, stamp, rotation, mirrored))
    return found


def nms(matches: list[Match], radius: float = NMS_PT) -> list[Match]:
    """Best score first; drop any match within radius of one already kept (any stamp, any rotation)."""
    kept: list[Match] = []
    for m in sorted(matches, key=lambda m: -m.score):
        if all((m.x - k.x) ** 2 + (m.y - k.y) ** 2 > radius**2 for k in kept):
            kept.append(m)
    return kept


def detect(sheet: Sheet, threshold: float = THRESHOLD) -> list[Detection]:
    page = pymupdf.open(str(sheet.pdf_path))[0]
    if not page.get_drawings():
        raise ValueError("template needs the vector legend to find the legend symbols; this page has none")
    templates = legend.parse_legend(page, sheet.legend_frame)
    plan, ox, oy = render(page, rect(sheet.plan_frame))
    with ThreadPoolExecutor() as pool:  # cv2 releases the GIL: one stamp per core
        found = pool.map(lambda s: search(plan, s, threshold, ox, oy), stamps(templates, page))
        matches = nms([m for ms in found for m in ms])
    return [
        make_detection(sheet, n, m.stamp.slug, m.x, m.y, m.score, method="template",
                       template_row=m.stamp.row_id, score=round(m.score, 3), rotation=m.rotation,
                       mirrored=m.mirrored)
        for n, m in enumerate(matches)
    ]


METHODS = {
    "template": Method(
        name="template", rung=3, needs="vector PDF (its legend)",
        idea="crop each legend symbol and stamp-search the plan image for it at four rotations, mirrored too",
        detect=detect,
    )
}
