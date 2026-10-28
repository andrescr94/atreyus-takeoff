# Decisions and the evidence behind each

Every number here comes from `uv run takeoff compare` (table in docs/results.md) or `uv run takeoff eval
<method> [--scan]`, scored against the 300-device answer key in eval/answer_key/ (readable version with who,
how and when: docs/answer-key.md). Overlays for every method and sheet are in data/eval/<method>/ (green
right, orange wrong type, red ring missed, purple extra). Every Claude call is live; each answer is also
saved in data/answers/ as the record behind the numbers, and `--replay` re-scores those saved answers.

## 1. Measure before choosing: build an answer key that can prove us wrong
No public dataset has verified counts for US residential electrical plans (FloorPlanCAD, ArchCAD-400K and
CubiCasa5K are non-commercial and have no electrical classes). So the key was built from two independent
methods, geometry and Claude, and every disagreement was settled by eye on a zoomed crop of the PDF.
Evidence: the first "verified" hand count (51 devices on sheet A1) was wrong; the two-method check found
29 devices across the four sheets that the earlier count and the geometry method had missed or mistyped,
including a ceiling fan. Rooms were then checked against a third, independent method (walls + room labels):
19 of 299 differed; 3 were key errors (fixed), 12 sit in open kitchen/dining/living space or an unlabelled
closet where either room is defensible (both accepted, listed in docs/answer-key.md), 4 the key had right.
Limits: no person has checked it yet; a device every method missed is not in it.

## 2. The brute-force way does not work: whole sheet to a vision model (rung 1)
| | Recall | Precision | Scan recall |
|---|---|---|---|
| vision-page | 38.5% | 53.5% | 34.8% |
A 36x24 in sheet shrunk to 2,400 px makes a receptacle about 8 px wide, and positions come back more than
22 pt off. It is the obvious approach. It is the baseline, not the product.

## 3. Text alone types well but cannot find unlabelled symbols (rung 2)
| | Recall | Precision | Scan recall | Fans | Lights |
|---|---|---|---|---|---|
| text | 70.6% | 99.5% | 62.5% (OCR) | 4/22 | 5/62 |
Labels (GFI, AFI, 3D, SD...) decide the type of almost every receptacle and switch, at 99.5% precision.
But recessed lights, ceiling and exhaust fans carry no label, and the brief weights fans and lights most.
OCR on the scan reads "240V" as "24QV" and receptacle circles as "D"; its small fix table was tuned on these
four sheets, so the scan number is optimistic.

## 4. Classic computer vision finds where, not what (rung 3)
| | Recall (right type) | Found at the right place, any type | Scan recall |
|---|---|---|---|
| template | 25.4% | 252/299 (84%) | 0% (refuses) |
A legend crop used as a stamp (Bluebeam VisualSearch's idea) finds most devices, but a GFI and an AFI
receptacle are the same circle with different letters, so the type is lost. It also needs the vector legend
to know where the legend symbols are, so it cannot run on a scan (an earlier version borrowed the vector
legend for its scan score; removed). Kept in the repo as a measured dead end, not shown in the demo.

## 5. Geometry is exact and free, but only on vector PDFs (rung 4)
| | Recall | Precision | Scan recall | s / sheet |
|---|---|---|---|---|
| geometry | 90.3% | 97.1% | 0.0% | 1 |
The drawing's own strokes give exact boxes at no cost, but symbols that touch other strokes (a switch drawn
into a sink outline, a receptacle on a cooktop circle) fuse and are missed, including one ceiling fan, and
a scan has no strokes at all. Production sees scans, so this is not the detector. It stays as the second
method that keeps the answer key honest.

## 6. Claude on the right crops, with the sheet's legend: the detector (rung 5)
| | Recall | Precision | Scan recall | s / sheet | $ / sheet (API-equivalent) |
|---|---|---|---|---|---|
| vision-crop | 96.3% | 98.0% | 97.3% | 54 | 0.36 |
Two images instead of one: the legend crop and a tight crop of the electrical plan, each 2,400 px on the
long edge, so a receptacle is about 30 px. The same pipeline handles the scanned copy. Changes measured:
- Model: `claude -p` on the subscription default (Fable 5.1) scored 93.3%; Opus 5.5 scored 97.3% in the
  probe. The backend passes `--model opus`.
- Prompt v1 offered catch-all types ("light", "fan", "detector") and the model used them for unsure wall
  lights: 93.6%. v2 removed them and said legend letters apply only when printed (94.3%), plus a sentence
  about switch stacks that made stacked labels swap. v3 dropped that sentence: 96.3%.
- Caveat: v2 and v3 were tuned after looking at errors on these same four sheets, so 96.3% is optimistic.
  The honest check is a sheet from another drafter (Orange County ADU set, not yet in the key).
Remaining errors repeat across runs: bath vanity wall lights at sinks missed, stacked switch letters printed
sideways (D over F) swapped.

## 7. Confidence: ask twice, review what the two answers disagree on (rung 6)
| | Recall | Precision | Scan recall | s / sheet | $ / sheet |
|---|---|---|---|---|---|
| vision-consensus | 97.0% | 97.0% | 97.7% | 104 | 0.67 |
A model's stated confidence is known to be overconfident; agreement between independent runs is the
strongest general signal in recent work. Measured here: flagging the items where the two runs disagree
sends 16 of 299 detections (5%) to review, and those hold 5 of the 9 wrong detections (31% of flagged
items are wrong, against 1.4% of agreed ones). On the scan: 3% flagged, 2 of 5 wrong detections caught.
It costs a second call per sheet. Errors both runs make the same way are not caught: those are what the
estimator's review is for.

