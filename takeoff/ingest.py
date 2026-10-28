"""Phase 1, Ingest: split a plan set into sheets and locate the frames we count inside.

A *sheet* is one PDF page. On this plan set every sheet carries several plans laid out in a
grid of cells drawn with thin stroked lines. We want two frames per sheet: the ELECTRICAL PLAN
drawing and the LEGEND. Both are found from the title text, never from hardcoded positions.

A scanned sheet (one image, no text layer, no strokes) goes through the same rules: its words come
from OCR and its grid lines from the image (see _ocr_text and _raster_grid_lines).
"""

from __future__ import annotations

import functools
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

log = logging.getLogger(__name__)

SHEET_ID_RE = re.compile(r"^[A-Z]{1,3}[-.]?\d{1,3}(?:[.-]\d{1,3})?$")  # A1-111, A2, E-101, E1.01
# 1/4" = 1'-0"  ->  paper inches (a fraction or decimal), feet, inches
SCALE_RE = re.compile(r"""(\d+(?:/\d+)?|\d*\.\d+)\s*"\s*=\s*(\d+)\s*'\s*-?\s*(\d+)\s*\"""")
DEFAULT_SCALE_PT_PER_FT = 18.0


@dataclass(frozen=True)
class Frame:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Sheet:
    id: str
    page_index: int
    width_pt: float
    height_pt: float
    plan_frame: Frame
    legend_frame: Frame
    scale_pt_per_ft: float
    png_path: Path
    pdf_path: Path
    dpi: int
    scale_source: str = "text"  # "text" when read from the sheet, "default" when assumed
    frames_source: str = "rules"  # "rules" (titles + grid), or "model" when takeoff.locate had to find them

    def to_json(self) -> str:
        d = asdict(self)
        d["png_path"] = str(self.png_path)
        d["pdf_path"] = str(self.pdf_path)
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, text: str) -> Sheet:
        d = json.loads(text)
        d["plan_frame"] = Frame(**d["plan_frame"])
        d["legend_frame"] = Frame(**d["legend_frame"])
        d["png_path"] = Path(d["png_path"])
        d["pdf_path"] = Path(d["pdf_path"])
        return cls(**d)


# Sheet grid lines on this plan set are 0.84 pt (cell dividers) and 1.56 pt (outer border).
# Hatch and wall lines are thinner (0.24-0.72 pt); title underline bars are heavier (2.4 pt).
GRID_MIN_WIDTH = 0.5
GRID_MAX_WIDTH = 2.0
GRID_MIN_LENGTH = 400.0

# Scans. At 80 DPI the titles (25 pt), the sheet id (37 pt) and the scale captions (9.6 pt) all read, and a
# 36x24 in sheet is two of text.py's 2000 px OCR tiles overlapping by about 1000 pt, so every title is whole
# in one of them.
OCR_DPI = 80
OCR_EDGE_PX = 4  # an OCR line this close to an inner tile edge may be cut off; the other tile has it whole
# OCR reads the inch and foot marks of 1/4" = 1'-0" as these look-alikes.
OCR_QUOTES = str.maketrans({"*": '"', "°": '"', "”": '"', "“": '"', "″": '"', "′": "'", "’": "'", "‘": "'"})
GRID_RASTER_DPI = 150  # 0.48 pt per pixel; the 0.84 pt grid lines are about 2 px wide
GRID_MIN_STEADY = 0.7  # see _raster_grid_lines


def _has_text_layer(page: pymupdf.Page) -> bool:
    return bool(page.get_text("text").strip())


