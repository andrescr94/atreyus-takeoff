"""Rung 4, geometry: the drawing's own vector strokes matched to the sheet's legend symbols.

Exact positions and zero cost per sheet, but only on vector PDFs: a scanned page has no strokes,
so this method returns nothing there (which the scan column of `takeoff compare` shows).
The work is done by takeoff.legend (templates from the legend) and takeoff.match (plan symbols).
"""
from __future__ import annotations

from takeoff import legend, match
from takeoff.ingest import Sheet
from takeoff.methods import Method, open_page


def detect(sheet: Sheet) -> list[match.Detection]:
    page = open_page(sheet)
    if not page.get_drawings():
        return []
    templates = legend.parse_legend(page, sheet.legend_frame)
    return match.detect(page, sheet, templates)


METHODS = {
    "geometry": Method(
        name="geometry", rung=4, needs="vector PDF",
        idea="group the drawing's stroke segments into symbols and match each to the sheet's legend",
        detect=detect,
    )
}
