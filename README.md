# AI Electrical Takeoff

Counts the fans, lights, switches, receptacles and detectors on residential electrical plans, per room and per
home, draws every detection on the plan, and lets an estimator correct it with counts updating immediately.
Built for the Atreyus AI Stage 3 assessment on the four City of Escondido PAADU sheets in `plans/`.

Design document: [DESIGN.md](DESIGN.md). Every decision with its measured evidence: [docs/DECISIONS.md](docs/DECISIONS.md).
Scoreboard of all detection methods against the answer key: [docs/results.md](docs/results.md).

## Results (300-device answer key, 4 sheets)
| Method | Recall | Precision | Scan recall | s / sheet |
|---|---|---|---|---|
| vision-consensus (default) | 97.7% | 98.0% | 95.7% | 108 |
| agent-pair (thorough) | 97.3% | 98.0% | 99.0% | 449 |
| geometry (vector only, no AI) | 90.0% | 97.1% | 0% | 4 |
Fans 22/22, lights 58/63 on the default method. Errors are shown on the overlays, not hidden
(docs/key/, and `takeoff eval <method>` writes green/orange/red/purple overlays to data/eval/).

## Install
Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).
```bash
uv sync
```
The AI steps call Claude. Pick one:
- `claude` CLI signed in with a Claude subscription (default, `TAKEOFF_LLM=cli`), or
- the Anthropic API: `export TAKEOFF_LLM=api ANTHROPIC_API_KEY=...` (model via `TAKEOFF_MODEL`, default `claude-opus-5-5`).

## Run
```bash
uv run takeoff serve --port 8000
```
Open http://localhost:8000, upload `plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf`, and review.
Click a count to land on its marks; add, move, retype or delete a device and the room and home totals update.

Without the web app:
```bash
uv run takeoff import plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf   # same as an upload
uv run takeoff run A1-111                                                  # one sheet, counts table in the terminal
uv run takeoff compare --replay                                            # re-score every method from saved answers
uv run pytest                                                              # 112 tests against the real PDF
```

## How it works
1. Ingest (`takeoff/ingest.py`): split the PDF into sheets, straighten rotated pages, find the electrical plan
   and legend frames by their titles; if a rule fails, the model locates them (`takeoff/locate.py`); a page that
   is not an electrical plan stops with a reason.
2. Detect (`takeoff/methods/`): pluggable method. Default sends Claude two crops, the sheet's own legend and the
   plan at 2,400 px, twice; the project's counting rules live in `takeoff/policy.py`, not in the prompt.
3. Rooms (`takeoff/rooms.py`): room labels plus wall polygons; anything outside the building is Exterior.
4. Review (`takeoff/review.py`): confidence is the measured accuracy of each device's evidence bucket (both reads
   agree 99.6%, they disagree 62%...); under 90% goes to "Needs a look".
5. Store and API (`takeoff/store.py`, `takeoff/api.py`): SQLite, FastAPI; counts are derived from detections,
   never stored, so a correction changes them at once. Web UI in `web/` (no build step).

## Layout
`takeoff/` pipeline and API · `web/` review UI · `tests/` · `eval/answer_key/` the 300-device key and how it was
built · `eval/oneshot/` the coding-agent baseline · `docs/` decisions, results, answer-key overlays.

## How AI tools were used
Claude Code (Opus 5.5) wrote most of the code under my direction, from a phase plan with tests first; Codex was
used as a second vendor in the evaluation. I reviewed and can explain every part.

## Bill of materials (`takeoff/bom.py`)
Recomputed from the live devices on every request, so any correction changes it at once. Results page: priced list
with the source of every price. Plan: "Show bill of materials" draws each switch box with its gang size, fan-rated
boxes, and each cable run labelled with type and length.
- Boxes: switches within 14 pt share one box (2 switches = one 2-gang box + 2-gang plate); fans get fan-rated boxes.
- Cable: each light or fan is fed from the nearest switch box in its room, measured at 1/4" = 1' plus 8 ft up into
  the ceiling and back down and 1 ft slack per end (14/2); 3-way boxes are paired and joined with 14/3.
  Limit: the dashed switch legs on the plan are not traced yet, so connectivity is approximated by distance.
- Prices: Home Depot public prices (store 33040, 2026-10-24, pre-tax) where checked; other lines are marked "estimate".
