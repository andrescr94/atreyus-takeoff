# Electrical takeoff – notes

## Method
- The PDF is vector, but every symbol is broken into many tiny line segments, so I couldn't detect symbols from the geometry. The text, though, is real text: GFI, AFI, DW, MW, 240V, WP, D, F, VS, 3D, A, EXT, SD, SD/MA, IAQ, FC, J. I pulled it with PyMuPDF to find each plan and its labels.
- I rendered each ELECTRICAL PLAN in zoomed tiles (about 4.5x) with a 10 pt grid in page coordinates. Then I read each device's symbol against the sheet's LEGEND and recorded its centre in PDF points (top-left origin).
- At the end I drew a circle over every recorded device on each sheet to check that the positions line up and nothing was missed.
- I assigned rooms by the walls. In open-plan areas (Kitchen/Dining/Living) with no walls between rooms, I gave each device to the closest room label, and gave counter devices to the kitchen.

## How I typed the symbols
- Switches: the letter next to the `$` sets the type: D → switch_dimmer, F → switch_fan, VS → switch_vacancy, 3D → switch_3way_dimmer, A (astronomical/motion sensor) → switch_sensor. A `$` with no letter is a plain switch.
- Half-filled circle with duplex lines at the kitchen sink → receptacle_half_hot. This is the disposal outlet, and a plain switch sits next to it. The yin-yang symbol over the range and in the baths → exhaust_fan. The same symbol with an IAQ tag → iaq_fan.
- 240V outlets (dryer, range, water heater, heat-pump condenser) → receptacle_240v, even when they are also tagged GFI or WP.
- FC (dedicated fan-coil outlet) → plain receptacle.
- Ceiling fan with J (fan pre-wire) → ceiling_fan, counted once. I did not also count it as a junction_box.
- Circle with crosshairs and a stem to the wall, over the vanity → light_wall.
- No recessed light has a VP tag, so all are light_recessed.

## What I'm unsure of
- **Water-heater closet (B14) 240V outlet.** On A1, A2 and A3 the closet only opens to the outside, so I put that outlet under Exterior. On A4 it opens off the Optional Bed 3 entry, so I put it under OPTIONAL BED 3.
- **Kitchen / Dining / Living split.** These are open-plan rooms with no walls between them, so which room a few lights, outlets and switches near the boundaries belong to is a judgement call. This applies to A2 (KITCHEN vs DINING), A3 (KITCHEN / DINING vs LIVING) and A4 (KITCHEN vs LIVING).
- **Attic light.** The light at the F03 attic access ("IN ATTIC") is a crosshair circle with no square, so I typed it light_wall. I put it, and its switch, under the room below it.
- **Things I left out.** I did not count the electrical panel (B32), the heat-pump unit itself, or the plumbing stub-outs. None of these are listed device types.
- **Position accuracy.** Coordinates are read from the grid, so they should be within about 1–2 pt.
