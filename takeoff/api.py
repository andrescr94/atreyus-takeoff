"""Phase 6, API: the web app's JSON endpoints over takeoff.store, plus the plan images and the exports.

    POST   /api/projects                     upload a PDF (multipart "file"); pages are read in the background
    GET    /api/projects                     every project with per-sheet progress
    GET    /api/projects/{id}                one project, its sheets' status, stage, errors and model usage
    GET    /api/projects/{id}/counts         live devices per sheet x room x type, with totals
    GET    /api/projects/{id}/events         the audit trail
    GET    /api/projects/{id}/export.csv     counts per sheet, room and type
    GET    /api/projects/{id}/export.json    every device with its status and the audit trail
    GET    /api/projects/{id}/export.pdf     each sheet with every live device boxed and labelled
    GET    /api/sheets/{id}                  sheet, frames, rooms and devices
    GET    /api/sheets/{id}/plan.png         the plan frame at PLAN_PX_PER_PT pixels per point
    GET    /api/sheets/{id}/legend.png       the legend frame
    GET    /api/sheets/{id}/page.png         the whole page, small (to show where the frames were found)
    POST   /api/sheets/{id}/confirm          a stage looks right {stage: sheet|legend|rooms|devices} (optional)
    POST   /api/sheets/{id}/retry            read the sheet again (after an error)
    POST   /api/sheets/{id}/devices          add a missed device {type, x, y, room?}
    POST   /api/sheets/{id}/accept-agreed    verify every agreed device {type?}
    PATCH  /api/devices/{id}                 change type, room, x, y or status
    DELETE /api/devices/{id}                 not a device (soft: status removed)
    POST   /api/devices/{id}/verify          looks right
    POST   /api/devices/{id}/restore         undo a delete

Nothing waits on a person: counts and exports use the AI's devices as soon as a sheet is read, and say how
many a person confirmed. Every mutation is an event in the store. Coordinates are PDF points on the sheet's page; the client maps
them to pixels with the plan frame. The web app (web/) is served at /.
"""
from __future__ import annotations

import csv
import functools
import io
import threading
from pathlib import Path
from typing import Annotated

import pymupdf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from takeoff import bom, jobs, legend, methods, rooms, vocabulary
from takeoff.store import CHECKED, CONFIRMED, COUNTED, STATUSES, Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PLAN_PX_PER_PT = 3  # 216 DPI: symbols stay sharp at 200% zoom and in the close-up
TYPES = ELECTRICAL = methods.ELECTRICAL_SLUGS
LABELS = {
    "receptacle": "Duplex receptacle", "receptacle_afi": "AFCI receptacle", "receptacle_gfi": "GFCI receptacle",
    "receptacle_240v": "240V receptacle", "receptacle_gfi_wp": "GFCI receptacle, weatherproof",
    "receptacle_half_hot": "Half-hot receptacle", "receptacle_gfi_mw": "GFCI receptacle, microwave",
    "receptacle_gfi_dw": "GFCI receptacle, dishwasher", "receptacle_hood": "Range hood receptacle",
    "switch": "Switch", "switch_dimmer": "Dimmer switch", "switch_3way_dimmer": "3-way dimmer switch",
    "switch_fan": "Fan switch", "switch_sensor": "Sensor switch", "switch_vacancy": "Vacancy switch",
    "light": "Light", "light_recessed": "Recessed light", "light_recessed_vp": "Recessed light, vapor proof",
    "light_wall": "Wall light", "light_wall_exterior": "Exterior wall light", "light_pendant": "Pendant light",
    "fan": "Fan", "ceiling_fan": "Ceiling fan", "exhaust_fan": "Exhaust fan", "iaq_fan": "Indoor air quality fan",
    "detector": "Detector", "smoke_detector": "Smoke detector", "smoke_co": "Smoke / CO detector",
    "junction_box": "Junction box", "other": "Other (not in the legend list)",
}
_PDF = threading.Lock()  # PyMuPDF is not thread-safe; request threads render one at a time


