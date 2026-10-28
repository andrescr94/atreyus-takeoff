"""Unseen sheets from other drafters: does the whole pipeline run, and what does it find? No answer key exists for
these, so this shows generality (ingest, frames, detection, flags), not accuracy.

    uv run python eval/heldout.py [--method vision-consensus]

Reads data/heldout/*.pdf (one sheet each; sources and terms in docs/heldout.md), writes docs/heldout.md and an
overlay per sheet in docs/heldout/ (device boxes coloured by family; red ring = flagged, needs a look).
"""
from __future__ import annotations

import argparse
import collections
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

from takeoff import llm, methods, pipeline, review
from takeoff.ingest import ingest

SRC = Path("data/heldout")
OUT = Path("docs/heldout")
RGB = {"fan": (0.85, 0.1, 0.1), "light": (0.95, 0.6, 0.0), "switch": (0.1, 0.35, 0.9),
       "receptacle": (0.0, 0.6, 0.2), "detector": (0.6, 0.0, 0.8)}


def one(pdf: Path, method: str) -> dict:
    row = {"file": pdf.name}
    errors: list = []
    t0 = time.time()
    try:
        sheets = ingest(pdf, out_dir=SRC / "sheets" / pdf.stem, errors=errors)
    except Exception as e:  # noqa: BLE001 - reported in the table, the run goes on
        return {**row, "error": f"{type(e).__name__}: {e}"}
    if not sheets:
        return {**row, "error": "; ".join(m for _, m in errors)}
    sheet = sheets[0]
    llm.LEDGER.clear()
    try:
        dets = pipeline.attach_rooms(sheet, methods.get(method).detect(sheet))
        dets, sugg = review.flag(sheet, dets)
    except Exception as e:  # noqa: BLE001
        return {**row, "sheet": sheet.id, "frames": sheet.frames_source, "error": f"{type(e).__name__}: {e}"}
    page = pymupdf.open(sheet.pdf_path)[0]
    for d in dets + sugg:
        r = pymupdf.Rect(d.bbox)
        page.draw_rect(r, color=RGB.get(d.family, (0.3, 0.3, 0.3)), width=1)
        if d.confidence < review.LOW:
            page.draw_circle(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2), 11, color=(1, 0, 0), width=1.2)
    f = sheet.plan_frame
    OUT.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(dpi=80, clip=pymupdf.Rect(f.x0, f.y0, f.x1, f.y1)).save(str(OUT / f"{pdf.stem}.png"))
    fam = collections.Counter(d.family for d in dets)
    return {**row, "sheet": sheet.id, "frames": sheet.frames_source, "devices": len(dets),
            "families": ", ".join(f"{n} {k}" for k, n in fam.most_common()),
            "low": sum(d.confidence < review.LOW for d in dets), "suggested": len(sugg),
            "rooms": len({d.room for d in dets}), "seconds": round(time.time() - t0),
            "tokens": sum(a.input_tokens or 0 for a in llm.LEDGER)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="vision-consensus", choices=methods.ORDER)
    a = ap.parse_args()
    pdfs = sorted(SRC.glob("*.pdf"))
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(lambda p: one(p, a.method), pdfs))
    lines = ["# Unseen sheets: does it generalise?", "",
             (f"Method {a.method}, run {time.strftime('%Y-%m-%d')} by `uv run python eval/heldout.py`. No answer key: "
             "these show that ingest, detection and review flags run on other drafters' sheets, not how accurate "
             "they are. Overlays: docs/heldout/<file>.png (red ring = confidence under 90%, needs a look)."), "",
             ("| File | Sheet | Plan/legend found by | Devices | Families | Needs a look | Suggested misses | Rooms "
             "| s | Tokens in | Error |"), "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['file']} | {r.get('sheet', '-')} | {r.get('frames', '-')} | {r.get('devices', '-')} | "
                     f"{r.get('families', '-')} | {r.get('low', '-')} | {r.get('suggested', '-')} | "
                     f"{r.get('rooms', '-')} | {r.get('seconds', '-')} | {r.get('tokens', '-')} | "
                     f"{r.get('error', '')[:160]} |")
    lines += ["", ("Sources (public pre-approved ADU programs; most restrict reuse to their city, so the PDFs stay "
              "out of git): Escondido PAADU (the 4 key sheets), San Diego County PDS-670, Orange County (Design Path "
              "Studio), Laguna Niguel Plan 1 (public domain), Upland 440R, El Centro Plan B, Garden Grove ADUGO-CP-1, "
              "Santa Ana Craftsman.")]
    Path("docs/heldout.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
