"""Phase 6, Store: projects, sheets, devices and an append-only audit of every change, in SQLite.

    projects   one uploaded PDF and the detection method it was read with
    sheets     one page: status queued -> reading -> ready | error (with the message), frames, the
               model calls' tokens and seconds
    devices    the current state of every device: type, room, centre in PDF points, status
    events     every create / update / verify / delete / restore, with the device before and after

Device status: "review" (flagged by takeoff.review, or low confidence), "agreed" (the AI's answer, not flagged), "suggested" (a possible
miss, not counted until accepted, then "added"), "verified" (a person said it is right), "edited"
(a person changed its type, room or place), "added" (a person put it there), "removed" (soft delete,
restorable to the status it was created with).
Counts are a query over devices (COUNTED statuses), never stored, and never wait for a person: the AI's
devices count as soon as a sheet is read; CONFIRMED says how many of them a person has accepted or made.
Every call opens its own connection, so the API threads and the worker processes share one file
(WAL mode lets readers and one writer work at the same time).
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

COUNTED = ("review", "agreed", "verified", "edited", "added")
STATUSES = COUNTED + ("suggested", "removed")
CHECKED = ("verified", "edited", "added", "removed")  # a person has looked at it
CONFIRMED = ("verified", "edited", "added")  # counted, and a person stands behind it

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    method TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sheets (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    page_index INTEGER NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    error TEXT,
    method TEXT NOT NULL,
    sheet_dir TEXT,
    width_pt REAL,
    height_pt REAL,
    plan_frame TEXT,
    legend_frame TEXT,
    frames_source TEXT,
    rooms TEXT NOT NULL DEFAULT '[]',
    confirmed TEXT NOT NULL DEFAULT '{}',
    model_calls INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    model_seconds REAL,
    cost_usd REAL,
    elapsed_seconds REAL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    sheet_id INTEGER NOT NULL REFERENCES sheets(id),
    type TEXT NOT NULL,
    family TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    w REAL NOT NULL DEFAULT 12,
    h REAL NOT NULL DEFAULT 12,
    room TEXT,
    confidence REAL,
    status TEXT NOT NULL,
    initial_status TEXT NOT NULL,
    flag TEXT,
    source TEXT NOT NULL,
    alternatives TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    sheet_id INTEGER,
    device_id INTEGER,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    before TEXT,
    after TEXT
);
CREATE INDEX IF NOT EXISTS devices_sheet ON devices(sheet_id);
CREATE INDEX IF NOT EXISTS events_project ON events(project_id);
"""

JSON_FIELDS = {"plan_frame", "legend_frame", "rooms", "confirmed", "alternatives", "evidence", "before", "after"}
DEVICE_FIELDS = ("type", "family", "x", "y", "w", "h", "room", "confidence", "status", "flag")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def dumps(value) -> str:
    """JSON that also takes numpy scalars (detections carry them in their evidence)."""
    return json.dumps(value, default=lambda o: o.item() if hasattr(o, "item") else str(o))


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: (json.loads(row[k]) if k in JSON_FIELDS and row[k] is not None else row[k]) for k in row.keys()}  # noqa: SIM118


