"""Detection methods: same input (a sheet), same output (detections in PDF points), different ideas.

They are listed as a ladder, simplest first. Each rung exists to answer one question with a number
from `takeoff compare` against the answer key in eval/answer_key/:

    rung  name              idea                                               needs
    1     vision-page       brute force: show Claude the whole sheet          an image
    2     text              read the labels (GFI, AFI, 3D...) from the text    a text layer (or OCR on scans)
    3     template          classic computer vision: legend symbol as a stamp  an image
    4     geometry          match the drawing's own vector strokes to legend   a vector PDF
    5     vision-crop       Claude on the plan crop + the legend crop          an image
    6     vision-consensus  vision-crop twice; disagreements go to review      an image

Every method module exposes METHODS = {name: Method(...)} whose detect(sheet) -> list[Detection].
A method must only read the page through sheet.pdf_path, so the same method runs on a scanned copy.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from takeoff import vocabulary
from takeoff.ingest import Frame, Sheet
from takeoff.match import Detection

ORDER = ["vision-page", "text", "template", "geometry", "vision-crop", "vision-consensus", "agent", "agent-pair"]
_MODULES = {
    "vision-page": "takeoff.methods.vision",
    "text": "takeoff.methods.text",
    "template": "takeoff.methods.template",
    "geometry": "takeoff.methods.geometry",
    "vision-crop": "takeoff.methods.vision",
    "vision-consensus": "takeoff.methods.vision",
    "agent": "takeoff.methods.agent",
    "agent-pair": "takeoff.methods.agent",
}


@dataclass(frozen=True)
class Method:
    name: str
    rung: int
    needs: str
    idea: str
    detect: Callable[[Sheet], list[Detection]]


def get(name: str) -> Method:
    return importlib.import_module(_MODULES[name]).METHODS[name]


def available() -> list[str]:
    """Methods whose module is built (the ladder is filled in rung by rung)."""
    names = []
    for name in ORDER:
        try:
            get(name)
            names.append(name)
        except (ModuleNotFoundError, KeyError):
            pass
    return names


# ---- helpers shared by the methods ----

ELECTRICAL_SLUGS = [t.slug for t in vocabulary.TYPES if t.is_electrical]


def open_page(sheet: Sheet) -> pymupdf.Page:
    """The sheet's single page (ingest writes one page.pdf per sheet; the scan copy is one page too)."""
    return pymupdf.open(str(sheet.pdf_path))[0]


def rect(frame: Frame) -> pymupdf.Rect:
    return pymupdf.Rect(frame.x0, frame.y0, frame.x1, frame.y1)


def make_detection(sheet: Sheet, n: int, slug: str, cx: float, cy: float, confidence: float,
                   method: str, room: str | None = None, half: float = 6.0, **evidence) -> Detection:
    """A detection from a centre point (methods that see images report centres, not exact boxes)."""
    if slug not in ELECTRICAL_SLUGS:  # kept as "other" but never silently: review flags it with the raw type
        evidence = {**evidence, "raw_type": slug}
        slug = "other"
    return Detection(
        id=f"{sheet.id}-{method}-{n:03d}",
        slug=slug,
        family=vocabulary.lookup(slug).family,
        bbox=(cx - half, cy - half, cx + half, cy + half),
        confidence=round(float(confidence), 3),
        room=room,
        evidence={"method": method, **evidence},
    )
