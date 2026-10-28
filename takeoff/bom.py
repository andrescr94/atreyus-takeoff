"""Bill of materials derived from the live detections: boxes by gang size, cable runs, a priced summary.

Like the counts, nothing here is stored: every call recomputes from the devices, so a correction (move a switch,
delete a light) changes boxes, cable and price at once.

Rules and assumptions, all stated in the output:
- Boxes. Switches whose centres are within GANG_PT of each other share one box (2 switches = one 2-gang box and
  one 2-gang plate). Every receptacle has its own 1-gang box and plate (weatherproof cover outside). Each light,
  exhaust fan and detector has a round ceiling box; each ceiling fan has a fan-rated box.
- Cable, switch legs (14/2 NM). Each light or fan goes to the nearest switch box in the same room. This is an
  approximation of the dashed switch legs on the plan, which are not traced yet. Length = straight distance at the
  sheet scale (1/4" = 1'-0", 18 pt per foot) + up from the switch to the ceiling and back down (ROUTING_FT) +
  SLACK_FT at each end.
- Cable, 3-way travellers (14/3 NM). Each 3-way switch box is paired with the nearest other 3-way box in the same
  room or, failing that, on the same sheet; same routing assumption.
- Receptacle and appliance circuits, home runs to the panel and labour are out of scope.
"""
from __future__ import annotations

import math

PT_PER_FT = 18.0  # 1/4" = 1'-0" -> 0.25 in = 18 pt per foot
GANG_PT = 14.0  # switch centres closer than this share a box (a switch symbol is about 10 pt wide)
ROUTING_FT = 2 * 4.0  # switch at 4 ft, ceiling at 8 ft: up into the ceiling at one end, down at the other
SLACK_FT = 1.0  # per end

# Source: Home Depot public prices, store 33040, 2026-10-24, pre-tax, checked in a browser. Items marked
# "estimate" were not checked and are placeholders.
PRICES: dict[str, tuple[str, float, str]] = {
    "switch": ("Single-pole switch, Leviton 1451-2W", 1.10, "Home Depot 2026-10-24"),
    "switch_3way": ("3-way switch, Leviton 1453-2W", 2.71, "Home Depot 2026-10-24"),
    "switch_dimmer": ("LED dimmer, Leviton DSL01", 25.00, "Home Depot 2026-10-24"),
    "switch_fan": ("Fan speed control", 25.00, "estimate (priced as a dimmer)"),
    "switch_vacancy": ("Vacancy sensor switch", 25.00, "estimate (priced as a dimmer)"),
    "receptacle": ("15A duplex receptacle, Leviton 5320-W", 0.98, "Home Depot 2026-10-24"),
    "receptacle_gfi": ("20A GFCI receptacle, Leviton GFNT2", 23.48, "Home Depot 2026-10-24"),
    "receptacle_gfi_wp": ("20A GFCI weather-resistant, Leviton GFWT2", 27.98, "Home Depot 2026-10-24"),
    "receptacle_240v": ("240V receptacle", 15.00, "estimate"),
    "light_recessed": ("6 in LED canless recessed", 8.00, "Home Depot 2026-10-24"),
    "light_pendant": ("Pendant light", 34.97, "estimate (flush-mount price, Home Depot)"),
    "light_wall": ("Wall sconce, indoor", 24.97, "Home Depot 2026-10-24"),
    "ceiling_fan": ("52 in ceiling fan with light", 54.98, "Home Depot 2026-10-24"),
    "exhaust_fan": ("Bath exhaust fan", 30.00, "estimate"),
    "smoke": ("Hardwired smoke / CO alarm", 35.00, "estimate"),
    "box_1": ("1-gang nail-on box, Carlon B118A", 0.82, "Home Depot 2026-10-24"),
    "box_2": ("2-gang box", 2.00, "estimate"),
    "box_3": ("3-gang box", 3.50, "estimate"),
    "box_4": ("4-gang box", 5.00, "estimate"),
    "box_ceiling": ("Round ceiling box", 2.50, "estimate"),
    "box_fan": ("Fan-rated ceiling box, Carlon B520A-CFB", 7.98, "Home Depot 2026-10-24"),
    "plate_1": ("1-gang plate", 0.60, "estimate"),
    "plate_2": ("2-gang plate", 1.20, "estimate"),
    "plate_3": ("3-gang plate", 2.00, "estimate"),
    "plate_4": ("4-gang plate", 3.00, "estimate"),
    "plate_wp": ("Weatherproof in-use cover", 12.00, "estimate"),
    "cable_14_2": ("14/2 NM-B cable, per ft", 0.45, "estimate"),
    "cable_14_3": ("14/3 NM-B cable, per ft", 0.70, "estimate"),
}

LOAD_FAMILIES = {"light", "fan"}
CEILING_BOX = {"light", "fan", "detector"}


def _price_key(t: str, family: str) -> str | None:
    """Price list key for a device type; None for things we do not price (junction boxes, other)."""
    if family == "switch":
        if t.startswith("switch_3way"):
            return "switch_3way"
        return t if t in PRICES else "switch"
    if family == "receptacle":
        if t == "receptacle_gfi_wp":
            return t
        if t == "receptacle_240v":
            return t
        return "receptacle_gfi" if t.startswith("receptacle_gfi") else "receptacle"
    if family == "light":
        if t.startswith("light_recessed"):
            return "light_recessed"
        return "light_pendant" if t == "light_pendant" else "light_wall"
    if family == "fan":
        return "ceiling_fan" if t == "ceiling_fan" else "exhaust_fan"
    if family == "detector":
        return "smoke"
    return None


