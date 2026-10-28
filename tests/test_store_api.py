"""Phase 6 (Store + API): upload -> sheets read in the background -> review CRUD -> counts and exports.

One upload per module: page 1 of the Escondido set (read by rules, detected with the free geometry method)
followed by a page of random noise, which must end as a sheet with status "error" and a message while the
first sheet is read normally. The model fallback of ingest (takeoff.locate) is replaced by its refusal, and
pages are read in a thread instead of worker processes so that replacement applies.
"""
import concurrent.futures
import csv
import io
import time

import numpy as np
import pymupdf
import pytest
from fastapi.testclient import TestClient

from takeoff import api, jobs, locate

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"


def _two_pages() -> bytes:
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open(PDF), from_page=0, to_page=0)
    noise = np.random.default_rng(0).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    page = doc.new_page(width=400, height=300)
    page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, 400, 300, noise.tobytes(), False))
    return doc.tobytes()


def _refuse(page, out_png):
    raise ValueError("page 1 is not an electrical plan with a legend: random noise image")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAKEOFF_METHOD", "geometry")
        mp.setattr(locate, "locate", _refuse)
        mp.setattr(jobs, "_POOL", concurrent.futures.ThreadPoolExecutor(max_workers=1))
        client = TestClient(api.create_app(tmp_path_factory.mktemp("data"), resume=False))
        r = client.post("/api/projects", files={"file": ("Escondido_two.pdf", _two_pages(), "application/pdf")})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        deadline = time.time() + 180
        while time.time() < deadline:
            project = client.get(f"/api/projects/{pid}").json()
            if project["progress"]["done"] == project["progress"]["total"]:
                break
            time.sleep(0.5)
        yield client, project


@pytest.fixture(scope="module")
def sheet(app):
    client, project = app
    return client.get(f"/api/sheets/{project['sheets'][0]['id']}").json()


def _total(client, pid) -> int:
    return client.get(f"/api/projects/{pid}/counts").json()["total"]


def test_good_page_is_read_and_bad_page_errors_loudly(app, sheet):
    _, project = app
    good, bad = project["sheets"]
    assert good["status"] == "ready" and good["name"] == "A1-111" and good["frames_source"] == "rules"
    assert bad["status"] == "error"
    assert bad["error"].startswith("Page 2 is not an electrical plan") and "random noise image" in bad["error"]
    assert len(sheet["devices"]) >= 40
    assert {d["status"] for d in sheet["devices"]} <= {"review", "agreed", "suggested"}
    assert "Exterior" in sheet["rooms"] and "BATH" in sheet["rooms"]
    assert project["errors"] == 1 and project["live"] == sheet["live"]


def test_counts_never_wait_for_review(app, sheet):
    client, project = app
    counts = client.get(f"/api/projects/{project['id']}/counts").json()
    assert counts["total"] == sheet["live"] > 0
    low = [d for d in sheet["devices"] if d["confidence"] < jobs.LOW_CONFIDENCE and d["status"] != "suggested"]
    assert all(d["status"] == "review" and d["flag"] for d in low)