def label(slug: str) -> str:
    return LABELS.get(slug, slug.replace("_", " ").capitalize())


class DeviceIn(BaseModel):
    type: str
    x: float
    y: float
    room: str | None = None


class DevicePatch(BaseModel):
    type: str | None = None
    room: str | None = None
    x: float | None = None
    y: float | None = None
    status: str | None = None


class AcceptIn(BaseModel):
    type: str | None = None


class ConfirmIn(BaseModel):
    stage: str


STAGES = ("sheet", "legend", "rooms", "devices")


# ---- shaping ----

EVIDENCE_KEYS = ("model_type", "model", "agreement", "raw_type", "room_method", "notes", "method", "suggested")


def device_json(d: dict) -> dict:
    ev = d.get("evidence") or {}
    return {
        **{k: d[k] for k in ("id", "sheet_id", "type", "family", "x", "y", "w", "h", "room", "confidence", "status",
                             "initial_status", "flag", "source", "alternatives", "created_at", "updated_at")},
        "label": label(d["type"]),
        "evidence": {k: ev[k] for k in EVIDENCE_KEYS if k in ev},
    }


def sheet_json(store: Store, s: dict) -> dict:
    n = store.status_counts(s["id"])
    return {
        **{k: s[k] for k in ("id", "project_id", "page_index", "name", "status", "stage", "error", "method", "width_pt",
                             "height_pt", "plan_frame", "legend_frame", "frames_source", "rooms", "confirmed", "model_calls",
                             "input_tokens", "output_tokens", "model_seconds", "cost_usd", "elapsed_seconds")},
        "title": s["name"] or f"Page {s['page_index'] + 1}",
        "status_counts": n,
        "live": sum(n.get(k, 0) for k in COUNTED),
        "to_review": n.get("review", 0) + n.get("suggested", 0),
        "checked": sum(n.get(k, 0) for k in CHECKED),
        "confirmed_devices": sum(n.get(k, 0) for k in CONFIRMED),
        "total": sum(n.values()),
        "plan_px_per_pt": PLAN_PX_PER_PT,
    }


def project_json(store: Store, p: dict) -> dict:
    sheets = [sheet_json(store, s) for s in store.sheets(p["id"])]
    done = sum(s["status"] in ("ready", "error") for s in sheets)
    return {
        **p, "sheets": sheets,
        "progress": {"done": done, "total": len(sheets), "reading": sum(s["status"] == "reading" for s in sheets)},
        "live": sum(s["live"] for s in sheets),
        "to_review": sum(s["to_review"] for s in sheets),
        "errors": sum(s["status"] == "error" for s in sheets),
    }


# ---- pages and rooms ----

def _clip(frame: dict) -> pymupdf.Rect:
    return pymupdf.Rect(frame["x0"], frame["y0"], frame["x1"], frame["y1"])


def _png(s: dict, frame_key: str | None, name: str, zoom: float) -> Path:
    """Render a frame of the sheet (None: the whole page) once and keep it next to the sheet."""
    out = Path(s["sheet_dir"]) / name
    with _PDF:
        if not out.exists():
            page = pymupdf.open(Path(s["sheet_dir"]) / "page.pdf")[0]
            clip = _clip(s[frame_key]) if frame_key else None
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).save(str(out))
    return out


@functools.lru_cache(maxsize=32)
def _rooms(sheet_dir: str, x0: float, y0: float, x1: float, y1: float) -> list:
    with _PDF:
        return rooms.find_rooms(pymupdf.open(Path(sheet_dir) / "page.pdf")[0], pymupdf.Rect(x0, y0, x1, y1))


