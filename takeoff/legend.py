"""Phase 3, Legend: one Template per legend row, parsed from the sheet's legend frame.

Nothing about sheet 1 is hardcoded. A legend row is a short description text block with a symbol
drawn to its left (or, for the big ceiling fan, above it) and optional short qualifier labels
touching the symbol (GFI, WP, 3D, SD/MA ...). The pattern that matters: one shape per family and
the letters next to it set the subtype, so a template carries the shape signature, the qualifier
set and the slug classified from the description.

Pairing rules (all distances in PDF points):
* words within QUALIFIER_GAP of a symbol bbox are qualifiers of the nearest such symbol and are
  removed before text blocks are built;
* the remaining words form lines, lines form blocks when left-aligned (within 3 pt) and separated
  by less than 0.6 line heights;
* a symbol's description is the nearest block to its right (gap <= DESCRIPTION_GAP, vertical
  bands overlapping), else the nearest block below it (same gap, horizontal ranges overlapping);
  pairs are taken greedily by gap, one block per symbol;
* blocks without a symbol (NOTE text, plumbing rows whose cross is too small to be a component)
  give no template.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field

import pymupdf

from takeoff import geometry, vocabulary
from takeoff.geometry import Component, Segment, Word
from takeoff.ingest import Frame

QUALIFIER_GAP = 10.0  # GFI above its receptacle sits 8.6 pt away; the next row's CW is 11.3 pt away
QUALIFIER_MAX_CHARS = 5  # SD/MA, 240V, +84; description words (ELECTRICAL, WIRING) are longer
DESCRIPTION_GAP = 30.0  # measured gaps symbol -> description: 11-21 pt; symbol -> next column: >= 50
MERGE_MAX_SIDE = 120.0  # symbol scale, same bound as geometry.symbol_sized
MERGE_TOUCH = 0.5  # bbox gap that still counts as touching
SEGMENT_CUT = 12.5  # pt, plan only; longest symbol stroke is the receptacle's 12.4 pt line, cabinet edges start at 22
LEAF_LENGTH_FACTOR = 1.4  # a leaf is a free-ended segment this many median lengths long (circle sides ~2.0, leader tip 3.0)
OVERDRAW_TOL = 0.1  # pt; identical segments drawn on top of each other count once (x4 on sheet 1 ceilings, x8 on sheet 3)
MICRO_SEGMENT = 0.5  # pt; the legend traces the exhaust fan's filled sector with ~250 segments under 0.3 pt
FILL_MAX_SIDE = 60.0  # filled paths bigger than this are wall poche / hatches, not symbol parts
# Rule 1, concentric parts (receptacle circle + square, recessed light circle + square):
CONCENTRIC_CENTER = 1.5  # bbox centres this close ...
CONCENTRIC_RATIO = 1.5  # ... and longer sides within this ratio
# Rule 2, spokes of a composite fixture (ceiling fan blade on the hub + blades body):
SPOKE_BODY_SEGMENTS = 60  # the body is a big component ...
SPOKE_MIN_SEGMENTS = 12  # ... the spoke is a drawn part, not a dash (fan blade = 18)
SPOKE_MIN_RATIO = 0.35  # blade 52.9 / fan 114.8 = 0.46 merges; receptacle 12.4 / cabinet 45.6 = 0.27 does not


@dataclass
class Template:
    row_id: str
    slug: str
    family: str
    description: str
    qualifiers: list[str]
    signature: tuple
    bbox: tuple[float, float, float, float]
    component_ids: list[int]
    is_electrical: bool
    fills: int = 0  # distinct filled paths inside the symbol bbox (exhaust fan quadrant, detector dot)


@dataclass
class Merged:
    """A symbol made of one or more touching components (receptacle circle + square, fan + blade)."""

    component: Component
    member_ids: list[int] = field(default_factory=list)
    fills: int = 0


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]
    words: list[Word]


@dataclass
class Block:
    lines: list[Line]

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return _union(ln.bbox for ln in self.lines)


def _union(boxes) -> tuple[float, float, float, float]:
    boxes = list(boxes)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def gap(a: tuple, b: tuple) -> float:
    """Chebyshev gap between two bboxes: 0 when they overlap or touch."""
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return max(dx, dy)


def _longer(c: Component) -> float:
    return max(c.bbox[2] - c.bbox[0], c.bbox[3] - c.bbox[1])


def prune_leaves(component: Component, tol: float = 0.6) -> list[tuple[Component, int]]:
    """Successively pruned variants of the component: [(after round 1, removed), (after round 2, ...)].

    A round removes every free-ended segment (an endpoint touching no other endpoint of the
    component) at least LEAF_LENGTH_FACTOR x the median segment length long: dashed-leg stubs and
    wall ticks. Rounds repeat until nothing is free-ended or fewer than 4 segments remain. The
    caller takes the least-pruned variant that matches, because a receptacle's own lines are
    free-ended too and go in the round after its wall tick.
    """
    segs = list(component.segments)
    variants: list[tuple[Component, int]] = []
    removed = 0
    while True:
        lens = sorted(s.length for s in segs)
        median = lens[len(lens) // 2]
        pts = [(s.x0, s.y0) for s in segs] + [(s.x1, s.y1) for s in segs]

        def free(x: float, y: float, pts=pts) -> bool:
            return sum(1 for qx, qy in pts if abs(qx - x) <= tol and abs(qy - y) <= tol) == 1

        leaves = [
            s
            for s in segs
            if s.length >= LEAF_LENGTH_FACTOR * median and (free(s.x0, s.y0) or free(s.x1, s.y1))
        ]
        if not leaves or len(segs) - len(leaves) < 4:
            return variants
        segs = [s for s in segs if s not in leaves]
        removed += len(leaves)
        variants.append((_component_from(component.id, segs), removed))


def _component_from(cid: int, segs: list[Segment]) -> Component:
    xs = [v for s in segs for v in (s.x0, s.x1)]
    ys = [v for s in segs for v in (s.y0, s.y1)]
    c = Component(cid, (min(xs), min(ys), max(xs), max(ys)), segs, len(segs), sum(s.length for s in segs))
    c.signature = geometry.signature(c)
    return c


def core_bbox(c: Component) -> tuple[float, float, float, float]:
    """Bbox of the component without its leaves (stubs, ticks, leaders): the symbol proper."""
    variants = prune_leaves(c)
    return variants[-1][0].bbox if variants else c.bbox


def _same_symbol(a: Component, b: Component, core: dict[int, tuple]) -> bool:
    """True when a and b are two parts of one symbol (see the CONCENTRIC_* and SPOKE_* constants).

    Rule 1 (concentric): the leaf-pruned cores (core_bbox, cached in `core`) have centres within
    CONCENTRIC_CENTER and longer sides within CONCENTRIC_RATIO. The recessed-light circle sits
    inside its square with the same centre even when a dashed-leg stub hangs off it; a dashed
    piece touching a light is off-centre and stays separate.
    Rule 2 (spoke): the parts touch, the bigger has >= SPOKE_BODY_SEGMENTS segments, the smaller
    >= SPOKE_MIN_SEGMENTS with a longer side >= SPOKE_MIN_RATIO of the body's, and the smaller
    lies within the body's extent on at least one axis. The fourth blade of the ceiling fan ends
    on the hub at a T-junction, so endpoint union-find never joins it; a dashed wiring chain that
    touches the fan from outside is not a spoke.
    """
    if gap(a.bbox, b.bbox) > MERGE_TOUCH:
        return False
    ca, cb = core[a.id], core[b.id]
    la, lb = max(ca[2] - ca[0], ca[3] - ca[1]), max(cb[2] - cb[0], cb[3] - cb[1])
    if la and lb:
        centres_match = (
            abs((ca[0] + ca[2]) - (cb[0] + cb[2])) <= 2 * CONCENTRIC_CENTER
            and abs((ca[1] + ca[3]) - (cb[1] + cb[3])) <= 2 * CONCENTRIC_CENTER
        )
        if centres_match and min(la, lb) / max(la, lb) >= 1 / CONCENTRIC_RATIO:
            return True
    big, small = (a, b) if _longer(a) >= _longer(b) else (b, a)
    ratio = _longer(small) / _longer(big) if _longer(big) else 0.0
    sb, bb = small.bbox, big.bbox
    within_an_axis = (bb[0] <= sb[0] and sb[2] <= bb[2]) or (bb[1] <= sb[1] and sb[3] <= bb[3])
    return (
        big.n_segments >= SPOKE_BODY_SEGMENTS
        and small.n_segments >= SPOKE_MIN_SEGMENTS
        and ratio >= SPOKE_MIN_RATIO
        and within_an_axis
    )


def fill_rects(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """Bboxes of small filled paths: the exhaust fan's black quadrant is a fill, not strokes."""
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if "f" in d["type"] and r.width <= FILL_MAX_SIDE and r.height <= FILL_MAX_SIDE:
            out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def dedupe_overdraw(segs: list[Segment], tol: float = OVERDRAW_TOL) -> list[Segment]:
    """Segments with the same endpoints (either direction, within tol) collapse to one.

    Overdraw is a drafting artifact that changes per sheet (the same recessed light is 60 segments
    on A1-111 and 120 on A3-111), so neither n_segments nor total_length may count it.
    """
    seen: set[tuple] = set()
    out = []
    for s in segs:
        a = (round(s.x0 / tol), round(s.y0 / tol))
        b = (round(s.x1 / tol), round(s.y1 / tol))
        key = (a, b) if a <= b else (b, a)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _build(members: list[Component], fills: list[tuple]) -> Merged:
    """One Merged from its member components: overdraw and micro segments dropped, fills counted."""
    segs = dedupe_overdraw([s for c in members for s in c.segments])
    kept = [s for s in segs if s.length >= MICRO_SEGMENT]
    if len(kept) < 4:
        kept = segs
    xs = [v for s in kept for v in (s.x0, s.x1)]
    ys = [v for s in kept for v in (s.y0, s.y1)]
    comp = Component(
        id=members[0].id,
        bbox=(min(xs), min(ys), max(xs), max(ys)),
        segments=kept,
        n_segments=len(kept),
        total_length=sum(s.length for s in kept),
    )
    comp.signature = geometry.signature(comp)
    n_fills = len({f for f in fills if _inside_box(f, comp.bbox)})  # a set: overdrawn fills count once
    return Merged(comp, [c.id for c in members], n_fills)