@functools.lru_cache(maxsize=1)
def _ocr_text(page: pymupdf.Page) -> tuple[tuple[str, pymupdf.Rect], ...]:
    """Text lines of a page without a text layer, read by OCR: (text, bbox in points) per printed line.

    The bbox height stands in for the font size. OCR splits some lines in pieces ("ELECTRICAL" and
    "PLAN - PLAN 1"); pieces on the same row less than half their height apart are joined with a space.
    """
    # Imported here: text.py imports this module.
    from takeoff.methods.text import TILE_PX, Token, _ocr_lines, _starts, dedupe

    pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    img = np.repeat(gray[:, :, None], 3, axis=2)
    pt = 72.0 / OCR_DPI
    tokens = []
    for ty in _starts(pix.h):
        for tx in _starts(pix.w):
            h, w = img[ty:ty + TILE_PX, tx:tx + TILE_PX].shape[:2]
            for text, score, x0, y0, x1, y1 in _ocr_lines(img[ty:ty + TILE_PX, tx:tx + TILE_PX]):
                if ((tx > 0 and x0 < OCR_EDGE_PX) or (tx + w < pix.w and x1 > w - OCR_EDGE_PX)
                        or (ty > 0 and y0 < OCR_EDGE_PX) or (ty + h < pix.h and y1 > h - OCR_EDGE_PX)):
                    continue
                bbox = tuple(float(v) for v in ((tx + x0) * pt, (ty + y0) * pt, (tx + x1) * pt, (ty + y1) * pt))
                tokens.append(Token(text.strip(), bbox, score))
    lines: list[list] = []  # [text, Rect]
    for tok in sorted(dedupe(tokens), key=lambda t: t.bbox[0]):  # the overlap of two tiles is read twice
        r = pymupdf.Rect(tok.bbox)
        for line in lines:
            b = line[1]
            tol = 0.25 * b.height
            if abs(r.y0 - b.y0) <= tol and abs(r.y1 - b.y1) <= tol and -tol <= r.x0 - b.x1 <= 0.5 * b.height:
                line[0] += " " + tok.text
                line[1] = b | r
                break
        else:
            lines.append([tok.text, r])
    return tuple((text, r) for text, r in lines)


def _starts_with_title(text: str, title: str) -> bool:
    """OCR line ``text`` starts with ``title`` as whole words, ignoring case and spaces (OCR drops and adds
    them: "ELECTRICALPLAN-PLAN 1"), with one misread letter per 10 ("ELECTRIGAL PLAN")."""
    want, got = title.replace(" ", "").upper(), text.upper()
    i = n = errors = 0
    while n < len(want) and i < len(got):
        if not got[i].isspace():
            errors += got[i] != want[n]
            n += 1
        i += 1
    return n == len(want) and errors <= len(want) // 10 and not got[i:i + 1].isalnum()


def _horizontal_spans(page: pymupdf.Page) -> list[dict]:
    """Horizontal text spans with their bbox, size and text (rotated text is dropped)."""
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if tuple(round(v) for v in line["dir"]) != (1, 0):
                continue
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append(span)
    return spans


def read_sheet_id(page: pymupdf.Page) -> str:
    """The sheet id is the largest text on the page shaped like a sheet number (A1-111, A2, E-101, E1.01)."""
    if not _has_text_layer(page):
        found = [(r.height, text.replace(" ", "")) for text, r in _ocr_text(page)
                 if SHEET_ID_RE.match(text.replace(" ", ""))]
        if not found:
            raise ValueError(f"no sheet id found on page {page.number}")
        return max(found)[1]
    candidates =[s for s in _horizontal_spans(page) if SHEET_ID_RE.match(s["text"].strip())]
    if not candidates:
        raise ValueError(f"no sheet id found on page {page.number}")
    return max(candidates, key=lambda s: s["size"])["text"].strip()


def _runs_by_size(spans: list[dict]) -> list[tuple[float, list[dict]]]:
    """Consecutive non-blank spans of the same font size, as (size, spans) runs."""
    runs: list[tuple[float, list[dict]]] = []
    for sp in spans:
        if not sp["text"].strip():
            continue
        size = round(sp["size"], 1)
        if runs and runs[-1][0] == size:
            runs[-1][1].append(sp)
        else:
            runs.append((size, [sp]))
    return runs


def find_title(page: pymupdf.Page, title: str) -> pymupdf.Rect:
    """Bounding box of the largest horizontal text line that starts with ``title`` as whole words.

    "ELECTRICAL PLAN" matches "ELECTRICAL PLAN - PLAN 1" but not "ELECTRICAL PLANS" (the rotated
    sheet-title text) nor "ELECTRICAL NOTES"; "LEGEND" does not match "FLOOR FINISH LEGEND".
    On a scan the lines come from OCR and match loosely (see _starts_with_title).
    """
    if not _has_text_layer(page):
        hits = [r for text, r in _ocr_text(page) if _starts_with_title(text, title)]
        if not hits:
            raise ValueError(f"title {title!r} not found on page {page.number}")
        return max(hits, key=lambda r: r.height)
    pattern =re.compile(r"^" + re.escape(title) + r"(?![A-Za-z0-9])")
    best = None
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if tuple(round(v) for v in line["dir"]) != (1, 0):
                continue
            # A line can mix a small reference ("A1-111") with the big title: group spans by size.
            for size, group in _runs_by_size(line["spans"]):
                text = "".join(sp["text"] for sp in group).strip()
                if pattern.match(text) and (best is None or size > best[0]):
                    bbox = pymupdf.Rect(group[0]["bbox"])
                    for sp in group[1:]:
                        bbox |= pymupdf.Rect(sp["bbox"])
                    best = (size, bbox)
    if best is None:
        raise ValueError(f"title {title!r} not found on page {page.number}")
    return best[1]


