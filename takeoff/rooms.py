"""Phase 5, Rooms: which room is a device in.

Approach (option a of the brief, a low-resolution wall raster):

1. Collect the grey FILLED rectangles inside the plan frame (``page.get_drawings()``, type "f" or
   "fs", fill about 0.75 grey on this plan set). On this CAD export those fills are the walls and
   nothing else; door swings and windows are stroked over them, so the fills already form closed
   rooms and the exterior ring is unbroken.
2. Rasterize them on a grid of CELL_PT points per pixel (4.5 pt = 3 inches at 1/4" = 1'-0", about
   4 px per foot). A pixel is wall when any wall rectangle overlaps its cell, so a 5.3 pt interior
   wall never falls between pixel centres and leaks.
3. Optionally dilate the wall pixels (``dilate_px``) to close door gaps; 0 by default because the
   fills are closed on all four sheets of this set (checked on the overlays). A dilation of 5-6 px
   would be needed for a 3'-0" door and would swallow the 2'-6" deep closets, so it stays off.
4. Flood-fill the empty pixels (4-connected). Regions touching the raster border are "Exterior"
   (the brief's rule: anything outside the exterior wall ring). Each remaining region is named
   by the bold room labels whose centres fall inside it; a region holding several labels (the
   open-plan KITCHEN / DINING + LIVING) is split between them by nearest label centre.
   Regions with no label are kept as "UNLABELLED" so the overlay shows them, but assign_room
   never answers with that name: it falls back to the nearest label instead.

Why not nearest label alone (option b): walls matter more than distance; the LNDRY label is
closer to half of the kitchen than the KITCHEN / DINING label is. Why not polygons: the fills are
hundreds of overlapping thin rectangles (1 pt edge lines beside each 5.3 pt core), and a flood
fill on a 130 x 180 raster is a few milliseconds of plain Python with no numpy.

assign_room: a point inside a labelled room is "inside"; a point on a wall pixel (devices sit on
the wall fill) is "nudged" to the nearest labelled room pixel within NUDGE_PT; otherwise the
"nearest-label" fallback answers with lower confidence. Points outside the exterior ring, or
outside the plan frame, are "Exterior".
"""

from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

CELL_PT = 4.5  # raster resolution: one pixel per 4.5 pt (3 inches at 1/4" = 1'-0")
NUDGE_PT = 12.0  # how far a point on a wall may be moved into a room
MIN_REGION_PX = 4  # smaller empty regions are wall slivers, not rooms
GREY_MIN, GREY_MAX = 0.5, 0.9  # wall fill grey level (0.754 on this plan set)
LABEL_SIZE = (9.0, 10.5)  # room labels are Arial,Bold 9.6 pt
LABEL_RE = re.compile(r"^[A-Z][A-Z0-9 /\-]*$")
NOT_A_ROOM_RE = re.compile(r"^[A-Z]-\d+$")  # sheet references like A-901 in detail callouts
EXTERIOR = "Exterior"
UNLABELLED = "UNLABELLED"