## 8. One door to the model, two keys
`takeoff/llm.py`: `claude -p --model opus` on a Claude subscription for the demo (TAKEOFF_LLM=cli, default),
or the Anthropic API with the customer's ANTHROPIC_API_KEY (TAKEOFF_LLM=api). Anthropic's terms: products
for other people use API keys. Calls are live. Each answer is saved with its inputs so any reported number can
be re-scored later (`--replay`), which is for checking the scoreboard, never for the demo.

## 9. The baseline every competitor can reach: a coding agent given the PDF and the brief (rung 0)
| | Recall | Precision | Room | Table | Scan recall | Time, 4 sheets | Cost, 4 sheets |
|---|---|---|---|---|---|---|---|
| Claude Opus 5.5 one-shot | 98.3% | 99.3% | 98.7% | 94.7% | 96.3% | 11 min | $3.36 (API-equivalent) |
| Codex GPT-6.1-Sol one-shot | 98.7% | 97.4% | 98.3% | 92.5% | 99.7% | 9 min | subscription |
| Claude Sonnet 5.5 one-shot | 87.3% | 96.0% | 95.2% | 72.1% | not run | 12 min | $1.58 |
eval/oneshot/: prompt.md (the brief's requirements, the type list, the output format), run.py (agent in a folder
outside the repo, sandboxed, Python with pymupdf/opencv/OCR available). Transcripts were checked: no run touched the
repo or the answer key. On the vector PDF the agents wrote stroke + text code; on the scan they zoomed into the
plan, read each symbol against the legend by eye and drew their marks back on the page to check. That self-check
loop is what our single-call vision-crop lacks, and it beats it by about 2 points.
Merging the Claude and Codex agent runs (same place and type = agreed, otherwise flagged): vector 99.3% recall,
11 items flagged (3.6%) holding 4 of 6 errors; scan 98.0%, 14 flagged (4.6%) holding 9 of 10 errors; no device
missed by both. Two vendors disagree where the mistakes are.
Caveats: one run per model (no variance yet); the agreement round that added one device and accepted two attic
light types used these runs, which favours them slightly; an agent run is minutes, not seconds.

## 10. Review is the product: a short queue that holds most of the errors
Cross-checks on vision-consensus: the two runs disagree, the printed label (text rung) disagrees with the type,
or a cheap rung (geometry, text, template) sees a symbol Claude did not. Vector: 22 items (7% of 299) hold 8 of the
12 errors. Scan: 14 items (5%) hold 5 of 11. The cheap rungs earn their place here, as checks, not as detectors.
Competitor pattern for comparison (notes research-estimating.md, 2026-10-24, vendor docs): a confidence threshold,
then the estimator reviews every hit on the drawing and adds missed symbols by hand (Trimble "Add Missed Symbols").

## 11. Generic prompts, project rules in a visible policy (prompt v4)
v3 carried rules written after reading the Escondido legends inside the prompt. v4 says only what holds for any
electrical plan (read the legend, then the letters beside each symbol) and appends takeoff/policy.py: the brief's
counting rules plus three type mappings, readable and editable per project. Answers are schema-enforced JSON
(`--json-schema` on the CLI, a forced tool call on the API). Result: vision-consensus 97.7% vector (v3: 97.0%),
95.7% scan. Generic did not cost accuracy.

## 12. The agent as a method (rungs 7, 8): best on scans, not on vector
| | Vector | Scan | s / sheet | tokens in / sheet |
|---|---|---|---|---|
| vision-consensus | 97.7% | 95.7% | 108 | 223k |
| agent (Claude Code) | 97.3% | 97.7% | 183 | 904k |
| agent-pair (Claude + Codex) | 97.3% | 99.0% | 449 | 1,713k |
On vector sheets the two-read crop method matches the agents at a quarter of the time and an eighth of the tokens.
On scans the agent pair is best. Default stays vision-consensus ("fast"); agent-pair is the "thorough" option.
If one agent crashes, the other's answer stands, marked "second opinion failed" (single-read confidence).
Every `claude -p` call carries ~46k tokens of Claude Code's own system prompt; the API backend does not.

## 13. Confidence = measured accuracy of the evidence bucket, not the model's own number
Measured on vision-consensus, vector + scan, 600 detections: both reads agree 99.6% right; printed label disagrees
80%; reads typed it differently 62%; only one read found it 62%; suggested missed device 40%; single read 98%.
takeoff/review.py turns each device's evidence into that number; under 90% is "Needs a look". Nothing waits on
review: counts and exports use every device; review only changes provenance (AI -> confirmed) or the device.

## 14. Fail loudly, and the formats we have not seen
Rules read the sheets we know; when a rule fails the model locates plan and legend (takeoff/locate.py), and a page
that is not an electrical plan stops with the model's reason (tested: random noise, blank page, text memo, not a
PDF). Rotated pages (/Rotate 270 on both unseen sets) are straightened at ingest so every coordinate shares one
frame. The San Diego County and Orange County sheets ingest end to end through the model fallback (frames checked
by eye); they have no answer key, so they show generality, not accuracy.

## 15. Match radius
Recall barely moves between 16 and 44 pt for every method that places devices well (vision-consensus 97.3% at
16 pt, 97.7% at 22 and 44); only brute force (vision-page) gains from a loose radius (15% at 8 pt, 45% at 22 pt).
22 pt (about 1.2 ft at 1/4 in = 1 ft) does not flatter the methods we ship. Table in docs/results.md.