def _grid_lines(page: pymupdf.Page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Long, thin, axis-aligned stroked lines: (horizontal as (y, x0, x1), vertical as (x, y0, y1))."""
    horizontal, vertical = [], []
    for d in page.get_drawings():
        if "s" not in d["type"]:
            continue
        width = d.get("width") or 0.0
        if not GRID_MIN_WIDTH <= width <= GRID_MAX_WIDTH:
            continue
        for item in d["items"]:
            if item[0] != "l":
                continue
            p, q = item[1], item[2]
            if abs(p.y - q.y) < 0.5 and abs(p.x - q.x) >= GRID_MIN_LENGTH:
                horizontal.append((p.y, min(p.x, q.x), max(p.x, q.x)))
            elif abs(p.x - q.x) < 0.5 and abs(p.y - q.y) >= GRID_MIN_LENGTH:
                vertical.append((p.x, min(p.y, q.y), max(p.y, q.y)))
    if not horizontal and not vertical:  # a scan: no strokes, read the lines off the image
        return _raster_grid_lines(page)
    return horizontal, vertical


@functools.lru_cache(maxsize=1)
def _raster_grid_lines(page: pymupdf.Page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """The grid lines of a scanned page, in the shape _grid_lines returns.

    An opening with a GRID_MIN_LENGTH-long kernel keeps only ink runs at least that long. A run's width is
    its darkness summed across it, which gives back the drawn width (0.83 and 1.57 pt for the two grid pens).
    A grid line is one stroke, so that width holds along it; walls and dimension lines that also survive
    the opening are several strokes, ticks and text touching, so theirs varies. Keep runs of grid width
    whose width is within 10% of its median over GRID_MIN_STEADY of their length (on this plan set grid
    lines score 0.79-1.0, walls and dimension lines 0.10-0.63).
    """
    pix = page.get_pixmap(dpi=GRID_RASTER_DPI, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    dark = (255 - gray.astype(np.float32)) / 255
    pt = 72.0 / GRID_RASTER_DPI
    length = round(GRID_MIN_LENGTH / pt)
    found: tuple[list, list] = ([], [])
    for axis, kernel in ((0, (length, 1)), (1, (1, length))):  # horizontal, vertical
        runs = cv2.morphologyEx((gray < 128).astype(np.uint8), cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        for x, y, w, h, _ in cv2.connectedComponentsWithStats(runs)[2][1:]:
            if axis == 0:
                widths = dark[max(y - 3, 0):y + h + 3, x:x + w].sum(axis=0) * pt
                line = ((y + h / 2) * pt, x * pt, (x + w) * pt)
            else:
                widths = dark[y:y + h, max(x - 3, 0):x + w + 3].sum(axis=1) * pt
                line = ((x + w / 2) * pt, y * pt, (y + h) * pt)
            width = float(np.median(widths))
            steady = float(np.mean(np.abs(widths - width) <= 0.1 * width))
            if GRID_MIN_WIDTH <= width <= GRID_MAX_WIDTH and steady >= GRID_MIN_STEADY:
                found[axis].append(tuple(float(v) for v in line))
    return found


def find_frame(page: pymupdf.Page, title: str) -> Frame:
    """Locate the drawing region that belongs to the plan titled ``title``.

    Rule: the sheet is a grid of cells drawn with thin stroked lines (see GRID_* constants).
    1. Find the title text (largest line starting with ``title``).
    2. The cell is bounded by the nearest grid line on each side of the title that spans the
       title's full extent on the other axis (a line that does not reach across the title is
       part of another cell).
    3. The drawing sits on the side of the title with more room: plan titles are captions at the
       bottom of their cell (frame = cell above the title), legend titles are headers at the top
       (frame = cell below the title). The frame excludes the title band.

    Fragile points: a wall or hatch line longer than GRID_MIN_LENGTH with a grid-like width that
    reaches across the title would be mistaken for a cell edge; a title sharing a cell with a
    second block (sheet A2-111 LEGEND shares its cell with KEYNOTES above it) keeps the shared
    cell's far edge, which is harmless only because the other block is on the title's far side.
    """
    t = find_title(page, title)
    horizontal, vertical = _grid_lines(page)
    above = [y for y, x0, x1 in horizontal if y <= t.y0 and x0 <= t.x0 and x1 >= t.x1]
    below = [y for y, x0, x1 in horizontal if y >= t.y1 and x0 <= t.x0 and x1 >= t.x1]
    left = [x for x, y0, y1 in vertical if x <= t.x0 and y0 <= t.y0 and y1 >= t.y1]
    right = [x for x, y0, y1 in vertical if x >= t.x1 and y0 <= t.y0 and y1 >= t.y1]
    if not (above and below and left and right):
        raise ValueError(f"no grid cell around {title!r} on page {page.number}")
    cell = pymupdf.Rect(max(left), max(above), min(right), min(below))
    if t.y0 - cell.y0 >= cell.y1 - t.y1:
        return Frame(cell.x0, cell.y0, cell.x1, t.y0)
    return Frame(cell.x0, t.y1, cell.x1, cell.y1)


def find_scale(page: pymupdf.Page) -> float | None:
    """Points per foot from the SCALE: 1/4" = 1'-0" caption nearest the ELECTRICAL PLAN title.

    Returns None when no such caption exists on the page.
    """
    try:
        anchor = find_title(page, "ELECTRICAL PLAN")
    except ValueError:
        anchor = None
    if _has_text_layer(page):
        lines = [("".join(sp["text"] for sp in line["spans"]), pymupdf.Rect(line["bbox"]))
                 for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])]
    else:
        lines = [(text.translate(OCR_QUOTES), rect) for text, rect in _ocr_text(page)]
    found: list[tuple[float, float]] = []  # (distance to anchor, pt per ft)
    for text, rect in lines:
        m = SCALE_RE.search(text)
        if not m:
            continue
        paper_in = _fraction(m.group(1))
        feet = int(m.group(2)) + int(m.group(3)) / 12
        if paper_in <= 0 or feet <= 0:
            continue
        dist = abs(rect.tl - anchor.bl) if anchor is not None else 0.0
        found.append((dist, paper_in * 72 / feet))
    if not found:
        return None
    return min(found)[1]


def read_scale(page: pymupdf.Page) -> float:
    """Points per foot for the page; falls back to 1/4" = 1'-0" (18 pt/ft) with a logged warning."""
    scale = find_scale(page)
    if scale is None:
        log.warning("page %s: no scale text found, assuming %s pt/ft", page.number, DEFAULT_SCALE_PT_PER_FT)
        return DEFAULT_SCALE_PT_PER_FT
    return scale


def _fraction(text: str) -> float:
    if "/" in text:
        num, den = text.split("/")
        return int(num) / int(den) if int(den) else 0.0  # OCR can read 1/0
    return float(text)


def identify(page: pymupdf.Page, work_png: Path) -> tuple[str, Frame, Frame, str]:
    """(sheet id, plan frame, legend frame, how). Rules first; when a rule fails, the model reads the page image
    (takeoff.locate). A page that neither can read as an electrical plan with a legend raises ValueError."""
    try:
        return read_sheet_id(page), find_frame(page, "ELECTRICAL PLAN"), find_frame(page, "LEGEND"), "rules"
    except ValueError as rule_error:
        from takeoff import locate  # needs the model: only imported when the rules give up

        try:
            sid, plan, legend = locate.locate(page, work_png)
        except ValueError as e:
            raise ValueError(f"{e} (rules: {rule_error})") from e
        return sid or f"page-{page.number + 1}", Frame(*plan), Frame(*legend), "model"


def ingest(pdf_path: str | Path, out_dir: str | Path = "data/sheets", dpi: int = 100,
           errors: list | None = None) -> list[Sheet]:
    """Split ``pdf_path`` into one folder per sheet under ``out_dir``.

    Each folder ``<out_dir>/<sheet id>/`` gets ``page.pdf`` (the single page), ``page.png``
    (rendered at ``dpi``) and ``sheet.json`` (every Sheet field).
    A page that is not an electrical plan raises ValueError. With ``errors`` (a list), such a page is recorded
    there as (page number from 1, message) instead and the other pages go on.
    """
    out_dir = Path(out_dir)
    doc = pymupdf.open(pdf_path)
    if doc.page_count == 0:
        raise ValueError(f"{pdf_path}: the PDF has no pages")
    sheets = []
    for n in range(doc.page_count):
        # Each page becomes its own one-page PDF with any /Rotate baked into the content, so text, strokes,
        # renders and every coordinate we store share one upright frame (plans are often saved rotated).
        single = pymupdf.open()
        single.insert_pdf(doc, from_page=n, to_page=n)
        page = single[0]
        if page.rotation:
            page.remove_rotation()
        try:
            sheet_id, plan, legend, how = identify(page, out_dir / f"_page{n + 1}.png")
        except ValueError as e:
            if errors is None:
                raise
            errors.append((n + 1, str(e).replace("page 1 ", f"page {n + 1} ")))
            continue
        folder = out_dir / sheet_id
        folder.mkdir(parents=True, exist_ok=True)
        single.save(folder / "page.pdf")
        page = pymupdf.open(folder / "page.pdf")[0]
        sheet = _read_sheet(page, sheet_id, folder / "page.pdf", folder / "page.png", dpi, (plan, legend, how))
        sheet.page_index = n
        (folder / "sheet.json").write_text(sheet.to_json())
        sheets.append(sheet)
    return sheets


def ingest_page(pdf_path: str | Path, page_index: int = 0, dpi: int = 100,
                png_path: str | Path | None = None) -> Sheet:
    """The Sheet for one page of ``pdf_path``, read in place: nothing is split off or copied, so the
    Sheet's pdf_path is ``pdf_path`` itself (a scanned copy stays the page every method reads).

    Writes only the page render, to ``png_path`` (default: ``pdf_path`` with a .png suffix).
    """
    pdf_path = Path(pdf_path)
    png_path = pdf_path.with_suffix(".png") if png_path is None else Path(png_path)
    page = pymupdf.open(pdf_path)[page_index]
    sheet_id, plan, legend, how = identify(page, png_path.with_name(png_path.stem + "_locate.png"))
    return _read_sheet(page, sheet_id, pdf_path, png_path, dpi, (plan, legend, how))


def _read_sheet(page: pymupdf.Page, sheet_id: str, pdf_path: Path, png_path: Path, dpi: int,
                frames: tuple[Frame, Frame, str]) -> Sheet:
    """Scale of ``page`` plus the frames already found; renders it to ``png_path``."""
    page.get_pixmap(dpi=dpi).save(png_path)
    scale = find_scale(page)
    plan, legend, how = frames
    return Sheet(
        id=sheet_id,
        page_index=page.number,
        width_pt=page.rect.width,
        height_pt=page.rect.height,
        plan_frame=plan,
        legend_frame=legend,
        scale_pt_per_ft=read_scale(page) if scale is None else scale,
        png_path=png_path,
        pdf_path=pdf_path,
        dpi=dpi,
        scale_source="text" if scale is not None else "default",
        frames_source=how,
    )


def load_sheet(sheet_id: str, out_dir: str | Path = "data/sheets") -> Sheet:
    """Read ``<out_dir>/<sheet_id>/sheet.json`` back into a Sheet."""
    return Sheet.from_json((Path(out_dir) / sheet_id / "sheet.json").read_text())


def _fmt(frame: Frame) -> str:
    return f"({frame.x0:.1f}, {frame.y0:.1f}, {frame.x1:.1f}, {frame.y1:.1f})"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m takeoff.ingest", description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--out", default="data/sheets", help="output folder (default: data/sheets)")
    parser.add_argument("--dpi", type=int, default=100, help="render resolution for page.png (default: 100)")
    args = parser.parse_args(argv)
    for s in ingest(args.pdf, out_dir=args.out, dpi=args.dpi):
        flag = "" if s.scale_source == "text" else " (assumed)"
        print(
            f"{s.id} page {s.page_index} {s.width_pt:.0f}x{s.height_pt:.0f}pt "
            f"plan={_fmt(s.plan_frame)} legend={_fmt(s.legend_frame)} "
            f"{s.scale_pt_per_ft:.1f} pt/ft{flag} -> {s.pdf_path.parent}"
        )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
