# Answer key: the four Escondido sheets (A1-111 .. A4-111)

One JSON per sheet: every electrical device with type, centre in PDF points, room. 300 devices in all
(55, 66, 82, 97). Built 2026-10-28. Readable version with per-room tables and pictures: docs/answer-key.md.

## How it was made
Two independent methods ran on each sheet: the vector geometry matcher (takeoff/geometry.py, legend.py, match.py)
and Claude reading the plan image with the legend (prompt, raw runs and zoom crops not kept in the repo).
Where they agreed (same place, same type) the device was accepted. Every disagreement was settled by eye on a
zoomed crop of the vector PDF (contact sheets c_A*.png, zoom_*.png in the evidence folder).

## Rulings
- A1: kitchen sink half-filled circle = "Duplex outlet AFCI-half hot" (both had missed or mistyped it);
  plain "$" at the sink (973,356) and the 240V GFI at (967,426) are real (vector missed both, AI missed the "$");
  bath vanity circle-with-ticks at (1335,291) = wall-mounted light (vector missed).
- A2: vector missed two 240V GFI receptacles, a recessed light, an AFI receptacle and the Bed 2 ceiling fan;
  the "3D A" switch pair at the entry was swapped by the vector matcher.
- A3: all methods first missed both bath vanity wall lights at the sinks (1029,374) and (1343,317); found by
  the claude -p run and confirmed by zoom. Vector missed three recessed lights, the IAQ fan and a 240V GFI; AI placed the DW receptacle 20 pt off
  (moved to the true spot).
- A4: vector missed the EXT light, the kitchen half-hot outlet, a 240V GFI in laundry, a Bath 2 recessed light,
  the IAQ fan; vector typed three recessed lights as wall lights and two switches wrong. AI missed the Bath 1
  vanity wall light, typed the laundry exhaust fan as a receptacle and the upper 240V GFI as GFI.
- Policy: the keyless light inside the IN ATTIC callout counts as light_wall (no "P", so not pendant).

## Agreement round (2026-10-28, afternoon)
Seven independent runs were lined up against the key: five one-shot coding agents (Claude Opus 5.5, Sonnet 5.5,
Fable 5.1; Codex GPT-6-Astra, GPT-6.1-Sol; eval/oneshot/), the vision-consensus method and the geometry method.
Key entries most runs disagreed with, and spots several runs found that the key lacked, were zoomed and ruled:
- A4 Bath 2 vanity wall light at the sink (1516,253): added (3 of 7 runs found it; same drawing as the Bath 1
  vanity light already in the key).
- IN ATTIC lights on A1 (1118,437) and A4 (1563,321): the glyph is close to the pendant symbol but carries no
  "P"; 4 of 7 runs read pendant. Both types accepted (type_also).
- A2 switch pair at (899/906, 254): key right (two switches, one labelled 3D).
- A3 vanity lights: key kept (4 of 7 runs miss them; confirmed earlier by zoom).
Rooms: checked against the wall-and-label room finder; 3 fixed, 12 open-plan or unlabelled spots accept either
room (room_also).

## Limits
- Not yet checked by a person. A device that every run missed is not in the key. The least certain entries are
  the vanity wall lights drawn over the sink faucets.
