"""Phase 4, Match: detect devices on the plan frame by nearest legend template.

Every candidate is a symbol-sized component (after legend.merge_overlapping) of the plan frame.
Its shape is compared with every electrical template of the sheet's legend with
geometry.signature_distance; the nearest template gives the family, the qualifier words next to
the symbol give the subtype. Evidence per detection: template row, distance, qualifier words with
their boxes, notes on every fallback taken. Errors are shown, not hidden: a symbol that matches a
family but carries no usable label is reported as the family's base type with lower confidence.

Threshold (THRESHOLD = 0.10), measured on sheet A1-111 on 2026-10-27 after overdraw collapsing:
* every true symbol scores <= 0.042 raw against its legend row (receptacles 0.000-0.015, switches
  <= 0.013, recessed lights <= 0.042, detectors 0.026, exhaust fans 0.031, ceiling fan 0.035);
* the nearest non-symbol scores 0.114 (a 15-segment dashed-wiring corner at (1070,564));
* the closest two legend rows of different families are 0.131 apart (junction box vs exterior
  wall light), so below 0.10 the family is never in doubt;
* the receptacle drawn with a wall tick at (967,274) scores 0.098 raw, the kitchen exhaust fan
  with its label leader 0.085 after pruning: both must pass.
Leaf pruning (legend.prune_leaves): a recessed light that absorbed the solid stub of its dashed
switch leg, a receptacle drawn with a wall-side tick (0.098 raw) and the kitchen exhaust fan with
its label leader attached all carry the extra strokes as free-ended segments, and so do the
receptacle's own two lines, so the pruned variants are only tried, least pruned first, when the
raw shape fails the threshold, and the note says so.
Segment cut (legend.SEGMENT_CUT): the laundry IAQ fan sits on a cabinet edge and union-find
glues it into a 430 pt network; segments over 12.5 pt (cabinet edges >= 22, walls, leaders) are
dropped before grouping on both the legend and the plan.
"""
from __future__ import annotations

import collections
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from takeoff import geometry, legend, vocabulary
from takeoff.geometry import Component, Word
from takeoff.ingest import Sheet
from takeoff.legend import SEGMENT_CUT, Template, gap, prune_leaves

THRESHOLD = 0.10
FILL_PENALTY = 0.5  # added when one side has a filled part and the other has none (fan quadrant vs plain circle)
QUALIFIER_RADIUS = 15.0  # pt from the symbol bbox; labels sit 1-10 pt away, the next device >= 20
AMBIGUOUS_FACTOR = 0.7  # confidence multiplier when the subtype had to fall back
METHOD = "vector-signature"


@dataclass
class Detection:
    id: str
    slug: str
    family: str
    bbox: tuple[float, float, float, float]
    confidence: float
    room: str | None = None
    evidence: dict = field(default_factory=dict)


def _template_component(t: Template) -> Component:
    c = Component(id=-1, bbox=t.bbox, segments=[], n_segments=t.signature[0], total_length=t.signature[3])
    c.signature = t.signature
    return c


def nearest_template(component: Component, templates: list[Template], fills: int = 0) -> tuple[Template, float]:
    """Nearest template by signature distance plus FILL_PENALTY when fill presence disagrees.

    The plan's exhaust fan (circle + two radii + a filled quadrant) is stroke-identical to the
    wall-mounted light; only the fill tells them apart.
    """
    best = None
    for t in templates:
        d = geometry.signature_distance(component, _template_component(t))
        if (fills > 0) != (t.fills > 0):
            d += FILL_PENALTY
        if best is None or d < best[1]:
            best = (t, d)
    return best


@dataclass
class _Candidate:
    merged: legend.Merged
    shape: Component  # the variant that matched: the raw component or a leaf-pruned one
    template: Template
    distance: float
    notes: list[str]
    words: list[Word] = field(default_factory=list)