class Store:
    def __init__(self, path: str | Path = "data/takeoff.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)

    @contextlib.contextmanager
    def _tx(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _event(self, con, project_id: int, action: str, actor: str = "you", sheet_id: int | None = None,
               device_id: int | None = None, before: dict | None = None, after: dict | None = None) -> None:
        con.execute(
            "INSERT INTO events (at, project_id, sheet_id, device_id, action, actor, before, after) VALUES (?,?,?,?,?,?,?,?)",
            (now(), project_id, sheet_id, device_id, action, actor,
             None if before is None else dumps(before), None if after is None else dumps(after)),
        )

    # ---- projects and sheets ----

    def create_project(self, name: str, filename: str, pdf_path: str, method: str, page_count: int) -> int:
        with self._tx() as con:
            pid = con.execute(
                "INSERT INTO projects (name, filename, pdf_path, method, page_count, created_at) VALUES (?,?,?,?,?,?)",
                (name, filename, pdf_path, method, page_count, now()),
            ).lastrowid
            for i in range(page_count):
                con.execute("INSERT INTO sheets (project_id, page_index, method) VALUES (?,?,?)", (pid, i, method))
            self._event(con, pid, "upload", after={"filename": filename, "pages": page_count, "method": method})
        return pid

    def set_pdf_path(self, project_id: int, pdf_path: str) -> None:
        with self._tx() as con:
            con.execute("UPDATE projects SET pdf_path = ? WHERE id = ?", (pdf_path, project_id))

    def projects(self) -> list[dict]:
        with self._tx() as con:
            return [_row(r) for r in con.execute("SELECT * FROM projects ORDER BY id DESC")]

    def project(self, project_id: int) -> dict | None:
        with self._tx() as con:
            return _row(con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())

    def sheets(self, project_id: int) -> list[dict]:
        with self._tx() as con:
            rows = con.execute("SELECT * FROM sheets WHERE project_id = ? ORDER BY page_index", (project_id,))
            return [_row(r) for r in rows]

    def sheet(self, sheet_id: int) -> dict | None:
        with self._tx() as con:
            return _row(con.execute("SELECT * FROM sheets WHERE id = ?", (sheet_id,)).fetchone())

    def unfinished_sheets(self) -> list[dict]:
        with self._tx() as con:
            return [_row(r) for r in con.execute("SELECT * FROM sheets WHERE status IN ('queued', 'reading')")]

    def update_sheet(self, sheet_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = [dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
        with self._tx() as con:
            con.execute(f"UPDATE sheets SET {cols} WHERE id = ?", (*values, sheet_id))

    def reset_sheet(self, sheet_id: int) -> None:
        """Back to queued for another read: its devices go (the events keep them)."""
        sheet = self.sheet(sheet_id)
        with self._tx() as con:
            con.execute("DELETE FROM devices WHERE sheet_id = ?", (sheet_id,))
            con.execute(
                "UPDATE sheets SET status='queued', stage=NULL, error=NULL, confirmed='{}', started_at=NULL, "
                "finished_at=NULL WHERE id = ?", (sheet_id,))
            self._event(con, sheet["project_id"], "retry", sheet_id=sheet_id, before={"status": sheet["status"]})

    def confirm_stage(self, sheet_id: int, stage: str) -> None:
        """A person said what was read for one stage of the sheet (sheet, legend, rooms, devices) looks right.
        Optional: nothing waits on it; it is recorded so the export shows what a person looked at."""
        sheet = self.sheet(sheet_id)
        with self._tx() as con:
            con.execute("UPDATE sheets SET confirmed = json_set(confirmed, ?, ?) WHERE id = ?", (f"$.{stage}", now(), sheet_id))
            self._event(con, sheet["project_id"], "confirm-stage", sheet_id=sheet_id, after={"stage": stage})

    def sheet_name_taken(self, project_id: int, name: str, sheet_id: int) -> dict | None:
        with self._tx() as con:
            row = con.execute("SELECT * FROM sheets WHERE project_id = ? AND name = ? AND id != ?",
                              (project_id, name, sheet_id)).fetchone()
            return _row(row)

    # ---- devices ----

    def add_detections(self, sheet_id: int, devices: list[dict]) -> None:
        """The model's devices for a sheet, each with its initial status; one "detect" event each."""
        sheet = self.sheet(sheet_id)
        with self._tx() as con:
            for d in devices:
                did = self._insert(con, sheet_id, d)
                self._event(con, sheet["project_id"], "detect", "model", sheet_id, did, after=self._get(con, did))

    def _insert(self, con, sheet_id: int, d: dict) -> int:
        t = now()
        return con.execute(
            "INSERT INTO devices (sheet_id, type, family, x, y, w, h, room, confidence, status, initial_status, flag, "
            "source, alternatives, evidence, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sheet_id, d["type"], d["family"], d["x"], d["y"], d.get("w", 12.0), d.get("h", 12.0), d.get("room"),
             d.get("confidence"), d["status"], d["status"], d.get("flag"), d["source"],
             dumps(d.get("alternatives", [])), dumps(d.get("evidence", {})), t, t),
        ).lastrowid

    def _get(self, con, device_id: int) -> dict | None:
        return _row(con.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())

    def device(self, device_id: int) -> dict | None:
        with self._tx() as con:
            return self._get(con, device_id)

    def devices(self, sheet_id: int) -> list[dict]:
        with self._tx() as con:
            return [_row(r) for r in con.execute("SELECT * FROM devices WHERE sheet_id = ? ORDER BY id", (sheet_id,))]

    def project_devices(self, project_id: int) -> list[dict]:
        with self._tx() as con:
            rows = con.execute(
                "SELECT d.*, s.name AS sheet FROM devices d JOIN sheets s ON s.id = d.sheet_id "
                "WHERE s.project_id = ? ORDER BY s.page_index, d.id", (project_id,))
            return [_row(r) for r in rows]

    def create_device(self, sheet_id: int, device: dict) -> dict:
        """A device a person put on the plan: status "added", source "you"."""
        sheet = self.sheet(sheet_id)
        with self._tx() as con:
            did = self._insert(con, sheet_id, {**device, "status": "added", "source": "you", "confidence": 1.0})
            after = self._get(con, did)
            self._event(con, sheet["project_id"], "create", sheet_id=sheet_id, device_id=did, after=after)
        return after

    def update_device(self, device_id: int, changes: dict, action: str = "update") -> dict:
        """Apply `changes` (any of DEVICE_FIELDS) and record before/after. Unchanged values write nothing."""
        with self._tx() as con:
            before = self._get(con, device_id)
            changes = {k: v for k, v in changes.items() if k in DEVICE_FIELDS and before[k] != v}
            if not changes:
                return before
            cols = ", ".join(f"{k} = ?" for k in changes)
            con.execute(f"UPDATE devices SET {cols}, updated_at = ? WHERE id = ?", (*changes.values(), now(), device_id))
            after = self._get(con, device_id)
            project_id = con.execute("SELECT project_id FROM sheets WHERE id = ?", (before["sheet_id"],)).fetchone()[0]
            self._event(con, project_id, action, sheet_id=before["sheet_id"], device_id=device_id,
                        before={k: before[k] for k in changes}, after={k: after[k] for k in changes})
        return after

    def edit_device(self, device_id: int, changes: dict) -> dict:
        """A person changed type, room or place: the device becomes "edited" (a suggestion becomes "added")."""
        d = self.device(device_id)
        real = {k: v for k, v in changes.items() if d[k] != v}
        if real and "status" not in real:
            real["status"] = {"suggested": "added", "added": "added", "removed": "removed"}.get(d["status"], "edited")
        return self.update_device(device_id, real)

    def verify_device(self, device_id: int) -> dict:
        """A person said it is right: "review"/"agreed" -> "verified", "suggested" -> "added"; others stay."""
        d = self.device(device_id)
        status = {"review": "verified", "agreed": "verified", "suggested": "added"}.get(d["status"])
        return self.update_device(device_id, {"status": status}, "verify") if status else d

    def remove_device(self, device_id: int) -> dict:
        return self.update_device(device_id, {"status": "removed"}, "delete")

    def restore_device(self, device_id: int) -> dict:
        d = self.device(device_id)
        if d["status"] != "removed":
            return d
        return self.update_device(device_id, {"status": d["initial_status"]}, "restore")

    def accept_agreed(self, sheet_id: int, type_: str | None = None) -> int:
        """Verify every "agreed" device of the sheet (of one type when given); returns how many."""
        ids = [d["id"] for d in self.devices(sheet_id) if d["status"] == "agreed" and type_ in (None, d["type"])]
        for did in ids:
            self.update_device(did, {"status": "verified"}, "verify")
        return len(ids)

    # ---- derived ----

    def counts(self, project_id: int) -> list[dict]:
        """Live devices per sheet x room x type (statuses in COUNTED)."""
        marks = ",".join("?" * len(COUNTED))
        with self._tx() as con:
            rows = con.execute(
                f"SELECT d.sheet_id, s.name AS sheet, COALESCE(d.room, '?') AS room, d.type, d.family, COUNT(*) AS n, "
                f"SUM(d.status IN ('verified', 'edited', 'added')) AS confirmed "
                f"FROM devices d JOIN sheets s ON s.id = d.sheet_id WHERE s.project_id = ? AND d.status IN ({marks}) "
                f"GROUP BY d.sheet_id, room, d.type ORDER BY s.page_index, room, d.type", (project_id, *COUNTED))
            return [dict(r) for r in rows]

    def status_counts(self, sheet_id: int) -> dict[str, int]:
        with self._tx() as con:
            rows = con.execute("SELECT status, COUNT(*) FROM devices WHERE sheet_id = ? GROUP BY status", (sheet_id,))
            return {s: n for s, n in rows}

    def events(self, project_id: int) -> list[dict]:
        with self._tx() as con:
            return [_row(r) for r in con.execute("SELECT * FROM events WHERE project_id = ? ORDER BY id", (project_id,))]
