"""The scoreboard and the model door: scoring rules, scan copies, answer cache, JSON parsing."""
import json

import pymupdf
import pytest

from takeoff import evaluate, llm, methods
from takeoff.ingest import load_sheet
from takeoff.match import Detection


def _det(slug, x, y):
    return Detection(id="t", slug=slug, family="receptacle", bbox=(x - 4, y - 4, x + 4, y + 4), confidence=1.0)


KEY = [{"type": "receptacle_gfi", "x": 100, "y": 100}, {"type": "switch", "x": 200, "y": 100}]


def test_right_needs_place_and_type():
    s, _ = evaluate.score(KEY, [_det("receptacle_gfi", 105, 100), _det("switch_fan", 200, 104)])
    assert (s.key, s.found, s.placed, s.right) == (2, 2, 2, 1)
    assert s.wrong_type == [("switch", "switch_fan", 200, 100)]
    assert s.recall == 0.5 and s.precision == 0.5


def test_far_detection_is_a_miss_and_an_extra():
    s, _ = evaluate.score(KEY, [_det("receptacle_gfi", 100 + evaluate.MATCH_PT + 1, 100)])
    assert s.right == 0 and len(s.missed) == 2 and len(s.extra) == 1


def test_one_device_cannot_be_counted_twice():
    s, _ = evaluate.score(KEY[:1], [_det("receptacle_gfi", 100, 100), _det("receptacle_gfi", 102, 100)])
    assert s.right == 1 and s.precision == 0.5


def test_rooms_and_count_table_are_scored():
    key = [{"type": "switch", "x": 0, "y": 0, "room": "KITCHEN", "room_also": "LIVING"},
           {"type": "switch", "x": 100, "y": 0, "room": "Bedroom 1"}]
    a, b = _det("switch", 0, 0), _det("switch", 100, 0)
    a.room, b.room = "Living", "BED 1"
    s, _ = evaluate.score(key, [a, b])
    assert s.rooms_right == 2 and s.cells_right == s.cells == 2  # open-plan alternative and spelling accepted
    b.room = "BATH"
    s, _ = evaluate.score(key, [a, b])
    assert s.rooms_right == 1 and (s.cells_right, s.cells) == (1, 3)


def test_answer_key_has_300_devices_on_four_sheets():
    assert [len(evaluate.load_key(s)) for s in evaluate.SHEETS] == [55, 66, 82, 97]
    slugs = {d["type"] for s in evaluate.SHEETS for d in evaluate.load_key(s)}
    assert slugs <= set(methods.ELECTRICAL_SLUGS)


def test_scan_copy_has_no_text_and_no_strokes():
    scan = evaluate.scan_copy(load_sheet("A1-111"))
    page = pymupdf.open(str(scan.pdf_path))[0]
    assert scan.pdf_path.name == "page_scan.pdf"
    assert page.get_text().strip() == "" and page.get_drawings() == []
    assert len(page.get_images()) == 1


def test_geometry_rung_scores_and_finds_nothing_on_a_scan():
    vec, _ = evaluate.evaluate("geometry", sheets=["A1-111"], draw=False)
    scn, _ = evaluate.evaluate("geometry", scan=True, sheets=["A1-111"], draw=False)
    assert vec.right == 51 and vec.precision == 1.0
    assert scn.found == 0


def test_replay_reads_the_saved_answer_and_a_normal_call_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ANSWERS_DIR", tmp_path)
    img = tmp_path / "a.png"
    img.write_bytes(b"png bytes")
    key = llm._key("count", [img], llm.model_name(), 0)
    (tmp_path / f"{key}.json").write_text(json.dumps(
        {"text": "[]", "model": "m", "backend": "cli", "seconds": 40.0, "cost_usd": 0.3}))
    monkeypatch.setattr(llm, "REPLAY", True)
    monkeypatch.setattr(llm, "_ask_cli", lambda *a: pytest.fail("must not call claude"))
    llm.LEDGER.clear()
    a = llm.ask("count", [img])
    assert a.replayed and a.seconds == 40.0 and llm.LEDGER == [a]
    monkeypatch.setattr(llm, "REPLAY", False)
    live = llm.Answer(text="[1]", model="m", backend="cli", seconds=0.0, cost_usd=None)
    monkeypatch.setattr(llm, "_ask_cli", lambda *a: live)
    assert llm.ask("count", [img]).text == "[1]"  # live by default, even with a saved answer on disk
    assert llm._key("count", [img], llm.model_name(), 1) != key  # run=1 is a fresh, independent answer


def test_json_array_survives_a_code_fence():
    assert llm.json_array('```json\n[{"type": "switch"}]\n```') == [{"type": "switch"}]
