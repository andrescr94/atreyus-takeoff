from takeoff import bom


def dev(i, type_, family, x, y, room="Bath", sheet=1):
    return {"id": i, "sheet_id": sheet, "type": type_, "family": family, "x": x, "y": y, "room": room}


def test_side_by_side_switches_share_one_two_gang_box():
    out = bom.build([dev(1, "switch", "switch", 100, 100), dev(2, "switch_dimmer", "switch", 110, 100)])
    switch_boxes = [b for b in out["boxes"] if b["kind"].endswith("switch box")]
    assert [b["gang"] for b in switch_boxes] == [2]
    items = {l["item"]: l["qty"] for l in out["lines"]}
    assert items["box_2"] == 1 and items["plate_2"] == 1 and "box_1" not in items


def test_ceiling_fan_gets_a_fan_rated_box_and_a_switch_leg_measured_at_scale():
    # fan 10 ft (180 pt) from the switch: 10 ft + 8 ft up and down + 2 ft slack = 20 ft of 14/2
    out = bom.build([dev(1, "switch_fan", "switch", 0, 0), dev(2, "ceiling_fan", "fan", 180, 0)])
    assert any(b["kind"] == "fan-rated ceiling box" for b in out["boxes"])
    assert [(r["cable"], r["feet"]) for r in out["runs"]] == [("14/2", 20.0)]


def test_three_way_pair_is_joined_with_14_3():
    out = bom.build([dev(1, "switch_3way", "switch", 0, 0, "Hall"), dev(2, "switch_3way", "switch", 360, 0, "Hall")])
    assert [(r["cable"], r["feet"]) for r in out["runs"]] == [("14/3", 30.0)]


def test_moving_a_switch_changes_cable_and_price():
    fan = dev(2, "light_recessed", "light", 180, 0)
    near = bom.build([dev(1, "switch", "switch", 0, 0), fan])
    far = bom.build([dev(1, "switch", "switch", -180, 0), fan])
    assert far["runs"][0]["feet"] == near["runs"][0]["feet"] + 10
    assert far["total"] > near["total"]


def test_every_line_names_its_price_source():
    out = bom.build([dev(1, "receptacle_gfi_wp", "receptacle", 0, 0, "Exterior")])
    assert all(l["source"] for l in out["lines"])
    assert {l["item"] for l in out["lines"]} == {"receptacle_gfi_wp", "box_1", "plate_wp"}
