"""Rungs 1, 5 and 6, vision: show Claude pictures of the sheet and read back a JSON list of devices.

    vision-page       the whole sheet as one image (the naive approach, the baseline)
    vision-crop       the legend crop and a tight crop of the electrical plan, legend first
    vision-consensus  vision-crop asked twice; agreement is the confidence, disagreements go to review

Claude answers with centres as fractions of the image it saw; each image remembers the PDF rect it
was rendered from, so fractions map back to PDF points. Images only come from sheet.pdf_path, so
the same code runs on the scanned copy.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pymupdf

from takeoff import llm, policy
from takeoff.ingest import Sheet
from takeoff.match import Detection
from takeoff.methods import ELECTRICAL_SLUGS, Method, make_detection, open_page, rect

TARGET_PX = 2400  # long edge of every image; Claude downsizes anything above 2576
BBOX_DPI = 50  # low-resolution render used only to find the drawn content of the plan frame
INK = 200  # grey level below which a pixel is drawing (the watermark is ~225, paper 255)
EDGE = 0.015  # fraction of each side ignored when finding content: the frame's own border lines
MARGIN_PT = 15.0
AGREE_PT = 12.0  # two runs' centres this close are the same device

# Family catch-alls ("light", "fan", "detector") are left out of the list: offered them, the model answered
# "light" for wall lights it was unsure of (prompt v1: 93.6% recall; v2 without them: see docs/DECISIONS.md).
GENERIC = {"light", "fan", "detector"}
ALLOWED = ", ".join(s for s in ELECTRICAL_SLUGS if s not in GENERIC)
PROMPT_VERSION = 4  # v4: generic prompts, project rules moved to takeoff.policy, schema-enforced answers
# v2 added a switch-stack sentence that made stacked switch letters swap (removed in v3); v3 carried rules
# written for the Escondido sheets inside the prompt, v4 keeps them in the visible counting policy instead.

DEVICE_SCHEMA = {
    "type": "object",
    "properties": {"devices": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": [*ALLOWED.split(", "), "other"]},
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
            "room": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "note": {"type": "string", "description": "for type other: what the legend calls it"},
        },
        "required": ["type", "x", "y", "room", "confidence"],
    }}},
    "required": ["devices"],
}

_FIND = """Find every electrical device symbol (lights, fans, switches, receptacles, detectors, junction boxes) drawn \
on the electrical plan. The legend's own sample symbols, notes, title blocks and other drawings on the sheet are not \
devices, and neither are plumbing, mechanical or architectural symbols.
Allowed types: {allowed}. If an electrical symbol fits none of them, use "other" and say in note what the legend \
calls it.
For each device give its centre as fractions of the {image} width and height (x from left, y from top, 0..1, \
3 decimals), the room label printed on the plan that it sits in (outside the building: Exterior), and your \
confidence 0..1."""

PAGE_PROMPT = f"""You are doing an electrical takeoff. The image is one sheet of a plan set; it may contain an \
electrical plan, its legend and other drawings.
{_FIND.format(allowed=ALLOWED, image="image")}
{policy.text()}"""

CROP_PROMPT = f"""You are doing an electrical takeoff. The first image is the sheet's electrical LEGEND (symbol on \
the left, description on the right). The second image is the ELECTRICAL PLAN it belongs to.
Decide each symbol's type from the legend: match the symbol's shape to a legend row, then the letters printed \
next to it, which the legend defines. A legend row whose symbol carries letters applies only when those letters \
are printed next to the plan symbol; without them use the row drawn without letters.
{_FIND.format(allowed=ALLOWED, image="plan image")}
{policy.text()}"""


# ---- pure helpers (tested without the model) ----

def to_points(x: float, y: float, clip: pymupdf.Rect) -> tuple[float, float]:
    """Image fractions (0..1 from the top-left) to PDF points on the page the image was cut from."""
    return clip.x0 + x * clip.width, clip.y0 + y * clip.height


def content_bbox(gray: np.ndarray, edge: float = EDGE, ink: int = INK) -> tuple[int, int, int, int] | None:
    """Pixel box (x0, y0, x1, y1) around the drawing in a greyscale image, or None if it is blank.

    The frame's border lines would make the box the whole image, so a band of `edge` along every side
    is ignored, and so is any row or column that is mostly ink (a full-width line).
    """
    h, w = gray.shape
    mask = gray < ink
    ex, ey = max(1, round(w * edge)), max(1, round(h * edge))
    mask[:ey, :] = mask[-ey:, :] = False
    mask[:, :ex] = mask[:, -ex:] = False
    mask[mask.mean(axis=1) > 0.8, :] = False
    mask[:, mask.mean(axis=0) > 0.8] = False
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def tight_rect(page: pymupdf.Page, frame: pymupdf.Rect, margin: float = MARGIN_PT) -> pymupdf.Rect:
    """The part of `frame` that holds drawing, plus a margin (the whole frame if nothing is drawn)."""
    pix = page.get_pixmap(dpi=BBOX_DPI, clip=frame, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    box = content_bbox(gray.copy())
    if box is None:
        return frame
    sx, sy = frame.width / pix.width, frame.height / pix.height
    x0, y0, x1, y1 = box
    tight = pymupdf.Rect(frame.x0 + x0 * sx - margin, frame.y0 + y0 * sy - margin,
                         frame.x0 + x1 * sx + margin, frame.y0 + y1 * sy + margin)
    return tight & frame


def render(page: pymupdf.Page, clip: pymupdf.Rect, out: Path) -> Path:
    """PNG of `clip` with its long edge TARGET_PX pixels."""
    zoom = TARGET_PX / max(clip.width, clip.height)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).save(str(out))
    return out


def _number(value, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def slug_of(item: dict) -> str:
    t = str(item.get("type", "")).strip()
    return t if t in ELECTRICAL_SLUGS else "other"


def placed(items: list[dict], clip: pymupdf.Rect) -> list[dict]:
    """The model's items with their centre in PDF points (cx, cy); items without numeric x, y are dropped."""
    out = []
    for item in items:
        try:
            cx, cy = to_points(float(item["x"]), float(item["y"]), clip)
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"slug": slug_of(item), "cx": cx, "cy": cy, "item": item,
                    "confidence": _number(item.get("confidence"), 0.5),
                    "evidence": {"raw_type": item.get("note") or item.get("type")} if slug_of(item) == "other" else {}})
    return out


