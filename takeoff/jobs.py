"""Upload -> sheets -> devices: the one code path behind the web upload and `takeoff import`.

start() checks the PDF, stores it, makes a project with one queued sheet per page and hands every page
to a pool of worker processes. A worker (read_page) runs ingest on that page alone, the detection
method, rooms and review flags, and writes the result to the store; a page that is not an electrical
plan sheet ends as status "error" with the message, and the other pages carry on. Ingest reads the frames with
rules first and asks the model (takeoff.locate) only when they fail, so an odd page can take a minute.
Processes rather than threads: PyMuPDF is not thread-safe, and each process has its own llm.LEDGER, so
the model calls' tokens and seconds belong to exactly one sheet.

    TAKEOFF_METHOD   detection method (default vision-consensus; "geometry" is free and takes ~1 s)
    TAKEOFF_WORKERS  pages read at the same time (default 4)
"""
from __future__ import annotations

import concurrent.futures
import multiprocessing
import os
import re
import sys
import time
from pathlib import Path

import pymupdf

from takeoff import ingest, llm, methods, pipeline, review, rooms
from takeoff.store import Store, now

DEFAULT_METHOD = "vision-consensus"
LOW_CONFIDENCE = review.LOW  # below this a device goes to "Needs a look" (measured calibration, review.py)
_POOL: concurrent.futures.Executor | None = None


def method_name() -> str:
    name = os.environ.get("TAKEOFF_METHOD", DEFAULT_METHOD)
    if name not in methods.ORDER:
        raise ValueError(f"TAKEOFF_METHOD={name!r} is not one of {', '.join(methods.ORDER)}")
    return name


def pool() -> concurrent.futures.Executor:
    """The worker processes (tests put a thread pool in _POOL to run pages in-process)."""
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ProcessPoolExecutor(
            max_workers=int(os.environ.get("TAKEOFF_WORKERS", "4")), mp_context=multiprocessing.get_context("spawn"))
    return _POOL


def check_pdf(data: bytes) -> int:
    """Number of pages, or ValueError with a message a person can act on."""
    if not data:
        raise ValueError("The file is empty.")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise ValueError("This file is not a PDF. Upload the plan set as a PDF.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"The PDF could not be opened: {e}") from e
    if doc.page_count == 0:
        raise ValueError("The PDF has no pages.")
    return doc.page_count


def start(store: Store, data_dir: Path, filename: str, data: bytes, method: str | None = None,
          ) -> tuple[int, list[concurrent.futures.Future]]:
    """New project from the PDF bytes; every page is queued for reading. Returns (project id, futures)."""
    pages = check_pdf(data)
    method = method or method_name()
    name = Path(filename).stem.replace("_", " ")
    pid = store.create_project(name, filename, "", method, pages)
    folder = Path(data_dir) / "projects" / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "upload.pdf").write_bytes(data)
    store.set_pdf_path(pid, str(folder / "upload.pdf"))
    return pid, [submit(store, s["id"]) for s in store.sheets(pid)]


def submit(store: Store, sheet_id: int) -> concurrent.futures.Future:
    future = pool().submit(read_page, str(store.path), sheet_id)
    future.add_done_callback(lambda f: _crashed(store, sheet_id, f))
    return future


def _crashed(store: Store, sheet_id: int, future: concurrent.futures.Future) -> None:
    """read_page records its own errors; this catches a worker process that died."""
    reason = "cancelled" if future.cancelled() else future.exception()
    if reason is not None:
        store.update_sheet(sheet_id, status="error", stage=None, finished_at=now(),
                           error=f"The worker reading this sheet stopped: {reason}")


def resume(store: Store) -> int:
    """Queue again the sheets a stopped server left half read."""
    stale = store.unfinished_sheets()
    for s in stale:
        store.reset_sheet(s["id"])
        submit(store, s["id"])
    return len(stale)


