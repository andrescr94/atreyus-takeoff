"""Rung 2, text: read the printed labels next to the symbols (GFI, AFI, 240V, 3D, SD...) and report
each label as a device at the label's position. No symbol is looked at.

Words come from the text layer when the plan frame has one, else from OCR (rapidocr) on a 300 DPI
render of the frame, run once upright and once rotated 90 degrees for the labels along vertical
walls. Unlabelled devices (recessed lights, plain switches, ceiling and exhaust fans) are missed by
design: that gap is what this rung measures.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np
import pymupdf

from takeoff.geometry import _collect_words
from takeoff.ingest import Sheet
from takeoff.match import Detection
from takeoff.methods import Method, make_detection, open_page, rect

# Receptacle qualifiers stack into one label ("WP" over "GFI" over "240V"); first match wins.
RECEPTACLE_LABELS = [("240V", "receptacle_240v"), ("WP", "receptacle_gfi_wp"), ("DW", "receptacle_gfi_dw"),
                     ("MW", "receptacle_gfi_mw"), ("GFI", "receptacle_gfi"), ("AFI", "receptacle_afi"),
                     ("FC", "receptacle")]
# Every one of these is a device on its own: "3D 3D" on one line is two switches.
SINGLE_LABELS = {"3D": "switch_3way_dimmer", "D": "switch_dimmer", "F": "switch_fan", "VS": "switch_vacancy",
                 "A": "switch_sensor", "SD/MA": "smoke_co", "SD": "smoke_detector", "IAQ": "iaq_fan",
                 "EXT": "light_wall_exterior"}
_RECEPTACLE = {text for text, _ in RECEPTACLE_LABELS}
VOCAB = sorted(_RECEPTACLE | set(SINGLE_LABELS), key=len, reverse=True)  # longest first for splitting

GROUP_GAP_PT = 2.0  # lines of one stack sit 0.5-1.1 pt apart; neighbouring stacks can be 4 pt apart
TEXT_CONFIDENCE = 0.6
OCR_DPI = 300
TILE_PX = 2000  # rapidocr shrinks any image whose long side exceeds 2000 px, which loses 6 pt labels
TILE_OVERLAP_PX = 150  # longer than any label at 300 DPI, so each label is whole in some tile
# OCR box width per character / box height: letters measure 0.5-1.0 at 300 DPI; a receptacle symbol
# read as "D" measures 1.15-1.25.
MIN_CHAR_ASPECT, MAX_CHAR_ASPECT = 0.3, 1.1
# Misreads seen on the 150 DPI scans (O/Q for 0, a lost or doubled I, a lost E).
OCR_FIXES = {"24QV": "240V", "24OV": "240V", "GF": "GFI", "GFF": "GFI", "XT": "EXT"}


@dataclass(frozen=True)
class Token:
    text: str
    bbox: tuple[float, float, float, float]  # PDF points
    score: float = 1.0


def split_label(word: str) -> list[str]:
    """A word as label tokens: itself if it is one, else its split into known labels ("3D3D" -> 3D, 3D;
    OCR joins neighbours), else [] (room names, heights, detail tags)."""
    word = word.strip().upper()
    if word in VOCAB:
        return [word]
    for v in VOCAB:
        if word.startswith(v) and len(word) > len(v):
            rest = split_label(word[len(v):])
            if rest:
                return [v, *rest]
    return []


def _gap(a: Token, b: Token) -> float:
    return max(a.bbox[0] - b.bbox[2], b.bbox[0] - a.bbox[2], a.bbox[1] - b.bbox[3], b.bbox[1] - a.bbox[3], 0.0)


def group_tokens(tokens: list[Token], gap: float = GROUP_GAP_PT) -> list[list[Token]]:
    """Receptacle qualifiers within `gap` points of each other form one label; any other label token
    is a group of its own. Tokens that are not labels are dropped."""
    recs = [t for t in tokens if t.text in _RECEPTACLE]
    groups = [[t] for t in tokens if t.text in SINGLE_LABELS]
    parent = list(range(len(recs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            if _gap(recs[i], recs[j]) <= gap:
                parent[find(i)] = find(j)
    stacks: dict[int, list[Token]] = {}
    for i, t in enumerate(recs):
        stacks.setdefault(find(i), []).append(t)
    return groups + list(stacks.values())


def label_slug(texts: list[str]) -> str | None:
    for text in texts:
        if text in SINGLE_LABELS:
            return SINGLE_LABELS[text]
    for text, slug in RECEPTACLE_LABELS:
        if text in texts:
            return slug
    return None


def text_layer_tokens(page: pymupdf.Page, clip: pymupdf.Rect) -> list[Token]:
    """Horizontal and vertical words of the text layer (the diagonal watermark is dropped)."""
    return [Token(w.text, w.bbox) for w in _collect_words(page, clip, vertical=True)]


@functools.cache
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _starts(length: int) -> list[int]:
    if length <= TILE_PX:
        return [0]
    step = TILE_PX - TILE_OVERLAP_PX
    return sorted(set(range(0, length - TILE_PX, step)) | {length - TILE_PX})


def _ocr_lines(img: np.ndarray) -> list[tuple[str, float, float, float, float, float]]:
    """(text, score, x0, y0, x1, y1) per OCR text line, in the image's pixels."""
    result, _ = _engine()(np.ascontiguousarray(img))
    lines = []
    for box, text, score in result or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        # Text running across this pass (a vertical label in the upright pass) still gets read, as a
        # box far narrower per character than it is tall; the other pass reads it properly. A box
        # far wider per character than tall is a misread of something else.
        char_aspect = (x1 - x0) / max(len(text), 1) / max(y1 - y0, 1)
        if MIN_CHAR_ASPECT <= char_aspect <= MAX_CHAR_ASPECT:
            lines.append((text, float(score), x0, y0, x1, y1))
    return lines


