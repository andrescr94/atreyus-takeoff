# Design: AI Electrical Takeoff

Goal: an estimator gets a per-room count of fans, lights, switches, receptacles and detectors they can trust,
because every number points at marks on the plan and the few doubtful ones are flagged for a look.
The measured evidence behind each choice is in docs/DECISIONS.md; scores in docs/results.md.

## 1. Breakdown (pieces, in build order)
1. Ingest: PDF to sheets, rotation fixed, electrical-plan and legend frames found (rules, model fallback), scale read.
2. Answer key first: 300 devices on the 4 sheets, built from two independent methods (geometry and Claude) with every
   disagreement settled by eye. Nothing was chosen before it could be measured.
3. Detection behind one interface, eight methods scored on the same key: whole page to a model (45%), plan text (70%),
   legend templates (25%), vector geometry (90%, no scans), Claude on legend + plan crops (96.7%), the same asked twice
   (97.7%, default), a coding agent (97.3%), two agents from two vendors (99.0% on the scan).
4. Rooms: room labels + wall polygons; outside the building = Exterior.
5. Confidence: each device's evidence (two reads agree, printed label agrees, a cheap method also saw it) maps to the
   accuracy measured for that bucket; under 90% goes to review.
6. Store, API, review UI: SQLite + FastAPI + plain JS/SVG. Counts are derived from detections, never stored.
7. Evaluation as a command (`takeoff compare`) so every change gets a number.

## 2. First release
Does: upload a PDF, detect with type, position, room and confidence, per-room and per-home tables, every detection
drawn on the plan, add / move / retype / delete with counts updating at once, a short "Needs a look" queue.
Leaves out: bill of materials, multi-floor projects, accounts, export to estimating software.
Ship criterion: on sheets from a drafter not used for tuning, at least 97% of devices found with the right type,
fans and lights at least 95%, and the review queue under 10% of devices holding at least half the errors; checked
against hand counts from two pilot estimators.

## 3. Release
Pilot: two or three electrical estimators on real bids, side by side with their hand count. Versioning: every
takeoff stores the method, model id and prompt version that made it; a model or prompt change is a new version that
runs on new takeoffs only. Done takeoffs never change. Feature flags per method (fast vs thorough) and per customer.
A change ships only if `takeoff compare` on the key (and the held-out sheets) does not drop.

## 4. Support
Accuracy in production: every correction is an event (what the AI said, what the estimator changed it to), so
correction rate per type, per drafter and per model version is the live accuracy number. Learning from
corrections: corrected sheets join the answer key; recurring corrections become legend/policy rules per drafter
before any fine-tuning. New plan styles: the legend is read per sheet, never a global symbol list; unseen formats
fail loudly with a reason (tested on noise, blank, memo, rotated and scanned pages). Bug triage: a bad count
reproduces from the saved model answers (`--replay`) without calling the model again.

## 5. Trade-offs, and with more time or a team
- Default is two crop reads (108 s, about $0.67 per sheet API-equivalent), not the agent pair: equal on vector,
  4x faster. Agent pair stays the "thorough" option for scans.
- Accuracy is optimistic: prompts v2-v3 were tuned on these four sheets. Next: a key on another drafter's set.
- The key was checked by methods and by me, not by a licensed estimator.
- Bill of materials is built on the live detections (boxes by gang, fan-rated boxes, cable by type and length,
  priced list), but connectivity is the nearest switch box in the room, not the dashed switch legs. Next: trace the
  dashed legs from the vector strokes (geometry already groups segments) and verify the price list.
- With a team: user research on the review screen, an estimator-verified dataset, the API path with prompt caching
  to cut cost, export to the estimating tools customers already use.