def _inside_box(inner: tuple, outer: tuple, tol: float = 0.5) -> bool:
    return (
        inner[0] >= outer[0] - tol
        and inner[1] >= outer[1] - tol
        and inner[2] <= outer[2] + tol
        and inner[3] <= outer[3] + tol
    )


def merge_overlapping(components: list[Component], fills: list[tuple] | None = None) -> list[Merged]:
    """Union components that are two parts of one symbol (rules in _same_symbol); transitive.

    Only symbol-scale parts take part (longer side <= MERGE_MAX_SIDE, >= 4 segments), so a
    receptacle drawn on a wall is never merged into the wall. Every result is rebuilt by _build:
    segments under MICRO_SEGMENT dropped before the signature, `fills` counted inside the bbox.
    """
    parts = [c for c in components if c.n_segments >= 4 and _longer(c) <= MERGE_MAX_SIDE]
    parent = {c.id: c.id for c in parts}
    core = {c.id: core_bbox(c) for c in parts}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    parts.sort(key=lambda c: c.bbox[0])
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            if b.bbox[0] - a.bbox[2] > MERGE_TOUCH:
                break
            if _same_symbol(a, b, core):
                parent[find(a.id)] = find(b.id)

    groups: dict[int, list[Component]] = {}
    for c in parts:
        groups.setdefault(find(c.id), []).append(c)
    return [_build(sorted(members, key=lambda c: c.id), fills or []) for members in groups.values()]