def _split_line(text: str, x0: float, y0: float, x1: float, y1: float):
    """Label tokens of one OCR line, each with its share of the line box (characters are about
    equally wide in this font). Yields (token, x0, y0, x1, y1) in the same image pixels."""
    per_char = (x1 - x0) / max(len(text), 1)
    pos = 0
    for word in text.split(" "):
        start = pos
        pos += len(word) + 1
        for part in split_label(OCR_FIXES.get(word, word)):
            yield part, x0 + start * per_char, y0, x0 + (start + len(part)) * per_char, y1
            start += len(part)


def _overlap(a: Token, b: Token) -> float:
    """Intersection area over the smaller box."""
    ix = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
    iy = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    area = min((t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]) for t in (a, b))
    return ix * iy / area if area else 0.0


def dedupe(tokens: list[Token], min_overlap: float = 0.5) -> list[Token]:
    """Best-scoring token wins where tiles overlap or both orientations read the same label."""
    kept: list[Token] = []
    for t in sorted(tokens, key=lambda t: -t.score):
        if all(_overlap(t, k) < min_overlap for k in kept):
            kept.append(t)
    return kept


def ocr_tokens(page: pymupdf.Page, clip: pymupdf.Rect) -> list[Token]:
    pix = page.get_pixmap(dpi=OCR_DPI, clip=clip, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    img = np.repeat(gray[:, :, None], 3, axis=2)
    pt = 72.0 / OCR_DPI
    tokens = []
    for ty in _starts(pix.h):
        for tx in _starts(pix.w):
            tile = img[ty:ty + TILE_PX, tx:tx + TILE_PX]
            h = tile.shape[0]
            for text, score, *box in _ocr_lines(tile):
                for part, x0, y0, x1, y1 in _split_line(text, *box):
                    tokens.append(Token(part, (clip.x0 + (tx + x0) * pt, clip.y0 + (ty + y0) * pt,
                                               clip.x0 + (tx + x1) * pt, clip.y0 + (ty + y1) * pt), score))
            # Turned clockwise, text reading bottom-to-top along a wall becomes a normal line.
            # Rotated pixel (xr, yr) is tile pixel (yr, h - 1 - xr).
            for text, score, *box in _ocr_lines(np.rot90(tile, -1)):
                for part, x0, y0, x1, y1 in _split_line(text, *box):
                    tokens.append(Token(part, (clip.x0 + (tx + y0) * pt, clip.y0 + (ty + h - 1 - x1) * pt,
                                               clip.x0 + (tx + y1) * pt, clip.y0 + (ty + h - 1 - x0) * pt), score))
    return dedupe(tokens)


def detect(sheet: Sheet) -> list[Detection]:
    page = open_page(sheet)
    clip = rect(sheet.plan_frame)
    tokens = dedupe(text_layer_tokens(page, clip))  # some labels are printed twice on the same spot
    source = "text layer"
    if not tokens:  # a scan: no text in the plan frame
        tokens, source = ocr_tokens(page, clip), "ocr"
    dets = []
    for group in group_tokens(tokens):
        slug = label_slug([t.text for t in group])
        if slug is None:
            continue
        x0, y0 = min(t.bbox[0] for t in group), min(t.bbox[1] for t in group)
        x1, y1 = max(t.bbox[2] for t in group), max(t.bbox[3] for t in group)
        conf = TEXT_CONFIDENCE if source == "text layer" else min(t.score for t in group) * TEXT_CONFIDENCE
        dets.append(make_detection(sheet, len(dets) + 1, slug, (x0 + x1) / 2, (y0 + y1) / 2, conf, method="text",
                                   tokens=[t.text for t in group], source=source))
    return dets


METHODS = {
    "text": Method(
        name="text", rung=2, needs="text layer or OCR",
        idea="read the device labels printed beside the symbols (GFI, AFI, 240V, 3D, SD...) and report each "
             "label as a device; unlabelled devices are missed by design",
        detect=detect,
    )
}