def room_at(store: Store, s: dict, x: float, y: float) -> str:
    """The room drawn around the point; on a scan (no wall fills) the room of the nearest device."""
    f = s["plan_frame"]
    found = _rooms(s["sheet_dir"], f["x0"], f["y0"], f["x1"], f["y1"])
    if any(r.is_room for r in found):
        return rooms.assign_room((x, y), found)[0]
    live = [d for d in store.devices(s["id"]) if d["status"] in COUNTED and d["room"]]
    if not live:
        return rooms.EXTERIOR
    return min(live, key=lambda d: (d["x"] - x) ** 2 + (d["y"] - y) ** 2)["room"]


# ---- exports ----

def counts_json(store: Store, pid: int) -> dict:
    rows = store.counts(pid)
    sheets = [{"id": s["id"], "name": s["name"] or f"Page {s['page_index'] + 1}", "status": s["status"]}
              for s in store.sheets(pid)]
    totals = {s["id"]: 0 for s in sheets}
    for r in rows:
        totals[r["sheet_id"]] += r["n"]
        r["label"] = label(r["type"])
    return {"sheets": sheets, "rows": rows, "sheet_totals": totals, "total": sum(totals.values()),
            "confirmed": sum(r["confirmed"] for r in rows)}


def counts_csv(store: Store, pid: int) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sheet", "room", "type", "description", "family", "count", "confirmed_by_person"])
    for r in store.counts(pid):
        w.writerow([r["sheet"], r["room"], r["type"], label(r["type"]), r["family"], r["n"], r["confirmed"]])
    return buf.getvalue()


def marked_pdf(store: Store, pid: int) -> bytes:
    """Every ready sheet with each live device boxed in its family colour and labelled with its type."""
    out = pymupdf.open()
    with _PDF:
        for s in store.sheets(pid):
            if s["status"] != "ready":
                continue
            out.insert_pdf(pymupdf.open(Path(s["sheet_dir"]) / "page.pdf"))
            page = out[-1]
            live = [d for d in store.devices(s["id"]) if d["status"] in COUNTED]
            for d in live:
                color = legend.FAMILY_COLORS.get(d["family"], (0.4, 0.4, 0.4))
                half_w, half_h = max(d["w"], 8) / 2 + 2, max(d["h"], 8) / 2 + 2
                box = pymupdf.Rect(d["x"] - half_w, d["y"] - half_h, d["x"] + half_w, d["y"] + half_h)
                page.draw_rect(box, color=color, width=0.8)
                page.insert_text((box.x0, box.y1 + 4.5), d["type"], fontsize=4, color=color)
            f = s["plan_frame"]
            page.insert_text((f["x0"] + 8, f["y0"] + 16), f"Takeoff {s['name']}: {len(live)} devices", fontsize=10,
                             color=(0.42, 0.25, 0.97))
    if out.page_count == 0:
        out.new_page().insert_text((72, 72), "No sheet of this project is ready yet.", fontsize=12)
    return out.tobytes(garbage=3, deflate=True)


# ---- the app ----