def _lines(words: list[Word]) -> list[Line]:
    """Words on one text line: same direction, same baseline, consecutive gaps under one line height."""
    lines: list[Line] = []
    for direction in {w.dir for w in words}:
        horizontal = direction[1] == 0
        group = [w for w in words if w.dir == direction]
        # "along" is the reading axis, "across" the line-stacking axis
        along = (0, 2) if horizontal else (1, 3)
        across = (1, 3) if horizontal else (0, 2)
        group.sort(key=lambda w: ((w.bbox[across[0]] + w.bbox[across[1]]) / 2, w.bbox[along[0]]))
        rows: list[list[Word]] = []
        for w in group:
            h = w.bbox[across[1]] - w.bbox[across[0]]
            wc = (w.bbox[across[0]] + w.bbox[across[1]]) / 2
            if rows:
                last = rows[-1][-1]
                lc = (last.bbox[across[0]] + last.bbox[across[1]]) / 2
                if abs(wc - lc) <= 0.4 * h:
                    rows[-1].append(w)
                    continue
            rows.append([w])
        for row in rows:
            row.sort(key=lambda w: w.bbox[along[0]])
            run = [row[0]]
            for w in row[1:]:
                h = w.bbox[across[1]] - w.bbox[across[0]]
                if w.bbox[along[0]] - run[-1].bbox[along[1]] <= 1.0 * h:
                    run.append(w)
                else:
                    lines.append(Line(" ".join(x.text for x in run), _union(x.bbox for x in run), run))
                    run = [w]
            lines.append(Line(" ".join(x.text for x in run), _union(x.bbox for x in run), run))
    return lines