def test_plan_and_legend_images(app, sheet):
    client, _ = app
    r = client.get(f"/api/sheets/{sheet['id']}/plan.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    pix = pymupdf.Pixmap(r.content)
    f = sheet["plan_frame"]
    assert abs(pix.width - (f["x1"] - f["x0"]) * api.PLAN_PX_PER_PT) <= 2
    assert client.get(f"/api/sheets/{sheet['id']}/legend.png").status_code == 200
    assert client.get(f"/api/sheets/{sheet['id']}/page.png").status_code == 200


def test_create_update_delete_restore_change_counts_and_are_audited(app, sheet):
    client, project = app
    pid, sid = project["id"], sheet["id"]
    before = _total(client, pid)
    f = sheet["plan_frame"]
    x, y = (f["x0"] + f["x1"]) / 2, (f["y0"] + f["y1"]) / 2
    r = client.post(f"/api/sheets/{sid}/devices", json={"type": "light_wall", "x": x, "y": y})
    assert r.status_code == 201, r.text
    dev = r.json()
    assert dev["status"] == "added" and dev["source"] == "you" and dev["room"]
    assert _total(client, pid) == before + 1

    r = client.patch(f"/api/devices/{dev['id']}", json={"type": "light_pendant", "room": "LIVING"})
    assert r.json()["type"] == "light_pendant" and r.json()["room"] == "LIVING" and r.json()["status"] == "added"
    rows = client.get(f"/api/projects/{pid}/counts").json()["rows"]
    assert any(r["room"] == "LIVING" and r["type"] == "light_pendant" and r["n"] >= 1 for r in rows)

    agreed = next(d for d in sheet["devices"] if d["status"] == "agreed")
    r = client.patch(f"/api/devices/{agreed['id']}", json={"room": "BATH" if agreed["room"] != "BATH" else "LIVING"})
    assert r.json()["status"] == "edited"

    assert client.delete(f"/api/devices/{dev['id']}").json()["status"] == "removed"
    assert _total(client, pid) == before
    assert client.post(f"/api/devices/{dev['id']}/restore").json()["status"] == "added"
    assert _total(client, pid) == before + 1

    events = [e for e in client.get(f"/api/projects/{pid}/events").json() if e["device_id"] == dev["id"]]
    assert [e["action"] for e in events] == ["create", "update", "delete", "restore"]
    assert events[1]["before"]["type"] == "light_wall" and events[1]["after"]["type"] == "light_pendant"
    assert all(e["at"] and e["actor"] == "you" for e in events)


def test_verify_suggestion_and_accept_agreed(app, sheet):
    client, project = app
    sid = sheet["id"]
    suggested = [d for d in sheet["devices"] if d["status"] == "suggested"]
    if suggested:
        before = _total(client, project["id"])
        assert client.post(f"/api/devices/{suggested[0]['id']}/verify").json()["status"] == "added"
        assert _total(client, project["id"]) == before + 1
    n = client.post(f"/api/sheets/{sid}/accept-agreed", json={"type": "light_recessed"}).json()["verified"]
    assert n >= 1
    rest = client.post(f"/api/sheets/{sid}/accept-agreed").json()["verified"]
    devices = client.get(f"/api/sheets/{sid}").json()["devices"]
    assert not any(d["status"] == "agreed" for d in devices) and rest >= 1
    assert "legend" in client.post(f"/api/sheets/{sid}/confirm", json={"stage": "legend"}).json()["confirmed"]
    assert client.post(f"/api/sheets/{sid}/confirm", json={"stage": "nope"}).status_code == 400
    counts = client.get(f"/api/projects/{project['id']}/counts").json()
    assert 0 < counts["confirmed"] <= counts["total"]


def test_bad_input_gets_clear_4xx(app, sheet):
    client, _ = app
    sid, did = sheet["id"], sheet["devices"][0]["id"]
    r = client.post(f"/api/sheets/{sid}/devices", json={"type": "toaster", "x": 900, "y": 300})
    assert r.status_code == 400 and "Unknown device type 'toaster'" in r.json()["detail"]
    r = client.post(f"/api/sheets/{sid}/devices", json={"type": "switch", "x": 99999, "y": 300})
    assert r.status_code == 400 and "outside the page" in r.json()["detail"]
    r = client.post(f"/api/sheets/{sid}/devices", json={"type": "switch", "x": "left"})
    assert r.status_code == 422 and "x:" in r.json()["detail"]
    assert client.patch(f"/api/devices/{did}", json={"status": "maybe"}).status_code == 400
    assert client.patch("/api/devices/999999", json={"room": "BATH"}).status_code == 404
    r = client.post("/api/projects", files={"file": ("notes.txt", b"hello, not a plan", "text/plain")})
    assert r.status_code == 400 and "not a PDF" in r.json()["detail"]
    r = client.post("/api/projects", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400 and "empty" in r.json()["detail"]


def test_exports(app, sheet):
    client, project = app
    pid = project["id"]
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/projects/{pid}/export.csv").text)))
    assert rows and rows[0].keys() >= {"sheet", "room", "type", "count", "confirmed_by_person"}
    assert sum(int(r["count"]) for r in rows) == _total(client, pid)
    body = client.get(f"/api/projects/{pid}/export.json").json()
    assert {d["status"] for d in body["devices"]} >= {"verified"}
    assert any(e["action"] == "create" for e in body["events"])
    r = client.get(f"/api/projects/{pid}/export.pdf")
    assert r.headers["content-type"] == "application/pdf"
    doc = pymupdf.open(stream=r.content, filetype="pdf")
    assert doc.page_count == 1 and "Takeoff A1-111" in doc[0].get_text()
