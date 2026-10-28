# Electrical takeoff notes

## Method
- The PDF is fully vector. Every symbol on the ELECTRICAL PLAN is drawn as thin (0.24 pt) black line segments, so I worked
  on the geometry instead of OCR: `takeoff.py` (with `cluster.py`) clips each sheet to its electrical plan only
  (the mechanical/finish plans and the legend are outside the clip), drops segments longer than 16 pt (wall outlines),
  and groups the remaining segments into connected clusters.
- Symbol families come from the legend geometry: receptacle = circle r≈3.9 pt with two parallel 12 pt lines
  (three lines for 240V/MW/hood, black half-fill = AFCI half hot); switch = 13.6 pt line with an S-curve on it;
  recessed light = circle r≈4.5 in a 9 pt square; wall/pendant light = circle r≈4.5 with ticks and a stem;
  smoke detector = circle r≈6.75; exhaust / IAQ fan = circle r≈4.5 with a black half-fill; ceiling fan = big blade
  cluster with the "J" hub; junction box = bare circle r≈3.9 with "J". Circles are found with a local RANSAC fit so
  symbols that overlap fixtures (sink, range burners, dryer hatch, water heater) are still recognised.
- The letters next to a symbol (D, 3D, F, VS, A; GFI, AFI, WP, MW, DW, 240V, H, FC, +42"; SD, SD/MA; IAQ; EXT +84; VP; P)
  are read from the PDF text layer and matched to the nearest compatible symbol, one label of each kind per device.
  Type precedence for receptacles: MW > DW > H > 240V > WP > GFI > AFI > plain.
- Rooms: wall fills, heavy wall/window lines, door swings and thin fixture lines are rasterised; room label text positions
  are seeds for a geodesic (wall-respecting) flood fill, so open-plan zones (kitchen / dining / living / hallway) are
  split at the geodesic midpoint between labels. Later passes let labels cross cabinet and fixture outlines
  (but not walls) so counter receptacles get their room. Page edges seed "Exterior".
- Every sheet was rendered with the detected devices and room map overlaid and checked by eye; the counts below
  reflect those checks.

## Judgement calls / uncertainty
- "FC" (fan coil unit, "provide dedicated outlet"): counted once as a plain `receptacle`; the FC cabinet itself is not
  counted. The electrical panel (keynote B38, outside the house) is counted once as `other` on each sheet.
- The "WP GFI 240V" outlet drawn in the panel enclosure is typed `receptacle_240v` (240V wins over WP).
- The water-heater (B14) outlets: on sheets 1 and 2 the closet is reached only from outside, so they are `Exterior`
  (sheet 2 needed a manual override; the flood fill leaked from BED 2 through a gap). On sheets 3 and 4 the closet is
  inside and unlabeled, so the outlet takes the nearest room (BATH 1).
- Counter/appliance receptacles (GFI +42"/+30", DW, MW, half-hot, 240V range) that the geodesic split put in
  LIVING or DINING were reassigned to the kitchen label (sheet 1: 2, sheet 2: 3, sheet 4: 1). The open-plan split of
  recessed lights between kitchen/dining and living is the geodesic midpoint and is inherently fuzzy.
- The "IN ATTIC" box holds a wall-mounted light and a plain switch (attic access light); they are counted in the room
  under the box (sheet 1 KITCHEN / DINING + LIVING, sheet 2 DINING + HALLWAY, sheet 3 HALL, sheet 4 HALLWAY).
- The sink-disposal receptacle (black half-filled circle, no label) is counted as `receptacle_half_hot` with its
  unlabeled plain switch nearby (sheets 1, 3, 4). No range-hood (H) outlet, pendant, vapor-proof light or stand-alone
  junction box appears on any plan; the fan hubs' "J" circles are part of the ceiling fans.
- Sheet 3 has duplicated text objects (SD, SD/MA, IAQ, J printed twice); the duplicates are ignored, devices are not doubled.
- Closets labelled "CL" contain no devices on any sheet (only the sheet 4 WALK-IN CLOSET has a recessed light).