def _blocks(lines: list[Line]) -> list[Block]:
    """Left-aligned lines (3 pt) stacked closer than 0.6 line heights form one block."""
    blocks: list[Block] = []
    for ln in sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0])):
        h = ln.bbox[3] - ln.bbox[1]
        for blk in blocks:
            last = blk.lines[-1]
            if abs(ln.bbox[0] - last.bbox[0]) <= 3 and 0 <= ln.bbox[1] - last.bbox[3] <= 0.6 * h:
                blk.lines.append(ln)
                break
        else:
            blocks.append(Block([ln]))
    return blocks


def _band_overlap(a: tuple, b: tuple, axis: int) -> bool:
    lo, hi = axis, axis + 2
    return a[lo] <= b[hi] and b[lo] <= a[hi]


def pair_symbols_with_blocks(symbols: list[Merged], blocks: list[Block]) -> dict[int, Block]:
    """Greedy one-to-one pairing: right neighbour first, else the block below (see module doc)."""
    candidates: list[tuple[float, int, int]] = []
    for si, sym in enumerate(symbols):
        sb = sym.component.bbox
        right = [
            (blk.bbox[0] - sb[2], bi)
            for bi, blk in enumerate(blocks)
            if 0 < blk.bbox[0] - sb[2] <= DESCRIPTION_GAP and _band_overlap(sb, blk.bbox, 1)
        ]
        below = [
            (blk.bbox[1] - sb[3], bi)
            for bi, blk in enumerate(blocks)
            if 0 < blk.bbox[1] - sb[3] <= DESCRIPTION_GAP and _band_overlap(sb, blk.bbox, 0)
        ]
        for g, bi in right or below:
            candidates.append((g, si, bi))
    paired: dict[int, Block] = {}
    used: set[int] = set()
    for _, si, bi in sorted(candidates):
        if si in paired or bi in used:
            continue
        paired[si] = blocks[bi]
        used.add(bi)
    return paired


def _assign_qualifiers(words: list[Word], symbols: list[Merged]) -> dict[int, list[Word]]:
    """Each short word (<= QUALIFIER_MAX_CHARS) within QUALIFIER_GAP of a symbol belongs to the nearest one."""
    out: dict[int, list[Word]] = {i: [] for i in range(len(symbols))}
    for w in words:
        if len(w.text) > QUALIFIER_MAX_CHARS:
            continue
        best = min(
            ((gap(w.bbox, s.component.bbox), i) for i, s in enumerate(symbols)), default=(None, None)
        )
        if best[0] is not None and best[0] <= QUALIFIER_GAP:
            out[best[1]].append(w)
    return out