@dataclass(frozen=True)
class Raster:
    """Pixel grid over the plan frame: pixel (ix, iy) covers x0 + ix*cell .. x0 + (ix+1)*cell."""

    x0: float
    y0: float
    cell: float
    width: int
    height: int
    walls: frozenset[tuple[int, int]]
    cache: dict = field(default_factory=dict, compare=False, repr=False)

    def pixel(self, x: float, y: float) -> tuple[int, int]:
        return int((x - self.x0) // self.cell), int((y - self.y0) // self.cell)

    def contains(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.width and 0 <= iy < self.height

    def centre(self, ix: int, iy: int) -> tuple[float, float]:
        return self.x0 + (ix + 0.5) * self.cell, self.y0 + (iy + 0.5) * self.cell

    def bbox(self, pixels: set[tuple[int, int]]) -> tuple[float, float, float, float]:
        xs = [p[0] for p in pixels]
        ys = [p[1] for p in pixels]
        return (
            self.x0 + min(xs) * self.cell,
            self.y0 + min(ys) * self.cell,
            self.x0 + (max(xs) + 1) * self.cell,
            self.y0 + (max(ys) + 1) * self.cell,
        )


@dataclass
class Room:
    name: str
    mask: frozenset[tuple[int, int]]  # raster pixels (ix, iy)
    bbox: tuple[float, float, float, float]  # PDF points
    label_bbox: tuple[float, float, float, float] | None  # PDF points, None for Exterior/UNLABELLED
    raster: Raster = field(repr=False)

    @property
    def area_pt2(self) -> float:
        return len(self.mask) * self.raster.cell**2

    @property
    def is_room(self) -> bool:
        return self.name not in (EXTERIOR, UNLABELLED)


@dataclass(frozen=True)
class Label:
    text: str
    bbox: tuple[float, float, float, float]

    @property
    def centre(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2


def wall_rects(page: pymupdf.Page, clip: pymupdf.Rect) -> list[pymupdf.Rect]:
    """Grey filled drawings intersecting ``clip``, as their bounding rectangles."""
    rects = []
    for d in page.get_drawings():
        if "f" not in d["type"]:
            continue
        fill = d.get("fill")
        if not fill or not all(GREY_MIN <= c <= GREY_MAX for c in fill[:3]):
            continue
        if max(fill[:3]) - min(fill[:3]) > 0.05:
            continue
        r = pymupdf.Rect(d["rect"])
        if r.x1 > clip.x0 and r.x0 < clip.x1 and r.y1 > clip.y0 and r.y0 < clip.y1:
            rects.append(r)
    return rects


def rasterize(clip: pymupdf.Rect, rects: list[pymupdf.Rect], cell: float = CELL_PT, dilate_px: int = 0) -> Raster:
    """Mark every pixel whose cell overlaps a wall rectangle; optionally dilate by ``dilate_px``."""
    width = math.ceil(clip.width / cell)
    height = math.ceil(clip.height / cell)
    walls: set[tuple[int, int]] = set()
    eps = 1e-6
    for r in rects:
        ix0 = max(0, int((r.x0 - clip.x0) // cell))
        ix1 = min(width - 1, int((r.x1 - clip.x0 - eps) // cell))
        iy0 = max(0, int((r.y0 - clip.y0) // cell))
        iy1 = min(height - 1, int((r.y1 - clip.y0 - eps) // cell))
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                walls.add((ix, iy))
    for _ in range(dilate_px):
        grown = set(walls)
        for ix, iy in walls:
            grown.update({(ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)})
        walls = {p for p in grown if 0 <= p[0] < width and 0 <= p[1] < height}
    return Raster(clip.x0, clip.y0, cell, width, height, frozenset(walls))


def empty_regions(raster: Raster) -> list[set[tuple[int, int]]]:
    """4-connected components of the non-wall pixels."""
    seen: set[tuple[int, int]] = set(raster.walls)
    regions = []
    for start_x in range(raster.width):
        for start_y in range(raster.height):
            if (start_x, start_y) in seen:
                continue
            region = set()
            queue = collections.deque([(start_x, start_y)])
            seen.add((start_x, start_y))
            while queue:
                ix, iy = queue.popleft()
                region.add((ix, iy))
                for nx, ny in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
                    if raster.contains(nx, ny) and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        queue.append((nx, ny))
            regions.append(region)
    return regions


def _touches_border(region: set[tuple[int, int]], raster: Raster) -> bool:
    return any(ix == 0 or iy == 0 or ix == raster.width - 1 or iy == raster.height - 1 for ix, iy in region)


def room_labels(page: pymupdf.Page, clip: pymupdf.Rect) -> list[Label]:
    """Bold, upper-case room labels inside ``clip``; stacked lines are joined into one label.

    Two bold lines that overlap horizontally and sit within one line height of each other are one
    label ("BEDROOM /" over "STUDIO"). Sheet references (A-901) and bare numbers (54) are dropped.
    """
    lines: list[Label] = []
    for block in page.get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            if tuple(round(v) for v in line["dir"]) != (1, 0):
                continue
            spans = [
                sp
                for sp in line["spans"]
                if sp["text"].strip() and "Bold" in sp["font"] and LABEL_SIZE[0] <= sp["size"] <= LABEL_SIZE[1]
            ]
            if not spans:
                continue
            text = " ".join(" ".join(sp["text"] for sp in spans).split())
            if not LABEL_RE.match(text) or NOT_A_ROOM_RE.match(text) or not any(c.isalpha() for c in text):
                continue
            bbox = pymupdf.Rect(spans[0]["bbox"])
            for sp in spans[1:]:
                bbox |= pymupdf.Rect(sp["bbox"])
            lines.append(Label(text, tuple(bbox)))
    lines.sort(key=lambda lb: (lb.bbox[1], lb.bbox[0]))
    merged: list[Label] = []
    for lb in lines:
        if merged:
            prev = merged[-1]
            h = prev.bbox[3] - prev.bbox[1]
            overlap = min(prev.bbox[2], lb.bbox[2]) - max(prev.bbox[0], lb.bbox[0])
            if overlap > 0 and 0 <= lb.bbox[1] - prev.bbox[3] <= h:
                box = pymupdf.Rect(prev.bbox) | pymupdf.Rect(lb.bbox)
                merged[-1] = Label(f"{prev.text} {lb.text}", tuple(box))
                continue
        merged.append(lb)
    return merged


def find_rooms(page: pymupdf.Page, plan_frame, cell: float = CELL_PT, dilate_px: int = 0) -> list[Room]:
    """Rooms of the plan inside ``plan_frame`` (a Rect or an object with x0, y0, x1, y1)."""
    clip = pymupdf.Rect(plan_frame.x0, plan_frame.y0, plan_frame.x1, plan_frame.y1)
    raster = rasterize(clip, wall_rects(page, clip), cell=cell, dilate_px=dilate_px)
    labels = room_labels(page, clip)
    exterior: set[tuple[int, int]] = set()
    rooms: list[Room] = []
    for region in empty_regions(raster):
        if _touches_border(region, raster):
            exterior |= region
            continue
        if len(region) < MIN_REGION_PX:
            continue
        inside = [lb for lb in labels if raster.pixel(*lb.centre) in region]
        if not inside:
            rooms.append(Room(UNLABELLED, frozenset(region), raster.bbox(region), None, raster))
            continue
        parts: dict[Label, set[tuple[int, int]]] = {lb: set() for lb in inside}
        for p in region:
            cx, cy = raster.centre(*p)
            nearest = min(inside, key=lambda lb: math.hypot(lb.centre[0] - cx, lb.centre[1] - cy))
            parts[nearest].add(p)
        for lb, pixels in parts.items():
            if pixels:
                rooms.append(Room(lb.text, frozenset(pixels), raster.bbox(pixels), lb.bbox, raster))
    rooms.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    if exterior:
        rooms.append(Room(EXTERIOR, frozenset(exterior), raster.bbox(exterior), None, raster))
    return rooms


def _as_point(point_or_bbox) -> tuple[float, float]:
    if len(point_or_bbox) == 4:
        x0, y0, x1, y1 = point_or_bbox
        return (x0 + x1) / 2, (y0 + y1) / 2
    x, y = point_or_bbox
    return float(x), float(y)


def assign_room(point_or_bbox, rooms: list[Room], nudge_pt: float = NUDGE_PT) -> tuple[str, str]:
    """(room name, how) for a point or a bbox (its centre) in PDF points.

    how is "inside" (the pixel belongs to that room, or to the Exterior), "nudged" (the point sat
    on a wall or in an unlabelled region and was moved up to ``nudge_pt`` to the nearest labelled
    room) or "nearest-label" (nothing within reach; answered by the closest label centre, lower
    confidence).
    """
    x, y = _as_point(point_or_bbox)
    if not rooms:
        return EXTERIOR, "nearest-label"
    raster = rooms[0].raster
    ix, iy = raster.pixel(x, y)
    if not raster.contains(ix, iy):
        return EXTERIOR, "inside"
    owner = _owner_index(rooms)
    room = owner.get((ix, iy))
    if room is not None and (room.is_room or room.name == EXTERIOR):
        return room.name, "inside"
    # on a wall pixel or in an unlabelled region: nearest labelled-room pixel within nudge_pt
    reach = math.ceil(nudge_pt / raster.cell)
    best: tuple[float, Room] | None = None
    for dx in range(-reach, reach + 1):
        for dy in range(-reach, reach + 1):
            cand = owner.get((ix + dx, iy + dy))
            if cand is None or not cand.is_room:
                continue
            cx, cy = raster.centre(ix + dx, iy + dy)
            dist = math.hypot(cx - x, cy - y)
            if dist <= nudge_pt + raster.cell / 2 and (best is None or dist < best[0]):
                best = (dist, cand)
    if best is not None:
        return best[1].name, "nudged"
    labelled = [r for r in rooms if r.is_room and r.label_bbox is not None]
    if not labelled:
        return EXTERIOR, "nearest-label"
    nearest = min(labelled, key=lambda r: math.hypot(_as_point(r.label_bbox)[0] - x, _as_point(r.label_bbox)[1] - y))
    return nearest.name, "nearest-label"


def _owner_index(rooms: list[Room]) -> dict[tuple[int, int], Room]:
    """Pixel -> Room map, cached on the shared raster so repeated assign_room calls stay cheap."""
    raster = rooms[0].raster
    key = tuple(id(r) for r in rooms)
    cached = raster.cache.get("owner")
    if cached is None or cached[0] != key:
        cached = (key, {p: r for r in rooms for p in r.mask})
        raster.cache["owner"] = cached
    return cached[1]


PALETTE = [
    (230, 90, 90), (90, 150, 230), (90, 190, 110), (230, 170, 60), (170, 100, 220),
    (60, 190, 190), (220, 120, 180), (150, 150, 60), (240, 130, 70), (100, 120, 200),
]


def render_rooms(page: pymupdf.Page, plan_frame, rooms: list[Room], out_png: str | Path, dpi: int = 100) -> None:
    """Render ``plan_frame`` to ``out_png`` with every room region tinted and its name drawn.

    Labelled rooms get a palette colour, UNLABELLED regions a grey tint, the Exterior is left
    untinted but hatched lightly so its extent is visible.
    """
    from PIL import Image, ImageDraw, ImageFont

    clip = pymupdf.Rect(plan_frame.x0, plan_frame.y0, plan_frame.x1, plan_frame.y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    sx, sy = pix.width / clip.width, pix.height / clip.height
    font = ImageFont.load_default(size=max(10, int(dpi / 7)))
    for i, room in enumerate(rooms):
        if room.name == EXTERIOR:
            colour = (120, 120, 120, 25)
        elif room.name == UNLABELLED:
            colour = (40, 40, 40, 90)
        else:
            colour = (*PALETTE[i % len(PALETTE)], 80)
        raster = room.raster
        for ix, iy in room.mask:
            x0 = (raster.x0 + ix * raster.cell - clip.x0) * sx
            y0 = (raster.y0 + iy * raster.cell - clip.y0) * sy
            draw.rectangle((x0, y0, x0 + raster.cell * sx, y0 + raster.cell * sy), fill=colour)
    merged = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(merged)
    for i, room in enumerate(rooms):
        if room.name == EXTERIOR:
            continue
        if room.label_bbox is not None:
            x = (room.label_bbox[0] - clip.x0) * sx
            y = (room.label_bbox[3] - clip.y0) * sy + 2
        else:
            x = (room.bbox[0] - clip.x0) * sx + 2
            y = (room.bbox[1] - clip.y0) * sy + 2
        text = room.name if room.label_bbox is not None else f"{room.name} {room.area_pt2 / 324:.0f} sf"
        colour = (60, 60, 60) if room.name == UNLABELLED else tuple(max(0, c - 70) for c in PALETTE[i % len(PALETTE)])
        draw.text((x, y), text, fill=colour, font=font)
    merged.convert("RGB").save(out_png)


def main(argv: list[str] | None = None) -> int:
    """python -m takeoff.rooms <sheet id> [--sheets data/sheets] [--out data/rooms_<id>.png] [--dpi 100]"""
    import argparse

    from takeoff.ingest import load_sheet

    parser = argparse.ArgumentParser(prog="python -m takeoff.rooms", description=__doc__.split("\n\n")[0])
    parser.add_argument("sheet_id")
    parser.add_argument("--sheets", default="data/sheets", help="ingest output folder (default: data/sheets)")
    parser.add_argument("--out", default=None, help="overlay PNG (default: data/rooms_<sheet id>.png)")
    parser.add_argument("--dpi", type=int, default=100)
    parser.add_argument("--dilate", type=int, default=0, help="wall dilation in pixels (default: 0)")
    args = parser.parse_args(argv)
    sheet = load_sheet(args.sheet_id, out_dir=args.sheets)
    page = pymupdf.open(sheet.pdf_path)[0]
    rooms = find_rooms(page, sheet.plan_frame, dilate_px=args.dilate)
    out = Path(args.out) if args.out else Path("data") / f"rooms_{sheet.id}.png"
    render_rooms(page, sheet.plan_frame, rooms, out, dpi=args.dpi)
    cell = rooms[0].raster.cell if rooms else CELL_PT
    print(f"{sheet.id}: {len(rooms)} regions, raster {rooms[0].raster.width}x{rooms[0].raster.height} px at {cell} pt/px")
    for r in rooms:
        x0, y0, x1, y1 = r.bbox
        print(f"  {r.name:18s} {r.area_pt2 / sheet.scale_pt_per_ft**2:7.1f} sf  bbox=({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})")
    print("overlay saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
