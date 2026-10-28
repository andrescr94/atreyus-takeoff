"""Run one sheet end to end with a chosen detection method and derive counts.

ingest (done once per PDF) -> detections (takeoff.methods, default vision-crop) -> room per detection
(the method's own room when it reads one, else wall polygons + labels) -> counts.
Counts are never stored: they are computed from the detections every time this runs, and the
same rule will live as a SQL view once the store exists (phase 6).
"""
from __future__ import annotations

import collections
import json
from dataclasses import asdict
from pathlib import Path

import pymupdf

from takeoff import match, methods, rooms
from takeoff.ingest import Sheet, load_sheet

DEFAULT_METHOD = "vision-crop"


def run_sheet(sheet: Sheet, out_dir: str | Path = "data/sheets", method: str = DEFAULT_METHOD) -> list[match.Detection]:
    """Detect every device on the sheet and attach its room. Writes detections.json next to the sheet."""
    page = pymupdf.open(sheet.pdf_path)[0]
    detections = attach_rooms(sheet, methods.get(method).detect(sheet))
    folder = Path(out_dir) / sheet.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "detections.json").write_text(json.dumps([asdict(d) for d in detections], indent=1))
    match.render_detections(page, sheet, detections, str(folder / "detections.png"))
    return detections


def attach_rooms(sheet: Sheet, detections: list[match.Detection]) -> list[match.Detection]:
    """Keep a room the model read; otherwise place the device with wall polygons + room labels."""
    found = None
    for d in detections:
        if d.room:
            d.evidence["room_method"] = "read by the model"
            continue
        found = found if found is not None else rooms.find_rooms(pymupdf.open(sheet.pdf_path)[0], sheet.plan_frame)
        d.room, how = rooms.assign_room(d.bbox, found)
        d.evidence["room_method"] = how
    return detections


def counts(detections: list[match.Detection]) -> dict[str, dict[str, int]]:
    """Per room x type, plus a 'TOTAL' row. Derived, never stored."""
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for d in detections:
        table[d.room or "?"][d.slug] += 1
        table["TOTAL"][d.slug] += 1
    return {room: dict(sorted(c.items())) for room, c in table.items()}


def print_counts(table: dict[str, dict[str, int]]) -> None:
    slugs = sorted(table["TOTAL"]) if "TOTAL" in table else []
    for room in [r for r in table if r != "TOTAL"] + ["TOTAL"]:
        row = table.get(room, {})
        print(f"{room:18s} " + "  ".join(f"{s}={n}" for s, n in row.items()) if room != "TOTAL" else "")
        if room == "TOTAL":
            print("TOTAL              " + "  ".join(f"{s}={row.get(s, 0)}" for s in slugs))


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sheet_id")
    ap.add_argument("--sheets", default="data/sheets")
    ap.add_argument("--method", default=DEFAULT_METHOD, choices=methods.ORDER)
    args = ap.parse_args(argv)
    sheet = load_sheet(args.sheet_id, args.sheets)
    dets = run_sheet(sheet, args.sheets, args.method)
    print(f"{sheet.id}: {len(dets)} detections ({args.method})")
    print_counts(counts(dets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
