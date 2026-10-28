"""Score detection methods against the answer key: the evidence behind every design decision.

    takeoff eval vision-crop            one method, all four sheets, vector PDF, model called live
    takeoff eval template --scan        same, on a scanned copy (image only, no text, no strokes)
    takeoff eval vision-crop --replay   re-score the saved model answers instead of calling the model
    takeoff compare [--replay]          every method plus the one-shot agent runs -> docs/results.md

A detection is right when its centre is within MATCH_PT of an answer-key device of the same type
(one-to-one, nearest first). Recall = right / devices in the key; precision = right / detections.
Room right = share of the devices found in the right place whose room matches the key (a device the key marks
with room_also, an open-plan or unlabelled spot, may be in either room; type_also likewise accepts a second type). Table right = share of the cells of the
per-room count table (room x type, the table the brief asks for) whose number is exactly the key's.
Overlays in data/eval/<method>/: green = right, orange = found but wrong type, red ring = missed,
purple = detection with no device there.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from takeoff import llm, methods, pipeline, vocabulary
from takeoff.ingest import Sheet, ingest_page, load_sheet
from takeoff.match import Detection

KEY_DIR = Path("eval/answer_key")
SHEETS = ["A1-111", "A2-111", "A3-111", "A4-111"]
MATCH_PT = 22.0  # ~2.5 symbol widths: vision methods report centres a few points off
SCAN_DPI = 150
FAMILIES = ["fan", "light", "switch", "receptacle", "detector"]
RADII = [8, 16, 22, 44]  # match radii shown in the sensitivity table
DETECTIONS: dict = {}  # (method, scan) -> {sheet: detections}, kept by evaluate() for the radius table
ONESHOT_DIR = Path("eval/oneshot/runs")
ROOM_ALIASES = {"LAUNDRY": "LNDRY", "CLOSET": "CL", "OUTSIDE": "EXTERIOR"}


def room_name(name: str | None) -> str:
    """Compare room names the way a person would: case, punctuation and common spellings ignored."""
    t = re.sub(r"[^A-Z0-9]+", " ", (name or "").upper()).strip()
    t = re.sub(r"^BEDROOM (\d)$", r"BED \1", t)
    return ROOM_ALIASES.get(t, t)


def family(slug: str) -> str:
    try:
        return vocabulary.lookup(slug).family
    except KeyError:
        return "other"


@dataclass
class Score:
    key: int = 0
    found: int = 0
    placed: int = 0  # right place, any type
    right: int = 0  # right place and type
    rooms_right: int = 0  # right place, right room
    cells: int = 0  # cells in the per-room count table (room x type, union of key and prediction)
    cells_right: int = 0  # cells whose count equals the key's
    seconds: float = 0.0
    cost_usd: float = 0.0
    tokens_in: int = 0  # every model call's input tokens (prompt, images, cached parts)
    tokens_out: int = 0
    by_family: dict = field(default_factory=lambda: collections.defaultdict(lambda: [0, 0]))  # family: [right, key]
    missed: list = field(default_factory=list)
    wrong_type: list = field(default_factory=list)
    extra: list = field(default_factory=list)

    def add(self, other: Score) -> None:
        for f in ("key", "found", "placed", "right", "rooms_right", "cells", "cells_right", "seconds", "cost_usd",
                  "tokens_in", "tokens_out"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        for fam, (r, k) in other.by_family.items():
            self.by_family[fam][0] += r
            self.by_family[fam][1] += k

    @property
    def recall(self) -> float:
        return self.right / self.key if self.key else 0.0

    @property
    def precision(self) -> float:
        return self.right / self.found if self.found else 0.0

    @property
    def room_rate(self) -> float:
        return self.rooms_right / self.placed if self.placed else 0.0

    @property
    def table_rate(self) -> float:
        return self.cells_right / self.cells if self.cells else 0.0


def load_key(sheet_id: str) -> list[dict]:
    return json.loads((KEY_DIR / f"{sheet_id}.json").read_text())["devices"]


def centre(d: Detection) -> tuple[float, float]:
    return (d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2


def score(key: list[dict], dets: list[Detection], radius: float | None = None) -> tuple[Score, list[tuple[int, int]]]:
    radius = MATCH_PT if radius is None else radius
    s = Score(key=len(key), found=len(dets))
    pts = [centre(d) for d in dets]
    pairs = sorted(
        (math.dist((k["x"], k["y"]), p), i, j) for i, k in enumerate(key) for j, p in enumerate(pts)
    )
    used_k, used_d, matched = set(), set(), []
    for dist, i, j in pairs:
        if dist <= radius and i not in used_k and j not in used_d:
            used_k.add(i)
            used_d.add(j)
            matched.append((i, j))
    s.placed = len(matched)
    for k in key:
        s.by_family[family(k["type"])][1] += 1
    for i, j in matched:
        if dets[j].slug in (key[i]["type"], key[i].get("type_also")):
            s.right += 1
            s.by_family[family(key[i]["type"])][0] += 1
        else:
            s.wrong_type.append((key[i]["type"], dets[j].slug, round(key[i]["x"]), round(key[i]["y"])))
    owner = {j: i for i, j in matched}
    key_rows, pred_rows = [], []
    for k in key:
        key_rows.append((room_name(k.get("room")), k["type"]))
    for j, d in enumerate(dets):
        room, slug = room_name(d.room), d.slug
        if j in owner:
            k = key[owner[j]]
            if room in (room_name(k.get("room")), room_name(k.get("room_also"))):
                s.rooms_right += 1
                room = room_name(k.get("room"))  # an accepted alternative counts in the key's room
            if slug == k.get("type_also"):
                slug = k["type"]  # same for an accepted alternative type
        pred_rows.append((room, slug))
    kc, pc = collections.Counter(key_rows), collections.Counter(pred_rows)
    s.cells = len(set(kc) | set(pc))
    s.cells_right = sum(kc[c] == pc[c] for c in set(kc) | set(pc))
    s.missed = [(k["type"], round(k["x"]), round(k["y"])) for i, k in enumerate(key) if i not in used_k]
    s.extra = [(d.slug, round(p[0]), round(p[1])) for j, (d, p) in enumerate(zip(dets, pts)) if j not in used_d]
    return s, matched


def scan_copy(sheet: Sheet) -> Sheet:
    """The same sheet as a scanner would deliver it: one greyscale image, no text layer, no strokes.

    The returned Sheet comes from ingesting the scan itself (OCR + image lines), nothing from the vector page.
    It is saved as sheet_scan.json because OCR takes ~15 s a page."""
    out = Path(sheet.pdf_path).with_name("page_scan.pdf")
    saved = out.with_name("sheet_scan.json")
    if not out.exists():
        src = pymupdf.open(str(sheet.pdf_path))[0]
        pix = src.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY)
        doc = pymupdf.open()
        page = doc.new_page(width=src.rect.width, height=src.rect.height)
        page.insert_image(page.rect, pixmap=pix)
        doc.save(str(out))
        saved.unlink(missing_ok=True)
    if not saved.exists():
        saved.write_text(ingest_page(out, dpi=sheet.dpi).to_json())
    return Sheet.from_json(saved.read_text())


def run_method(name: str, sheet: Sheet) -> tuple[list[Detection], float, float, int, int]:
    """Detections with rooms, plus seconds and API-equivalent cost. A replayed model answer reports the time
    and cost of the live call that produced it, so the table always shows what a live run costs."""
    llm.LEDGER.clear()
    t0 = time.time()
    dets = pipeline.attach_rooms(sheet, methods.get(name).detect(sheet))
    seconds = time.time() - t0 + sum(a.seconds for a in llm.LEDGER if a.replayed)
    cost = sum(a.cost_usd or 0.0 for a in llm.LEDGER)
    tin = sum(a.input_tokens or 0 for a in llm.LEDGER)
    tout = sum(a.output_tokens or 0 for a in llm.LEDGER)
    return dets, round(seconds, 1), round(cost, 3), tin, tout


def render(sheet: Sheet, key: list[dict], dets: list[Detection], matched: list[tuple[int, int]], out_png: Path) -> None:
    page = pymupdf.open(str(load_sheet(sheet.id).pdf_path))[0]  # always draw on the clean vector page
    hit_k = {i for i, _ in matched}
    hit_d = {j: i for i, j in matched}
    green, orange, red, purple = (0, 0.6, 0), (1, 0.5, 0), (0.9, 0, 0), (0.6, 0, 0.8)
    for j, d in enumerate(dets):
        x, y = centre(d)
        if j in hit_d:
            ok = d.slug in (key[hit_d[j]]["type"], key[hit_d[j]].get("type_also"))
            page.draw_rect(pymupdf.Rect(x - 6, y - 6, x + 6, y + 6), color=green if ok else orange, width=0.8)
            if not ok:
                page.insert_text((x - 6, y + 10), d.slug, fontsize=3.5, color=orange)
        else:
            page.draw_rect(pymupdf.Rect(x - 6, y - 6, x + 6, y + 6), color=purple, width=0.8)
            page.insert_text((x - 6, y + 10), d.slug, fontsize=3.5, color=purple)
    for i, k in enumerate(key):
        if i not in hit_k:
            page.draw_circle((k["x"], k["y"]), 9, color=red, width=1.2)
            page.insert_text((k["x"] - 9, k["y"] - 11), "MISSED " + k["type"], fontsize=3.5, color=red)
    f = sheet.plan_frame
    out_png.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(dpi=150, clip=pymupdf.Rect(f.x0, f.y0, f.x1, f.y1)).save(str(out_png))


def evaluate(name: str, scan: bool = False, sheets: list[str] = SHEETS, draw: bool = True) -> tuple[Score, dict]:
    """Score one method on every sheet. Sheets run in parallel (each makes its own model calls)."""

    def one(sid: str) -> tuple[Score, str | None]:
        sheet = load_sheet(sid)
        if scan:
            sheet = scan_copy(sheet)
        key = load_key(sid)
        try:
            dets, seconds, cost, tin, tout = run_method(name, sheet)
            err = None
        except Exception as e:  # noqa: BLE001 - a method that cannot run on this input scores zero, reason kept
            dets, seconds, cost, tin, tout, err = [], 0.0, 0.0, 0, 0, f"{type(e).__name__}: {e}"
        s, matched = score(key, dets)
        s.seconds, s.cost_usd, s.tokens_in, s.tokens_out = seconds, cost, tin, tout
        DETECTIONS.setdefault((name, scan), {})[sid] = dets
        if draw:
            render(sheet, key, dets, matched, Path("data/eval") / name / f"{sid}{'_scan' if scan else ''}.png")
        return s, err

    with ThreadPoolExecutor(len(sheets)) as pool:
        results = list(pool.map(one, sheets))
    total, per_sheet = Score(), {}
    for sid, (s, err) in zip(sheets, results):
        per_sheet[sid] = (s, err)
        total.add(s)
    return total, per_sheet


def _fmt(s: Score) -> str:
    return (f"{s.right}/{s.key} right, recall {s.recall:.1%}, precision {s.precision:.1%}, "
            f"room {s.room_rate:.1%}, table {s.cells_right}/{s.cells}")


def print_eval(name: str, scan: bool, total: Score, per_sheet: dict) -> None:
    print(f"{name}{' (scan)' if scan else ''}: {_fmt(total)}; {total.seconds:.0f} s, "
          f"{total.tokens_in:,} tokens in, {total.tokens_out:,} out for {len(per_sheet)} sheets")
    for sid, (s, err) in per_sheet.items():
        print(f"  {sid}: {_fmt(s)}  found {s.found}  {s.seconds:.0f} s" + (f"  ERROR {err}" if err else ""))
        for label, items in (("missed", s.missed), ("wrong type (key, got)", s.wrong_type), ("extra", s.extra)):
            if items:
                print(f"    {label}: {items[:12]}{' ...' if len(items) > 12 else ''}")


def oneshot_runs() -> list[Path]:
    return sorted(p for p in ONESHOT_DIR.glob("*") if (p / "meta.json").exists())


def score_oneshot(run: Path) -> tuple[Score, dict, dict]:
    """Score a one-shot agent run (eval/oneshot/run.py) from the result.json it wrote."""
    meta = json.loads((run / "meta.json").read_text())
    result = json.loads((run / "result.json").read_text()) if (run / "result.json").exists() else {"sheets": []}
    total, per_sheet = Score(), {}
    known = set(methods.ELECTRICAL_SLUGS)
    for n, sid in enumerate(SHEETS, start=1):
        sheet = next((x for x in result.get("sheets", []) if x.get("sheet") == sid or x.get("page") == n), {})
        dets = []
        for m, d in enumerate(sheet.get("devices", [])):
            try:
                x, y = float(d["x"]), float(d["y"])
            except (KeyError, TypeError, ValueError):
                continue
            slug = d.get("type") if d.get("type") in known else "other"
            dets.append(Detection(id=f"{run.name}-{sid}-{m}", slug=slug, family=family(slug),
                                  bbox=(x - 6, y - 6, x + 6, y + 6), confidence=1.0, room=d.get("room")))
        s, matched = score(load_key(sid), dets)
        per_sheet[sid] = (s, None if sheet else "no result for this sheet")
        total.add(s)
        render(load_sheet(sid), load_key(sid), dets, matched, Path("data/eval") / f"oneshot-{run.name}" / f"{sid}.png")
    total.seconds = meta.get("seconds") or 0.0
    total.cost_usd = meta.get("cost_usd") or 0.0
    total.tokens_in = meta.get("input_tokens") or 0
    total.tokens_out = meta.get("output_tokens") or 0
    return total, per_sheet, meta


def _row(rung, name: str, vec: Score, scan: str, per_sheet_s: str, cost: str) -> str:
    fams = " | ".join(f"{vec.by_family[f][0]}/{vec.by_family[f][1]}" for f in FAMILIES)
    return (f"| {rung} | {name} | {vec.recall:.1%} | {vec.precision:.1%} | {vec.room_rate:.1%} | "
            f"{vec.table_rate:.1%} | {scan} | {fams} | {per_sheet_s} | {cost} |")


def _tokens(s: Score) -> str:
    if not s.tokens_in:
        return "0" if not s.cost_usd else "not recorded"
    n = len(SHEETS)
    return f"{s.tokens_in / n / 1000:,.0f}k / {s.tokens_out / n / 1000:,.1f}k"


def compare(names: list[str] | None = None, out_md: Path = Path("docs/results.md")) -> str:
    names = names or methods.available()
    fam_head = " | ".join(f.capitalize() for f in FAMILIES)
    mode = "re-scored from saved model answers (--replay)" if llm.REPLAY else "model called live"
    lines = [
        "# Detection methods vs the answer key",
        "",
        (f"Generated by `uv run takeoff compare` on {time.strftime('%Y-%m-%d')} ({mode}). Answer key: "
         f"{sum(len(load_key(s)) for s in SHEETS)} devices on 4 sheets (eval/answer_key/README.md says who checked "
         f"it and how). Found = same type within {MATCH_PT:.0f} pt of the device. Room = the found device's room "
         "matches. Table = cells of the per-room count table (room x type) that are exactly right. Scan = the same "
         f"sheet as a {SCAN_DPI} DPI greyscale image, no text layer, no vector strokes."),
        "",
        (f"| Rung | Method | Found (recall) | Precision | Room | Table | Scan recall | {fam_head} | s / sheet | "
        "tokens / sheet (in / out) |"),
        "|---|---|---|---|---|---|---|" + "---|" * len(FAMILIES) + "---|---|",
    ]
    sheet_rows = []  # (method, {sheet: Score}) for the per-sheet table

    def per_sheet_row(label: str, per: dict) -> str:
        return f"| {label} | " + " | ".join(
            f"{per[sid][0].right}/{per[sid][0].key} · room {per[sid][0].room_rate:.0%} · "
            f"table {per[sid][0].cells_right}/{per[sid][0].cells}" for sid in SHEETS) + " |"

    for run in [r for r in oneshot_runs() if not r.name.endswith("-scan")]:
        vec, per, meta = score_oneshot(run)
        scan_run = run.with_name(run.name + "-scan")
        scan = f"{score_oneshot(scan_run)[0].recall:.1%}" if (scan_run / "meta.json").exists() else "not run"
        sheet_rows.append(per_sheet_row(f"one-shot {meta['agent']} {meta['model']}", per))
        cost = _tokens(vec)
        lines.append(_row(0, f"one-shot {meta['agent']} {meta['model']}", vec, scan,
                          f"{vec.seconds / len(SHEETS):.0f}", cost))
    rows = []
    for name in names:
        m = methods.get(name)
        vec, per = evaluate(name, scan=False)
        scn, _ = evaluate(name, scan=True)
        rows.append(m)
        sheet_rows.append(per_sheet_row(m.name, per))
        lines.append(_row(m.rung, m.name, vec, f"{scn.recall:.1%}", f"{vec.seconds / len(SHEETS):.0f}",
                          _tokens(vec)))
    lines += ["", "## Per sheet (vector PDF): devices right / in the key · room right · count-table cells right", "",
              "| Method | " + " | ".join(SHEETS) + " |", "|---|" + "---|" * len(SHEETS), *sheet_rows]
    lines += ["", "## Does the match radius change the result?", "",
              (f"Recall on the vector PDF when a detection must be within R points of the device (one-to-one, same type). "
              f"{MATCH_PT:.0f} pt is used above; 1 pt = 1/72 in on paper, at 1/4 in = 1 ft that is 0.67 in of building, "
              "so 22 pt is about 1.2 ft. A third of the devices have a neighbour closer than 22 pt, which is why "
              "matching is one-to-one and must agree on type."), "",
              "| Method | " + " | ".join(f"R = {r} pt" for r in RADII) + " |", "|---|" + "---|" * len(RADII)]
    for name in names:
        dets = DETECTIONS.get((name, False), {})
        if dets:
            cells = []
            for r in RADII:
                right = sum(score(load_key(sid), d, r)[0].right for sid, d in dets.items())
                cells.append(f"{right / sum(len(load_key(sid)) for sid in dets):.1%}")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", ("Rung 0 is a coding agent given only the PDF and the brief (eval/oneshot/prompt.md), one run each, "
              "timed end to end; the date of each run is in eval/oneshot/runs/<run>/meta.json."), "", "Ideas:", ""]
    lines += [f"- **{m.name}** ({m.needs}): {m.idea}." for m in rows]
    text = "\n".join(lines) + "\n"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(text)
    return text


def main_eval(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="takeoff eval", description="score one method against the answer key")
    ap.add_argument("method", choices=methods.ORDER)
    ap.add_argument("--scan", action="store_true", help="run on a scanned copy of each sheet")
    ap.add_argument("--sheet", action="append", help="limit to these sheets (default: all four)")
    ap.add_argument("--replay", action="store_true", help="re-score saved model answers instead of calling the model")
    a = ap.parse_args(argv)
    llm.REPLAY = llm.REPLAY or a.replay
    total, per = evaluate(a.method, a.scan, a.sheet or SHEETS)
    print_eval(a.method, a.scan, total, per)
    return 0


def main_compare(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="takeoff compare", description="score every method -> docs/results.md")
    ap.add_argument("methods", nargs="*", help="default: every built method")
    ap.add_argument("--replay", action="store_true", help="re-score saved model answers instead of calling the model")
    a = ap.parse_args(argv)
    llm.REPLAY = llm.REPLAY or a.replay
    print(compare(a.methods or None))
    return 0


FAMILY_RGB = {"fan": (0.85, 0.1, 0.1), "light": (0.95, 0.6, 0.0), "switch": (0.1, 0.35, 0.9),
              "receptacle": (0.0, 0.6, 0.2), "detector": (0.6, 0.0, 0.8)}
PLURAL = {"fan": "fans", "light": "lights", "switch": "switches", "receptacle": "receptacles", "detector": "detectors"}
KEY_STEPS = [
    ("Geometry matcher counted every sheet from the PDF's own strokes", "code: takeoff/methods/geometry.py",
     "2026-10-27"),
    ("Claude read every plan with its legend", "Claude Opus 5.5 via `claude -p`", "2026-10-28"),
    ("Every place the two disagreed was settled by eye on a zoomed crop of the vector PDF",
     "Claude Opus 5.5 (the coding assistant), crops kept in the author's notes", "2026-10-28"),
    (("Rooms checked against the wall-and-label room finder; 19 differences looked at: 3 fixed, 12 open-plan or "
     "unlabelled spots marked 'either room', 4 kept"), "Claude Opus 5.5, crops of each spot", "2026-10-28"),
    (("Seven independent runs lined up against the key (five one-shot coding agents, Claude and Codex; vision-consensus; "
     "geometry); every entry most of them disputed was zoomed: 1 device added, 2 attic lights accept pendant or wall"),
     "Claude Opus 5.5; runs in eval/oneshot/runs/", "2026-10-28"),
    ("Checked by a person (an estimator or the author)", "not done yet", "-"),
]


def _short(slug: str) -> str:
    fam = family(slug)
    rest = slug.removeprefix(fam).removeprefix("_") if slug.startswith(fam) else slug
    return rest.replace("_", " ") or "plain"


def key_report(out_md: Path = Path("docs/answer-key.md"), img_dir: Path = Path("docs/key")) -> str:
    """The answer key in a form a person can check: who made it, how, and per-room counts with a picture."""
    lines = ["# The answer key: what every number is scored against", "",
             ("No public dataset has verified counts for these plans, so this one was built for the project. "
             "Files: eval/answer_key/<sheet>.json (one entry per device: type, centre in PDF points, room)."), "",
             "| Step | Who / what | When |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in KEY_STEPS]
    lines += ["", "A device that every method missed is not in the key. That is why the person check matters.", ""]
    img_dir.mkdir(parents=True, exist_ok=True)
    for sid in SHEETS:
        key = load_key(sid)
        rooms_: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for d in key:
            rooms_[d["room"]][d["type"]] += 1
        fam_tot = collections.Counter(family(d["type"]) for d in key)
        lines += [f"## {sid}: {len(key)} devices", "",
                  " · ".join(f"{fam_tot[f]} {PLURAL[f]}" for f in FAMILIES if fam_tot[f]), "",
                  f"![{sid} answer key on the plan](key/{sid}.png)", "",
                  "| Room | " + " | ".join(PLURAL[f].capitalize() for f in FAMILIES) + " | Total |",
                  "|---|" + "---|" * (len(FAMILIES) + 1)]
        for room in sorted(rooms_, key=lambda r: (r == "Exterior", r)):
            c = rooms_[room]
            cells = []
            for f in FAMILIES:
                parts = [(n, _short(t)) for t, n in sorted(c.items()) if family(t) == f]
                total = sum(n for n, _ in parts)
                cells.append(f"{total} ({', '.join(f'{n} {t}' for n, t in parts)})" if total else "")
            lines.append(f"| {room} | " + " | ".join(cells) + f" | {sum(c.values())} |")
        either = [d for d in key if d.get("room_also")]
        if either:
            lines += ["", "Either room accepted: " + "; ".join(
                f"{_short(d['type'])} {family(d['type'])} at ({d['x']:.0f}, {d['y']:.0f}) {d['room']} or {d['room_also']}"
                for d in either) + "."]
        lines.append("")
        page = pymupdf.open(str(load_sheet(sid).pdf_path))[0]
        for d in key:
            rgb = FAMILY_RGB.get(family(d["type"]), (0.3, 0.3, 0.3))
            page.draw_circle((d["x"], d["y"]), 7, color=rgb, width=1.4)
            page.insert_text((d["x"] + 8, d["y"] + 2), _short(d["type"]), fontsize=4.5, color=rgb)
        f = load_sheet(sid).plan_frame
        page.get_pixmap(dpi=110, clip=pymupdf.Rect(f.x0, f.y0, f.x1, f.y1)).save(str(img_dir / f"{sid}.png"))
    lines += ["Colours on the pictures: red fans, orange lights, blue switches, green receptacles, purple detectors."]
    text = "\n".join(lines) + "\n"
    out_md.write_text(text)
    return text


def main_key(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="takeoff key", description="write docs/answer-key.md and its pictures").parse_args(argv)
    key_report()
    print("wrote docs/answer-key.md and docs/key/*.png")
    return 0