def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _gang_groups(switches: list[dict]) -> list[list[dict]]:
    """Union-find on switch centres closer than GANG_PT (same sheet only)."""
    parent = list(range(len(switches)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(switches)):
        for j in range(i + 1, len(switches)):
            if switches[i]["sheet_id"] == switches[j]["sheet_id"] and _dist(switches[i], switches[j]) <= GANG_PT:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, s in enumerate(switches):
        groups.setdefault(find(i), []).append(s)
    return list(groups.values())


def _run_ft(a: dict, b: dict) -> float:
    return round(_dist(a, b) / PT_PER_FT + ROUTING_FT + 2 * SLACK_FT, 1)


def build(devices: list[dict]) -> dict:
    """devices: live devices with id, sheet_id, type, family, x, y (PDF points), room."""
    boxes: list[dict] = []
    runs: list[dict] = []
    qty: dict[str, float] = {}

    def add(key: str, n: float = 1) -> None:
        qty[key] = qty.get(key, 0) + n

    for d in devices:
        k = _price_key(d["type"], d["family"])
        if k:
            add(k)

    switches = [d for d in devices if d["family"] == "switch"]
    groups = _gang_groups(switches)
    for g in groups:
        gang = min(len(g), 4)
        cx, cy = sum(s["x"] for s in g) / len(g), sum(s["y"] for s in g) / len(g)
        boxes.append({"kind": f"{len(g)}-gang switch box", "gang": len(g), "sheet_id": g[0]["sheet_id"],
                      "room": g[0]["room"], "x": cx, "y": cy, "devices": [s["id"] for s in g]})
        add(f"box_{gang}")
        add(f"plate_{gang}")
    for d in devices:
        if d["family"] == "receptacle":
            wp = d["type"] == "receptacle_gfi_wp" or d["room"] == "Exterior"
            boxes.append({"kind": "1-gang receptacle box", "gang": 1, "sheet_id": d["sheet_id"], "room": d["room"],
                          "x": d["x"], "y": d["y"], "devices": [d["id"]]})
            add("box_1")
            add("plate_wp" if wp else "plate_1")
        elif d["family"] in CEILING_BOX:
            fan = d["type"] == "ceiling_fan"
            boxes.append({"kind": "fan-rated ceiling box" if fan else "ceiling box", "gang": 1,
                          "sheet_id": d["sheet_id"], "room": d["room"], "x": d["x"], "y": d["y"], "devices": [d["id"]]})
            add("box_fan" if fan else "box_ceiling")

    switch_boxes = [b for b in boxes if b["kind"].endswith("switch box")]
    for d in devices:
        if d["family"] not in LOAD_FAMILIES:
            continue
        same_sheet = [b for b in switch_boxes if b["sheet_id"] == d["sheet_id"]]
        pool = [b for b in same_sheet if b["room"] == d["room"]] or same_sheet
        if not pool:
            continue
        b = min(pool, key=lambda b: _dist(b, d))
        runs.append({"cable": "14/2", "sheet_id": d["sheet_id"], "room": d["room"], "from": [b["x"], b["y"]],
                     "to": [d["x"], d["y"]], "feet": _run_ft(b, d), "load": d["id"]})

    by_id = {s["id"]: s for s in switches}
    three_way = [b for b in switch_boxes if any(by_id[i]["type"].startswith("switch_3way") for i in b["devices"])]
    paired: set[int] = set()
    for i, a in enumerate(three_way):
        if i in paired:
            continue
        others = [(j, b) for j, b in enumerate(three_way) if j != i and j not in paired and b["sheet_id"] == a["sheet_id"]]
        if not others:
            continue
        same_room = [(j, b) for j, b in others if b["room"] == a["room"]] or others
        j, b = min(same_room, key=lambda jb: _dist(a, jb[1]))
        paired |= {i, j}
        runs.append({"cable": "14/3", "sheet_id": a["sheet_id"], "room": a["room"], "from": [a["x"], a["y"]],
                     "to": [b["x"], b["y"]], "feet": _run_ft(a, b), "load": None})

    for cable, key in (("14/2", "cable_14_2"), ("14/3", "cable_14_3")):
        feet = round(sum(r["feet"] for r in runs if r["cable"] == cable), 1)
        if feet:
            add(key, feet)

    lines = []
    for key, n in qty.items():
        name, unit, source = PRICES[key]
        lines.append({"item": key, "name": name, "qty": round(n, 1), "unit_price": unit,
                      "total": round(n * unit, 2), "source": source})
    lines.sort(key=lambda r: list(PRICES).index(r["item"]))
    return {
        "boxes": boxes,
        "runs": runs,
        "lines": lines,
        "total": round(sum(r["total"] for r in lines), 2),
        "assumptions": [
            f"Scale 1/4\" = 1'-0\" ({PT_PER_FT:g} pt per foot).",
            f"Switches within {GANG_PT:g} pt share one box; the box gang size is the number of switches.",
            ("Each light or fan is fed from the nearest switch box in its room (approximates the dashed switch "
             "legs, which are not traced yet)."),
            f"Every run adds {ROUTING_FT:g} ft up into the ceiling and back down and {SLACK_FT:g} ft slack per end.",
            "3-way switch boxes are paired with the nearest other 3-way box and joined with 14/3.",
            ("Prices: Home Depot public prices, store 33040, 2026-10-24, pre-tax; lines marked estimate are "
             "placeholders. Receptacle circuits, home runs and labour are not included."),
        ],
    }
