"""Find the electrical plan and its legend with the model, for sheets the title-and-grid rules cannot read.

takeoff.ingest first tries rules (the sheet id text, the ELECTRICAL PLAN and LEGEND titles, the grid cell around
each). They were written for one drafter's title blocks; another drafter names, places and frames things
differently. When a rule fails, the page image goes to Claude with a schema: is there an electrical plan with a
legend, what is the sheet id, where are the two regions. A page that is not an electrical plan raises with the
model's reason, so a stray image or a cover sheet fails loudly instead of yielding zero devices.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from takeoff import llm

TARGET_PX = 2000
MARGIN = 0.01  # fraction of the page added around each region: model boxes are approximate

_BOX = {"type": "object", "additionalProperties": False, "required": ["x0", "y0", "x1", "y1"],
        "properties": {k: {"type": "number", "minimum": 0, "maximum": 1} for k in ("x0", "y0", "x1", "y1")}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_electrical_plan", "problem", "sheet_id", "plan", "legend"],
    "properties": {
        "is_electrical_plan": {"type": "boolean"},
        "problem": {"type": "string", "description": "what the page is instead, when it is not an electrical plan"},
        "sheet_id": {"type": "string", "description": "the sheet number in the title block, empty if none"},
        "plan": _BOX,
        "legend": _BOX,
    },
}

PROMPT = """The image is one page of a construction document. Decide whether it contains an electrical plan (a \
floor plan with electrical device symbols: lights, switches, receptacles, fans) AND a symbol legend for it.
If yes: give the sheet number printed in the title block, and the bounding box of the electrical plan drawing \
(walls and devices, not its notes) and of the electrical symbol legend table, as fractions of the image width and \
height (x from left, y from top). If no: set is_electrical_plan false, say in problem what the page is, and give \
zero boxes."""


def locate(page: pymupdf.Page, out_png: Path) -> tuple[str, pymupdf.Rect, pymupdf.Rect]:
    """(sheet id, plan rect, legend rect) in PDF points. Raises ValueError when the page is not an electrical plan."""
    zoom = TARGET_PX / max(page.rect.width, page.rect.height)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(str(out_png))
    a = llm.ask(PROMPT, [out_png], schema=SCHEMA)
    d = a.data
    if not d["is_electrical_plan"]:
        raise ValueError(f"page {page.number + 1} is not an electrical plan with a legend: {d['problem']}")

    def box(b: dict) -> pymupdf.Rect:
        w, h = page.rect.width, page.rect.height
        r = pymupdf.Rect((b["x0"] - MARGIN) * w, (b["y0"] - MARGIN) * h, (b["x1"] + MARGIN) * w, (b["y1"] + MARGIN) * h)
        r &= page.rect
        if r.is_empty or r.width < 0.05 * w or r.height < 0.03 * h:
            raise ValueError(f"page {page.number + 1}: the model's {b} is not a usable region")
        return r

    return d["sheet_id"].strip(), box(d["plan"]), box(d["legend"])