def _match_shape(m: legend.Merged, templates: list[Template]) -> _Candidate | None:
    t, d = nearest_template(m.component, templates, m.fills)
    if d <= THRESHOLD:
        return _Candidate(m, m.component, t, d, [])
    for pruned, n in prune_leaves(m.component):
        t2, d2 = nearest_template(pruned, templates, m.fills)
        if d2 <= THRESHOLD:
            note = f"matched after pruning {n} free-ended segment(s); raw distance {d:.3f}"
            return _Candidate(m, pruned, t2, d2, [note])
    return None


def _assign_words(words: list[Word], cands: list[_Candidate], electrical: list[Template]) -> None:
    """Label words go to candidates by increasing distance (gap <= QUALIFIER_RADIUS).

    A candidate accepts a word only when the legend has a row of its family whose qualifier set
    contains the words it would then carry: GFI + WP is a row, D + F is not, so the second label
    of a switch bank moves on to the next nearest switch.
    """
    rows: dict[str, list[frozenset[str]]] = collections.defaultdict(list)
    for t in electrical:
        rows[t.family].append(frozenset(t.qualifiers))
    vocab = {q for t in electrical for q in t.qualifiers}
    pairs = []
    for w in words:
        if w.text not in vocab or len(w.text) > legend.QUALIFIER_MAX_CHARS:
            continue
        for i, c in enumerate(cands):
            g = gap(w.bbox, c.merged.component.bbox)
            if g <= QUALIFIER_RADIUS:
                pairs.append((g, id(w), i, w))
    taken: set[int] = set()
    for _, wid, i, w in sorted(pairs, key=lambda p: (p[0], p[1])):
        if wid in taken:
            continue
        c = cands[i]
        if w.text in {x.text for x in c.words}:
            continue  # "3D 3D" over a switch bank: the second 3D belongs to the next switch
        carried = {x.text for x in c.words} | {w.text}
        if any(carried <= row for row in rows[c.template.family]):
            c.words.append(w)
            taken.add(wid)


def _resolve_subtype(c: _Candidate, family_templates: list[Template]) -> tuple[str, Template, bool]:
    """(slug, template, ambiguous) from the qualifier word set and the family's legend rows."""
    words = {w.text for w in c.words}
    if words:
        exact = [t for t in family_templates if set(t.qualifiers) == words]
        if exact:
            return exact[0].slug, exact[0], False
        scored = [(len(words & set(t.qualifiers)) / len(words | set(t.qualifiers)), t) for t in family_templates]
        scored = [(s, t) for s, t in scored if s > 0]
        if scored:
            _, t = max(scored, key=lambda st: st[0])
            c.notes.append(f"qualifiers {sorted(words)} partially match row {t.row_id} {t.qualifiers}")
            return t.slug, t, True
        c.notes.append(f"qualifiers {sorted(words)} match no row of family {c.template.family}")
    unqualified = [t for t in family_templates if not t.qualifiers]
    if unqualified:
        t, d = nearest_template(c.shape, unqualified, c.merged.fills)
        if d <= THRESHOLD:
            return t.slug, t, bool(words)
    base = vocabulary.family_base(c.template.family)
    c.notes.append(f"no qualifier: nearest row {c.template.row_id} needs {c.template.qualifiers}; reported as {base}")
    return base, c.template, True


def _inside(inner: tuple, outer: tuple, tol: float = 0.5) -> bool:
    return (
        inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol
    )