def create_app(data_dir: str | Path = "data", resume: bool = True) -> FastAPI:
    data_dir = Path(data_dir)
    store = Store(data_dir / "takeoff.db")
    app = FastAPI(title="Takeoff", description=__doc__)
    app.state.store = store
    if resume:
        jobs.resume(store)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"detail": msg}, status_code=422)

    def get_project(pid: int) -> dict:
        p = store.project(pid)
        if p is None:
            raise HTTPException(404, f"No project {pid}.")
        return p

    def get_sheet(sid: int, ready: bool = False) -> dict:
        s = store.sheet(sid)
        if s is None:
            raise HTTPException(404, f"No sheet {sid}.")
        if ready and s["status"] != "ready":
            raise HTTPException(409, f"Sheet {s['name'] or sid} is {s['status']}, not ready.")
        return s

    def get_device(did: int) -> dict:
        d = store.device(did)
        if d is None:
            raise HTTPException(404, f"No device {did}.")
        return d

    def check_type(slug: str) -> None:
        if slug not in TYPES:
            raise HTTPException(400, f"Unknown device type {slug!r}. Known types: {', '.join(TYPES)}.")

    def check_point(s: dict, x: float, y: float) -> None:
        if not (0 <= x <= s["width_pt"] and 0 <= y <= s["height_pt"]):
            raise HTTPException(400, f"Point ({x:.0f}, {y:.0f}) is outside the page "
                                     f"(0..{s['width_pt']:.0f} x 0..{s['height_pt']:.0f} pt).")

    def check_room(room: str | None) -> None:
        if room is not None and not (0 < len(room.strip()) <= 80):
            raise HTTPException(400, "Room must be 1 to 80 characters.")

    @app.get("/api/config")
    def config():
        return {"method": jobs.method_name(), "methods": methods.ORDER,
                "types": [{"slug": t, "label": label(t), "family": vocabulary.lookup(t).family} for t in TYPES]}

    @app.post("/api/projects", status_code=201)
    def upload(file: Annotated[UploadFile, File()]):
        data = file.file.read()
        try:
            with _PDF:
                pid, _ = jobs.start(store, data_dir, file.filename or "upload.pdf", data)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return project_json(store, store.project(pid))

    @app.get("/api/projects")
    def list_projects():
        return [project_json(store, p) for p in store.projects()]

    @app.get("/api/projects/{pid}")
    def one_project(pid: int):
        return project_json(store, get_project(pid))

    @app.get("/api/projects/{pid}/counts")
    def counts(pid: int):
        get_project(pid)
        return counts_json(store, pid)

    @app.get("/api/projects/{pid}/bom")
    def bill_of_materials(pid: int, sheet: int | None = None):
        get_project(pid)
        live = [d for d in store.project_devices(pid)
                if d["status"] in COUNTED and (sheet is None or d["sheet_id"] == sheet)]
        return bom.build(live)

    @app.get("/api/projects/{pid}/events")
    def events(pid: int):
        get_project(pid)
        names = {s["id"]: s["name"] for s in store.sheets(pid)}
        return [{**e, "sheet": names.get(e["sheet_id"])} for e in store.events(pid)]

    @app.get("/api/projects/{pid}/export.csv")
    def export_csv(pid: int):
        p = get_project(pid)
        return Response(counts_csv(store, pid), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{Path(p["filename"]).stem}-counts.csv"'})

    @app.get("/api/projects/{pid}/export.json")
    def export_json(pid: int):
        p = get_project(pid)
        body = {
            "project": p, "counts": counts_json(store, pid),
            "sheets": [sheet_json(store, s) for s in store.sheets(pid)],
            "devices": [{**device_json(d), "sheet": d["sheet"]} for d in store.project_devices(pid)],
            "events": store.events(pid),
        }
        return JSONResponse(body, headers={
            "Content-Disposition": f'attachment; filename="{Path(p["filename"]).stem}-takeoff.json"'})

    @app.get("/api/projects/{pid}/export.pdf")
    def export_pdf(pid: int):
        p = get_project(pid)
        return Response(marked_pdf(store, pid), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{Path(p["filename"]).stem}-marked.pdf"'})

    @app.get("/api/sheets/{sid}")
    def one_sheet(sid: int):
        s = get_sheet(sid)
        devices = [device_json(d) for d in store.devices(sid)]
        names = list(s["rooms"] or [])
        names += sorted({d["room"] for d in devices if d["room"] and d["room"] not in names})
        return {**sheet_json(store, s), "rooms": names, "devices": devices}

    @app.get("/api/sheets/{sid}/plan.png")
    def plan_png(sid: int):
        s = get_sheet(sid)
        if not s["plan_frame"]:
            raise HTTPException(409, "This sheet has no plan frame yet.")
        return FileResponse(_png(s, "plan_frame", f"plan@{PLAN_PX_PER_PT}x.png", PLAN_PX_PER_PT), media_type="image/png")

    @app.get("/api/sheets/{sid}/legend.png")
    def legend_png(sid: int):
        s = get_sheet(sid)
        if not s["legend_frame"]:
            raise HTTPException(409, "This sheet has no legend frame yet.")
        return FileResponse(_png(s, "legend_frame", "legend@2x.png", 2), media_type="image/png")

    @app.get("/api/sheets/{sid}/page.png")
    def page_png(sid: int):
        s = get_sheet(sid)
        if not s["sheet_dir"]:
            raise HTTPException(409, "This sheet has not been read yet.")
        return FileResponse(_png(s, None, "page@small.png", 0.3), media_type="image/png")

    @app.post("/api/sheets/{sid}/confirm")
    def confirm_stage(sid: int, body: ConfirmIn):
        get_sheet(sid, ready=True)
        if body.stage not in STAGES:
            raise HTTPException(400, f"Unknown stage {body.stage!r}. Known: {', '.join(STAGES)}.")
        store.confirm_stage(sid, body.stage)
        return sheet_json(store, store.sheet(sid))

    @app.post("/api/sheets/{sid}/retry")
    def retry(sid: int):
        s = get_sheet(sid)
        if s["status"] in ("queued", "reading"):
            raise HTTPException(409, "This sheet is being read already.")
        store.reset_sheet(sid)
        jobs.submit(store, sid)
        return sheet_json(store, store.sheet(sid))

    @app.post("/api/sheets/{sid}/devices", status_code=201)
    def create_device(sid: int, body: DeviceIn):
        s = get_sheet(sid, ready=True)
        check_type(body.type)
        check_point(s, body.x, body.y)
        check_room(body.room)
        room = body.room or room_at(store, s, body.x, body.y)
        d = store.create_device(sid, {"type": body.type, "family": vocabulary.lookup(body.type).family,
                                      "x": body.x, "y": body.y, "room": room})
        return device_json(d)

    @app.post("/api/sheets/{sid}/accept-agreed")
    def accept_agreed(sid: int, body: AcceptIn | None = None):
        get_sheet(sid, ready=True)
        type_ = body.type if body else None
        if type_ is not None:
            check_type(type_)
        return {"verified": store.accept_agreed(sid, type_)}

    @app.patch("/api/devices/{did}")
    def patch_device(did: int, body: DevicePatch):
        d = get_device(did)
        s = get_sheet(d["sheet_id"])
        changes = body.model_dump(exclude_unset=True)
        if "type" in changes:
            check_type(changes["type"])
            changes["family"] = vocabulary.lookup(changes["type"]).family
        if "x" in changes or "y" in changes:
            check_point(s, changes.get("x", d["x"]), changes.get("y", d["y"]))
        if "room" in changes:
            check_room(changes["room"])
        if "status" in changes:
            if changes["status"] not in STATUSES:
                raise HTTPException(400, f"Unknown status {changes['status']!r}. Known: {', '.join(STATUSES)}.")
            return device_json(store.update_device(did, changes))
        return device_json(store.edit_device(did, changes))

    @app.delete("/api/devices/{did}")
    def delete_device(did: int):
        get_device(did)
        return device_json(store.remove_device(did))

    @app.post("/api/devices/{did}/verify")
    def verify_device(did: int):
        get_device(did)
        return device_json(store.verify_device(did))

    @app.post("/api/devices/{did}/restore")
    def restore_device(did: int):
        get_device(did)
        return device_json(store.restore_device(did))

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def main(argv: list[str] | None = None) -> int:
    """takeoff serve [--port 8000] [--host 127.0.0.1] [--data data]: the web app and its API."""
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="takeoff serve", description=main.__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data", default="data", help="data folder holding takeoff.db and projects/ (default: data)")
    args = ap.parse_args(argv)
    print(f"Takeoff on http://{args.host}:{args.port}  (method {jobs.method_name()})")
    uvicorn.run(create_app(args.data), host=args.host, port=args.port)
    return 0