def _split_page(pdf_path: str, page_index: int, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open(pdf_path)
    single = pymupdf.open()
    single.insert_pdf(src, from_page=page_index, to_page=page_index)
    single.save(out)
    return out


def _device(d, status: str, method: str) -> dict:
    x0, y0, x1, y1 = (float(v) for v in d.bbox)
    ev = {**d.evidence, "model_type": d.slug}
    return {
        "type": d.slug, "family": d.family, "x": (x0 + x1) / 2, "y": (y0 + y1) / 2, "w": x1 - x0, "h": y1 - y0,
        "room": d.room, "confidence": float(d.confidence), "status": status, "flag": ev.get("flag"),
        "source": ev.get("method") or method, "alternatives": list(ev.get("alternatives", [])), "evidence": ev,
    }


def _ledger_stats(answers: list) -> dict:
    def total(field: str):
        values = [getattr(a, field, None) for a in answers]
        values = [v for v in values if v is not None]
        return sum(values) if values else None

    return {"model_calls": len(answers), "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
            "model_seconds": total("seconds"), "cost_usd": total("cost_usd")}


def read_page(db_path: str, sheet_id: int) -> None:
    """Worker: ingest one page, detect, place in rooms, flag; every outcome is written to the store."""
    store = Store(db_path)
    row = store.sheet(sheet_id)
    project = store.project(row["project_id"])
    folder = Path(project["pdf_path"]).parent
    t0 = time.time()
    n = row["page_index"] + 1
    store.update_sheet(sheet_id, status="reading", stage="Finding the plan and legend", started_at=now())
    llm.LEDGER.clear()
    try:
        page_pdf = _split_page(project["pdf_path"], row["page_index"], folder / "pages" / f"{n}.pdf")
        try:
            sheet = ingest.ingest(page_pdf, folder / "sheets" / str(n))[0]
        except ValueError as e:
            reason = re.sub(r"^page \d+ |,? ?on page \d+", "", str(e)).rstrip(".")
            msg = f"Page {n} {reason}." if reason.startswith("is not") else f"Page {n} is not an electrical plan sheet: {reason}."
            raise ValueError(msg) from e
        name = f"page-{n}" if re.fullmatch(r"page-\d+", sheet.id) else sheet.id  # the model found no sheet id
        twin = store.sheet_name_taken(project["id"], name, sheet_id)
        if twin is not None:
            raise ValueError(f"Sheet {name} is also page {twin['page_index'] + 1} of this PDF; it is read once only.")
        store.update_sheet(
            sheet_id, name=name, sheet_dir=str(Path(sheet.pdf_path).parent), width_pt=sheet.width_pt,
            height_pt=sheet.height_pt, plan_frame=vars(sheet.plan_frame), legend_frame=vars(sheet.legend_frame),
            frames_source=getattr(sheet, "frames_source", "rules"), stage=f"Detecting devices ({row['method']})")
        found = pipeline.attach_rooms(sheet, methods.get(row["method"]).detect(sheet))
        answers = list(llm.LEDGER)
        store.update_sheet(sheet_id, stage="Checking for missed devices")
        found, suggestions = review.flag(sheet, found)
        pipeline.attach_rooms(sheet, suggestions)
        page = pymupdf.open(sheet.pdf_path)[0]
        names = {r.name for r in rooms.find_rooms(page, sheet.plan_frame) if r.is_room}
        names |= {d.room for d in found if d.room}
        for d in found:
            if not d.evidence.get("flag") and d.confidence < LOW_CONFIDENCE:
                notes = d.evidence.get("notes") or []
                d.evidence["flag"] = f"Low confidence ({round(d.confidence * 100)}%)" + (f": {notes[0]}" if notes else "")
        devices = [_device(d, "review" if d.evidence.get("flag") else "agreed", row["method"]) for d in found]
        devices += [_device(d, "suggested", row["method"]) for d in suggestions]
        store.add_detections(sheet_id, devices)
        store.update_sheet(sheet_id, status="ready", stage=None, rooms=sorted(names - {rooms.EXTERIOR}) + [rooms.EXTERIOR],
                           elapsed_seconds=round(time.time() - t0, 1), finished_at=now(), **_ledger_stats(answers))
    except Exception as e:  # noqa: BLE001 - the sheet shows the message; the other sheets carry on
        store.update_sheet(sheet_id, status="error", stage=None, error=str(e) or type(e).__name__,
                           elapsed_seconds=round(time.time() - t0, 1), finished_at=now(), **_ledger_stats(list(llm.LEDGER)))


def main(argv: list[str] | None = None) -> int:
    """takeoff import <pdf> [--method M] [--data DIR]: the web upload without the browser; waits for every sheet."""
    import argparse

    ap = argparse.ArgumentParser(prog="takeoff import", description=main.__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--method", default=None, choices=methods.ORDER, help=f"default $TAKEOFF_METHOD or {DEFAULT_METHOD}")
    ap.add_argument("--data", default="data", help="data folder (default: data)")
    args = ap.parse_args(argv)
    store = Store(Path(args.data) / "takeoff.db")
    try:
        pid, futures = start(store, Path(args.data), Path(args.pdf).name, Path(args.pdf).read_bytes(), args.method)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"project {pid}: {len(futures)} page(s), method {store.project(pid)['method']}")
    concurrent.futures.wait(futures)
    failed = 0
    for s in store.sheets(pid):
        n = store.status_counts(s["id"])
        if s["status"] == "ready":
            print(f"  {s['name']}: {sum(n.values())} devices, {n.get('review', 0)} to review, "
                  f"{n.get('suggested', 0)} suggested ({s['elapsed_seconds']} s)")
        else:
            failed += 1
            print(f"  page {s['page_index'] + 1}: {s['status']}: {s['error']}")
    print(f"open http://localhost:8000/#/p/{pid}")
    return 1 if failed else 0
