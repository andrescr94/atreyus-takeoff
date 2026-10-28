# Takeoff notes

Method: plans.pdf is vector with real text. I located the ELECTRICAL PLAN on each sheet, read the legend (sheet 1), then
- receptacles and switches: found by their vector signature (same size/path count as the legend symbols);
  subtype taken from the nearest printed label (GFI, AFI, 240V, WP, MW, DW, H; D, F, 3D, VS, A per legend);
  a duplex with only "FC" or no label is plain `receptacle`; the half-filled duplex is `receptacle_half_hot`.
- lights, fans, detectors: OpenCV template matching against the legend symbols (with rotations); recessed vs pendant vs wall by
  best score, EXT label -> light_wall_exterior, IAQ label -> iaq_fan, SD/MA -> smoke_co.
- ceiling fans: the "J" centre text of each 4-blade symbol (sheet 3 text is double-printed, de-duplicated).
- rooms: hand-built rectangles per sheet from the printed labels and walls; open areas use the nearest label.
  Everything outside the building outline is "Exterior".
- I overlaid detections on renders of each sheet and checked them by eye (sheets 1-4); several misses were fixed by hand-placed rules (240V receptacles hidden by pipe lines, attic switch, one Bath 2 light on sheet 4).

Unsure / judgement calls:
- Devices inside the "IN ATTIC" boxes (a light and a $ switch) are counted as light_pendant/light_wall + switch in the surrounding room.
- Unlabelled water-heater closets (B14) are put under Exterior (their receptacle is 240V GFI).
- "WP GFI 240V" at the B38 outdoor unit is typed receptacle_gfi_wp (WP wins over 240V).
- Pendant vs wall light is a close call from symbol score alone (no stem is drawn clearly in several places); no "VP" label occurs on the plans so light_recessed_vp = 0.
- FC (fan coil) outlets counted as plain `receptacle`. No standalone junction boxes were seen (J only appears inside fan symbols).
- Kitchen/Dining/Living boundaries in open plans are approximate; KITCHEN/LIVING split on sheet 4 is x-based. All CL closets are merged under "CL".
- Some very small/overlapped symbols (e.g. lights drawn on top of fixtures) may still be missed; positions are symbol centres within ~1-2 pt.
