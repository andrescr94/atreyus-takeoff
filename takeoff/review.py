"""What a person should look at first: flags on detections, and spots where a device may be missing.

Every check here is independent of the detector's own judgement, and each was measured against the answer key
(docs/DECISIONS.md, section 10): the flagged items are a few percent of the sheet and hold most of the errors.

    disagree   two independent reads disagree (two runs, or two vendors): different type, or only one found it
    label      the label printed next to the symbol (text layer or OCR) names another type of the same family
    unknown    the model answered with a type outside the vocabulary
    off-plan   the point lies outside the electrical plan frame
    missing    a cheap method (geometry, text, template) sees a symbol where no detection is: a suggested device

flag(sheet, detections) sets evidence["flag"] (a sentence a reviewer reads) on flagged detections and returns
the suggestions as extra Detections with evidence["suggested"] = True. Nothing is removed or retyped here.

Confidence is not the model's own number (models state confidence too high; kept as evidence["model_confidence"]).
It is the share of detections in the same evidence bucket that were right on the answer key, measured on
vision-consensus, vector and scan, 2026-10-28 (600 detections). LOW marks what the app lists under "Needs a look";
nothing waits on review.
"""
from __future__ import annotations

import math

from takeoff import methods
from takeoff.ingest import Sheet
from takeoff.match import Detection

LABEL_PT = 12.0  # a printed label this close to a detection describes it
MISSING_PT = 22.0  # a cheap-method hit farther than this from every detection is a possible miss
CHECKS = ("geometry", "text", "template")
CALIBRATION = {  # bucket: measured share right (right / detections in the bucket)
    "agreed": 0.996,  # 559 / 561, two reads agree and nothing else objects
    "single": 0.98,  # one read only, nothing objects: vision-crop precision 98.3% vector, 97.6% scan
    "label": 0.80,  # 8 / 10
    "disagree-type": 0.62,  # 5 / 8
    "disagree-found": 0.62,  # 8 / 13
    "missing": 0.40,  # 2 / 5
    "unknown": 0.0,  # 0 / 1
    "off-plan": 0.0,  # no sample; outside the plan is wrong by definition
}
LOW = 0.9  # below this a device is listed under "Needs a look"


def centre(d: Detection) -> tuple[float, float]:
    return (d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2


def _others(sheet: Sheet) -> dict[str, list[Detection]]:
    """Each cheap method's detections; a method that cannot run on this sheet (a scan) is skipped."""
    out = {}
    for name in CHECKS:
        try:
            out[name] = methods.get(name).detect(sheet)
        except Exception:  # noqa: BLE001 - a check that cannot run adds no flags; detection still stands
            out[name] = []
    return out


def _reason(d: Detection, sheet: Sheet, labels: list[Detection]) -> tuple[str, str | None]:
    """(calibration bucket, the sentence a reviewer reads or None)."""
    ev = d.evidence
    if ev.get("agreement") == "type differs":
        return "disagree-type", f"The two reads typed it differently ({d.slug} / {', '.join(ev.get('alternatives', []))})"
    if ev.get("agreement") == "1/2":
        return "disagree-found", "Only one of the two reads found it"
    if d.slug == "other":
        return "unknown", f"Type not in the vocabulary: {ev.get('raw_type') or ev.get('raw', {}).get('type', '?')}"
    x, y = centre(d)
    f = sheet.plan_frame
    if not (f.x0 <= x <= f.x1 and f.y0 <= y <= f.y1):
        return "off-plan", "Outside the electrical plan"
    for t in labels:
        if t.family == d.family and t.slug != d.slug and math.dist(centre(t), (x, y)) <= LABEL_PT:
            return "label", f"The printed label says {t.slug}"
    return ("agreed" if ev.get("agreement") == "2/2" else "single"), None


def flag(sheet: Sheet, detections: list[Detection]) -> tuple[list[Detection], list[Detection]]:
    others = _others(sheet)
    for d in detections:
        bucket, d.evidence["flag"] = _reason(d, sheet, others["text"])
        d.evidence.update(bucket=bucket, model_confidence=d.confidence)
        d.confidence = CALIBRATION[bucket]
    pts = [centre(d) for d in detections]
    suggestions: list[Detection] = []
    for name, found in others.items():
        for c in found:
            p = centre(c)
            if any(math.dist(p, q) <= MISSING_PT for q in pts + [centre(s) for s in suggestions]):
                continue
            c.evidence.update(suggested=True, flag=f"Possible missed device: {name} sees a {c.slug} here",
                              bucket="missing", model_confidence=None)
            c.confidence = CALIBRATION["missing"]
            suggestions.append(c)
    return detections, suggestions
