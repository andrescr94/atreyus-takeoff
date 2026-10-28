"""Phase 2, geometry: recover symbols as connected stroke components on vector plans.

The CAD export exploded every symbol into tiny straight stroke segments (no curves,
no per-symbol XObjects). Segments whose endpoints touch are grouped with union-find
and each group becomes a Component with an exact bbox in PAGE coordinates (PDF points).
Words are read from the page text with their line direction so the diagonal watermark
can be excluded.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field

import pymupdf


@dataclass(frozen=True)
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Component:
    id: int
    bbox: tuple[float, float, float, float]
    segments: list[Segment]
    n_segments: int
    total_length: float
    signature: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class Word:
    text: str
    bbox: tuple[float, float, float, float]
    dir: tuple[float, float]


@dataclass
class Geometry:
    components: list[Component]
    words: list[Word]
    clip: pymupdf.Rect


def _touches(seg: Segment, clip: pymupdf.Rect) -> bool:
    """Axis-aligned-bbox overlap test that, unlike Rect.intersects, accepts zero-width/height boxes.

    Horizontal and vertical lines have degenerate rects; pymupdf's Rect.intersects() returns False
    for any empty rect, which silently dropped every axis-aligned segment (symbol squares, ticks,
    wall lines) in the original probe.
    """
    return (
        max(seg.x0, seg.x1) >= clip.x0
        and min(seg.x0, seg.x1) <= clip.x1
        and max(seg.y0, seg.y1) >= clip.y0
        and min(seg.y0, seg.y1) <= clip.y1
    )


def _collect_segments(page: pymupdf.Page, clip: pymupdf.Rect, max_stroke_width: float) -> list[Segment]:
    """Line and rectangle-edge segments of thin stroke drawings that touch the clip (per segment)."""
    segs: list[Segment] = []
    for d in page.get_drawings():
        if d["type"] != "s" or (d.get("width") or 0) >= max_stroke_width:
            continue
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                candidates = [Segment(a.x, a.y, b.x, b.y)]
            elif it[0] == "re":
                r = it[1]
                pts = [r.tl, r.tr, r.br, r.bl]
                candidates = [Segment(a.x, a.y, b.x, b.y) for a, b in zip(pts, pts[1:] + pts[:1])]
            else:
                continue
            segs.extend(seg for seg in candidates if _touches(seg, clip))
    return segs


def _union_find_components(segs: list[Segment], tol: float) -> list[list[int]]:
    """Group segment indices whose endpoints lie within `tol` of each other (grid-bucketed)."""
    parent = list(range(len(segs)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    cell = 2.0 * tol + 0.8  # bucket wider than tol so neighbours are in adjacent cells only
    grid: dict[tuple[int, int], list[tuple[int, float, float]]] = collections.defaultdict(list)
    for i, s in enumerate(segs):
        for px, py in ((s.x0, s.y0), (s.x1, s.y1)):
            kx, ky = int(px // cell), int(py // cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, qx, qy in grid.get((kx + dx, ky + dy), ()):
                        if abs(qx - px) <= tol and abs(qy - py) <= tol:
                            union(i, j)
            grid[(kx, ky)].append((i, px, py))

    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i in range(len(segs)):
        groups[find(i)].append(i)
    return list(groups.values())


def _collect_words(
    page: pymupdf.Page, clip: pymupdf.Rect, dir_tol: float = 0.01, vertical: bool = False
) -> list[Word]:
    """Whitespace-separated words of horizontal text lines (dir == (1, 0)) intersecting the clip.

    Built from get_text("rawdict") because only lines carry "dir"; the diagonal watermark
    ("FOR USE IN THE CITY OF ESCONDIDO", dir (0.866, -0.5)) is dropped. Vertical lines
    (dir (0, +-1): half the qualifier labels on the plan, GFI / AFI / D / F / VS / DW / MW on
    vertical walls) are admitted only when `vertical` is True.
    """
    words: list[Word] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line["dir"]
            horizontal = abs(dx - 1.0) <= dir_tol and abs(dy) <= dir_tol
            if not horizontal and not (vertical and abs(dx) <= dir_tol and abs(abs(dy) - 1.0) <= dir_tol):
                continue
            chars = [ch for span in line["spans"] for ch in span["chars"]]
            run: list[dict] = []
            for ch in chars + [{"c": " "}]:
                if ch["c"].isspace():
                    if run:
                        rect = pymupdf.Rect(run[0]["bbox"])
                        for c in run[1:]:
                            rect |= pymupdf.Rect(c["bbox"])
                        if clip.intersects(rect):
                            words.append(Word("".join(c["c"] for c in run), tuple(rect), (round(dx), round(dy))))
                        run = []
                else:
                    run.append(ch)
    return words


N_ANGLE_BINS = 8
_BIN_DEG = 180.0 / N_ANGLE_BINS  # 22.5 degrees per bin


def angle_histogram(segments: list[Segment]) -> tuple[float, ...]:
    """Length-weighted histogram of undirected segment angles, normalised to sum 1.

    Angles are taken modulo 180 degrees and binned with bin centres at multiples of 22.5
    degrees (bin = round(theta / 22.5) mod 8), so axis-aligned lines land exactly in bins
    0 (horizontal) and 4 (vertical) and the 90-degree rotation / mirror maps below are exact.
    """
    hist = [0.0] * N_ANGLE_BINS
    for s in segments:
        length = s.length
        if length == 0:
            continue
        theta = math.degrees(math.atan2(s.y1 - s.y0, s.x1 - s.x0)) % 180.0
        hist[round(theta / _BIN_DEG) % N_ANGLE_BINS] += length
    total = sum(hist) or 1.0
    return tuple(round(v / total, 3) for v in hist)


def canonical_histogram(hist: tuple[float, ...]) -> tuple[float, ...]:
    """Rotation (90 degrees) and mirror invariant form of an angle histogram.

    Rule: rotating a symbol by 90 degrees shifts every angle by 90 degrees, i.e. bin i -> i + 4;
    rotating by 180 degrees changes nothing (angles are undirected); mirroring across either
    axis negates the angle, i.e. bin i -> -i mod 8. The canonical form is the lexicographically
    smallest tuple among the four variants {h, rot90(h), mirror(h), rot90(mirror(h))}.
    """
    n = len(hist)
    half = n // 2
    variants = []
    for mirrored in (hist, tuple(hist[(-i) % n] for i in range(n))):
        variants.append(mirrored)
        variants.append(tuple(mirrored[(i + half) % n] for i in range(n)))
    return min(variants)


def signature(component: Component) -> tuple:
    """Translation-, rotation- and mirror-invariant descriptor for nearest-neighbour matching.

    (n_segments, round(min(w, h), 1), round(max(w, h), 1), round(total_length, 1),
     *canonical_histogram(angle_histogram(segments)))
    min/max of the bbox sides make the size part orientation-free; the histogram part is
    canonicalised as described in canonical_histogram.
    """
    x0, y0, x1, y1 = component.bbox
    w, h = x1 - x0, y1 - y0
    return (
        component.n_segments,
        round(min(w, h), 1),
        round(max(w, h), 1),
        round(component.total_length, 1),
        *canonical_histogram(angle_histogram(component.segments)),
    )


def signature_distance(a: Component, b: Component) -> float:
    """Normalised L1 distance in [0, 1] between two component signatures.

    Five equally weighted terms: relative difference |p - q| / max(p, q) of n_segments, min side,
    max side and total length (each in [0, 1]), plus half the L1 distance between the two
    canonical histograms (also in [0, 1] because each histogram sums to 1). Identical symbols
    score 0.0; a receptacle against a switch scores well above 0.2.
    """
    sa, sb = a.signature or signature(a), b.signature or signature(b)
    terms = []
    for p, q in zip(sa[:4], sb[:4]):
        m = max(abs(p), abs(q))
        terms.append(abs(p - q) / m if m else 0.0)
    terms.append(sum(abs(p - q) for p, q in zip(sa[4:], sb[4:])) / 2.0)
    return sum(terms) / len(terms)


def symbol_sized(
    components: list[Component], min_pt: float = 3, max_pt: float = 120, min_segments: int = 4
) -> list[Component]:
    """Components whose shorter side is >= min_pt, longer side <= max_pt and n_segments >= min_segments.

    max_pt defaults to 120 because the ceiling fan on sheet 1 is 114.8 pt across: its hub plus three
    blades form one 92-segment component (1219.2, 448.8, 1334.0, 513.2) and the fourth blade a
    separate 18-segment one. Door-swing arcs (54 pt) and bath fixtures pass this filter too; walls
    and dashed switch legs are excluded by size and by n_segments (dashes are 1-2 segments).
    """
    kept = []
    for c in components:
        x0, y0, x1, y1 = c.bbox
        w, h = x1 - x0, y1 - y0
        if c.n_segments >= min_segments and min(w, h) >= min_pt and max(w, h) <= max_pt:
            kept.append(c)
    return kept


def extract(
    page: pymupdf.Page,
    clip: pymupdf.Rect,
    tol: float = 0.6,
    max_stroke_width: float = 0.5,
    max_segment_length: float | None = None,
    vertical_words: bool = False,
) -> Geometry:
    """Components (thin-stroke segments grouped by touching endpoints) and horizontal words in `clip`.

    Bboxes are in page coordinates (PDF points), never clip-relative. `max_segment_length`, when
    set, drops longer segments before grouping: on sheet 1 every symbol stroke is <= 9 pt while
    walls, leaders and dimension lines are >= 10 pt, so a cut around 12 pt keeps symbols from
    being glued to the wall or leader they touch.
    """
    segs = _collect_segments(page, clip, max_stroke_width)
    if max_segment_length is not None:
        segs = [s for s in segs if s.length <= max_segment_length]
    components: list[Component] = []
    for cid, idx in enumerate(_union_find_components(segs, tol)):
        group = [segs[i] for i in idx]
        xs = [v for s in group for v in (s.x0, s.x1)]
        ys = [v for s in group for v in (s.y0, s.y1)]
        components.append(
            Component(
                id=cid,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                segments=group,
                n_segments=len(group),
                total_length=sum(s.length for s in group),
            )
        )
        components[-1].signature = signature(components[-1])
    return Geometry(components=components, words=_collect_words(page, clip, vertical=vertical_words), clip=clip)


def render_overlay(
    page: pymupdf.Page,
    clip: pymupdf.Rect,
    components: list[Component],
    out_png: str,
    dpi: int = 150,
    color: tuple[float, float, float] = (1, 0, 0),
) -> None:
    """Render `clip` of the page to out_png with a thin box around each component's bbox."""
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    canvas = pymupdf.open()
    pg = canvas.new_page(width=clip.width, height=clip.height)
    pg.insert_image(pg.rect, pixmap=pix)
    for c in components:
        x0, y0, x1, y1 = c.bbox
        box = pymupdf.Rect(x0 - clip.x0 - 1, y0 - clip.y0 - 1, x1 - clip.x0 + 1, y1 - clip.y0 + 1)
        pg.draw_rect(box, color=color, width=0.6)
    pg.get_pixmap(dpi=dpi).save(out_png)
    canvas.close()


def main(argv: list[str] | None = None) -> int:
    """python -m takeoff.geometry <pdf> <page_index> "x0,y0,x1,y1" <out.png>"""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 4:
        print(main.__doc__)
        return 2
    pdf, page_index, clip_text, out_png = args
    clip = pymupdf.Rect(*map(float, clip_text.split(",")))
    doc = pymupdf.open(pdf)
    geo = extract(doc[int(page_index)], clip)
    kept = symbol_sized(geo.components)
    print("segments:", sum(c.n_segments for c in geo.components))
    print("components:", len(geo.components))
    print("symbol-sized:", len(kept))
    print("words (horizontal):", len(geo.words))
    groups = collections.Counter(c.signature for c in kept)
    print("top signature groups (count, signature):")
    for sig, n in groups.most_common(20):
        print(f"  {n:3d}  {sig}")
    render_overlay(doc[int(page_index)], clip, kept, out_png)
    print("overlay saved", out_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