def detect(page: pymupdf.Page, sheet: Sheet, templates: list[Template]) -> list[Detection]:
    frame = sheet.plan_frame
    clip = pymupdf.Rect(frame.x0, frame.y0, frame.x1, frame.y1)
    electrical = [t for t in templates if t.is_electrical]
    geo = geometry.extract(page, clip, max_segment_length=SEGMENT_CUT, vertical_words=True)
    merged = legend.merge_overlapping(geo.components, legend.fill_rects(page))
    symbols = [m for m in merged if geometry.symbol_sized([m.component])]
    frame_box = (frame.x0, frame.y0, frame.x1, frame.y1)
    cands = []
    for m in symbols:
        if not _inside(m.component.bbox, frame_box):
            continue
        c = _match_shape(m, electrical)
        if c is not None:
            cands.append(c)

    _assign_words(geo.words, cands, electrical)

    # a junction box drawn at the hub of a ceiling fan is part of the fan: the fixture counts once
    fans = [c for c in cands if c.template.slug == "ceiling_fan"]
    kept = []
    for c in cands:
        host = next((f for f in fans if f is not c and _inside(c.merged.component.bbox, f.merged.component.bbox)), None)
        if host is not None and c.template.family == "junction":
            host.notes.append(f"junction box at {tuple(round(v, 1) for v in c.merged.component.bbox)} inside the fan dropped")
            continue
        kept.append(c)

    by_family: dict[str, list[Template]] = collections.defaultdict(list)
    for t in electrical:
        by_family[t.family].append(t)
    detections = []
    kept.sort(key=lambda c: (round(c.merged.component.bbox[1] / 10), c.merged.component.bbox[0]))
    for n, c in enumerate(kept, start=1):
        slug, t, ambiguous = _resolve_subtype(c, by_family[c.template.family])
        confidence = (1 - c.distance) * (AMBIGUOUS_FACTOR if ambiguous else 1.0)
        detections.append(
            Detection(
                id=f"{sheet.id}-{n:03d}",
                slug=slug,
                family=c.template.family,
                bbox=c.merged.component.bbox,
                confidence=round(confidence, 3),
                evidence={
                    "template_row": t.row_id,
                    "template_slug": t.slug,
                    "shape_row": c.template.row_id,
                    "signature_distance": round(c.distance, 4),
                    "signature": list(c.merged.component.signature),
                    "component_ids": list(c.merged.member_ids),
                    "fills": c.merged.fills,
                    "qualifier_words": [{"text": w.text, "bbox": list(w.bbox)} for w in c.words],
                    "method": METHOD,
                    "notes": list(c.notes),
                },
            )
        )
    return detections


def render_detections(page: pymupdf.Page, sheet: Sheet, detections: list[Detection], out_png: str, dpi: int = 150):
    f = sheet.plan_frame
    clip = pymupdf.Rect(f.x0, f.y0, f.x1, f.y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    canvas = pymupdf.open()
    pg = canvas.new_page(width=clip.width, height=clip.height)
    pg.insert_image(pg.rect, pixmap=pix)
    for d in detections:
        x0, y0, x1, y1 = d.bbox
        color = legend.FAMILY_COLORS.get(d.family, (0, 0, 0))
        box = pymupdf.Rect(x0 - clip.x0 - 1.5, y0 - clip.y0 - 1.5, x1 - clip.x0 + 1.5, y1 - clip.y0 + 1.5)
        pg.draw_rect(box, color=color, width=0.6)
        pg.insert_text((box.x0, box.y1 + 4), d.slug, fontsize=3.2, color=color)
    pg.get_pixmap(dpi=dpi).save(out_png)
    canvas.close()


def main(argv: list[str] | None = None) -> int:
    """python -m takeoff.match <sheet_id> [...] -> data/sheets/<id>/detections.{json,png}, counts per slug"""
    from takeoff.ingest import load_sheet

    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(main.__doc__)
        return 2
    for sheet_id in args:
        sheet = load_sheet(sheet_id)
        page = pymupdf.open(str(sheet.pdf_path))[0]
        templates = legend.parse_legend(page, sheet.legend_frame)
        detections = detect(page, sheet, templates)
        folder = Path("data/sheets") / sheet_id
        (folder / "detections.json").write_text(json.dumps([asdict(d) for d in detections], indent=1))
        render_detections(page, sheet, detections, str(folder / "detections.png"))
        counts = collections.Counter(d.slug for d in detections)
        print(f"{sheet_id}: {len(detections)} detections")
        for slug, n in sorted(counts.items()):
            print(f"  {n:3d} {slug}")
        flagged = [d for d in detections if d.evidence["notes"]]
        if flagged:
            print(f"  {len(flagged)} with notes:")
            for d in flagged:
                print(f"    {d.id} {d.slug} at ({d.bbox[0]:.0f},{d.bbox[1]:.0f}): " + "; ".join(d.evidence["notes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