def merge(run0: list[dict], run1: list[dict], max_pt: float = AGREE_PT) -> list[dict]:
    """Two runs' placed items paired one-to-one, nearest first. Nothing is dropped:
    each result carries the kept item plus confidence and evidence for the review queue."""
    pairs = sorted(
        (math.dist((a["cx"], a["cy"]), (b["cx"], b["cy"])), i, j)
        for i, a in enumerate(run0) for j, b in enumerate(run1)
    )
    used0, used1, out = set(), set(), []
    for dist, i, j in pairs:
        if dist > max_pt:
            break
        if i in used0 or j in used1:
            continue
        used0.add(i)
        used1.add(j)
        a, b = run0[i], run1[j]
        if a["slug"] == b["slug"]:
            out.append({**a, "confidence": 0.95, "evidence": {**a.get("evidence", {}), "agreement": "2/2"}})
        else:
            out.append({**a, "confidence": 0.5,
                        "evidence": {**a.get("evidence", {}), "agreement": "type differs", "alternatives": [b["slug"]]}})
    for run, used in ((run0, used0), (run1, used1)):
        out += [{**d, "confidence": 0.4, "evidence": {**d.get("evidence", {}), "agreement": "1/2"}}
                for k, d in enumerate(run) if k not in used]
    return out


# ---- the three methods ----

def _image_dir(sheet: Sheet) -> Path:
    return Path(sheet.pdf_path).parent / "vision"


def _detections(sheet: Sheet, method: str, found: list[dict], model: str) -> list[Detection]:
    return [
        make_detection(sheet, n, d["slug"], d["cx"], d["cy"], d["confidence"], method=method,
                       room=d["item"].get("room"), model=model, raw=d["item"], **d.get("evidence", {}))
        for n, d in enumerate(found, 1)
    ]


def detect_page(sheet: Sheet) -> list[Detection]:
    page = open_page(sheet)
    png = render(page, page.rect, _image_dir(sheet) / f"{Path(sheet.pdf_path).stem}_page.png")
    answer = llm.ask(PAGE_PROMPT, [png], schema=DEVICE_SCHEMA)
    found = placed(answer.data["devices"], page.rect)
    return _detections(sheet, "vision-page", found, answer.model)


def _ask_crop(sheet: Sheet, run: int) -> tuple[list[dict], llm.Answer]:
    page = open_page(sheet)
    stem = Path(sheet.pdf_path).stem
    legend = render(page, rect(sheet.legend_frame), _image_dir(sheet) / f"{stem}_legend.png")
    clip = tight_rect(page, rect(sheet.plan_frame))
    plan = render(page, clip, _image_dir(sheet) / f"{stem}_plan.png")
    answer = llm.ask(CROP_PROMPT, [legend, plan], run=run, schema=DEVICE_SCHEMA)
    return placed(answer.data["devices"], clip), answer


def detect_crop(sheet: Sheet) -> list[Detection]:
    found, answer = _ask_crop(sheet, run=0)
    return _detections(sheet, "vision-crop", found, answer.model)


def detect_consensus(sheet: Sheet) -> list[Detection]:
    """The two reads run at the same time; their answers are added to the caller's ledger."""
    with ThreadPoolExecutor(2) as pool:
        (run0, a0), (run1, a1) = pool.map(lambda r: _ask_crop(sheet, run=r), (0, 1))
    llm.LEDGER.extend([a0, a1])
    return _detections(sheet, "vision-consensus", merge(run0, run1), a0.model)



METHODS = {
    "vision-page": Method(
        name="vision-page", rung=1, needs="an image",
        idea="brute force: the whole sheet as one image, Claude finds and types every device",
        detect=detect_page,
    ),
    "vision-crop": Method(
        name="vision-crop", rung=5, needs="an image",
        idea="Claude sees the legend crop and a tight crop of the electrical plan, with typing rules",
        detect=detect_crop,
    ),
    "vision-consensus": Method(
        name="vision-consensus", rung=6, needs="an image",
        idea="vision-crop asked twice; agreement is the confidence, disagreements go to the top of the "
             "review queue",
        detect=detect_consensus,
    ),
}