def _column_major(symbols: list[Merged], frame: Frame):
    """Sort key for (symbol index, block): legend columns left to right, rows top to bottom.

    A symbol's column is its x0 position in thirds of the frame width (this plan set draws three
    legend columns); within a column rows are ordered by y0.
    """
    col_w = (frame.x1 - frame.x0) / 3

    def key(item):
        x0, y0 = symbols[item[0]].component.bbox[:2]
        return (int((x0 - frame.x0) // col_w), y0)

    return key


def parse_legend(page: pymupdf.Page, legend_frame: Frame) -> list[Template]:
    clip = pymupdf.Rect(legend_frame.x0, legend_frame.y0, legend_frame.x1, legend_frame.y1)
    # legend symbols touch nothing, so every segment is kept (the plan applies SEGMENT_CUT)
    geo = geometry.extract(page, clip, vertical_words=True)
    merged = merge_overlapping(geo.components, fill_rects(page))
    symbols = [m for m in merged if geometry.symbol_sized([m.component])]
    quals = _assign_qualifiers(geo.words, symbols)
    taken = {id(w) for ws in quals.values() for w in ws}
    blocks = _blocks(_lines([w for w in geo.words if id(w) not in taken]))
    paired = pair_symbols_with_blocks(symbols, blocks)
    templates: list[Template] = []
    for n, (si, blk) in enumerate(sorted(paired.items(), key=_column_major(symbols, legend_frame)), start=1):
        sym = symbols[si]
        description = blk.text
        if description.upper().startswith("NOTE"):
            continue
        slug = vocabulary.classify_description(description)
        dtype = vocabulary.lookup(slug)
        qwords = sorted(quals[si], key=lambda w: (w.bbox[1], w.bbox[0]))
        templates.append(
            Template(
                row_id=f"L{n:02d}",
                slug=slug,
                family=dtype.family,
                description=description,
                qualifiers=[w.text for w in qwords],
                signature=sym.component.signature,
                bbox=sym.component.bbox,
                component_ids=list(sym.member_ids),
                is_electrical=dtype.is_electrical,
                fills=sym.fills,
            )
        )
    return templates


FAMILY_COLORS = {
    "switch": (0.0, 0.5, 0.0),
    "receptacle": (0.9, 0.0, 0.0),
    "light": (0.9, 0.6, 0.0),
    "fan": (0.0, 0.0, 0.9),
    "detector": (0.6, 0.0, 0.6),
    "junction": (0.0, 0.6, 0.6),
    "other": (0.4, 0.4, 0.4),
}


def render_templates(page: pymupdf.Page, frame: Frame, templates: list[Template], out_png: str, dpi: int = 150):
    clip = pymupdf.Rect(frame.x0, frame.y0, frame.x1, frame.y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    canvas = pymupdf.open()
    pg = canvas.new_page(width=clip.width, height=clip.height)
    pg.insert_image(pg.rect, pixmap=pix)
    for t in templates:
        x0, y0, x1, y1 = t.bbox
        color = FAMILY_COLORS.get(t.family, (0, 0, 0))
        box = pymupdf.Rect(x0 - clip.x0 - 1.5, y0 - clip.y0 - 1.5, x1 - clip.x0 + 1.5, y1 - clip.y0 + 1.5)
        pg.draw_rect(box, color=color, width=0.7)
        label = f"{t.row_id} {t.slug}" + (f" [{' '.join(t.qualifiers)}]" if t.qualifiers else "")
        pg.insert_text((box.x0, box.y1 + 5), label, fontsize=4, color=color)
    pg.get_pixmap(dpi=dpi).save(out_png)
    canvas.close()


def main(argv: list[str] | None = None) -> int:
    """python -m takeoff.legend <sheet_id> [...]  -> prints templates, writes data/legend_<sheet>.png"""
    from takeoff.ingest import load_sheet

    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(main.__doc__)
        return 2
    for sheet_id in args:
        sheet = load_sheet(sheet_id)
        page = pymupdf.open(str(sheet.pdf_path))[0]
        templates = parse_legend(page, sheet.legend_frame)
        print(f"{sheet_id}: {len(templates)} templates")
        for t in templates:
            q = " ".join(t.qualifiers)
            flag = "" if t.is_electrical else " (not counted)"
            fill = f" fills={t.fills}" if t.fills else ""
            print(f"  {t.row_id} {t.slug:22s} {t.family:10s} [{q:10s}] sig={t.signature[:4]}{fill} {t.description}{flag}")
        out = f"data/legend_{sheet_id}.png"
        render_templates(page, sheet.legend_frame, templates, out)
        with open(f"data/sheets/{sheet_id}/templates.json", "w") as f:
            json.dump([asdict(t) for t in templates], f, indent=1)
        print("  overlay", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
