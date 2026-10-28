[auto] You are doing an electrical takeoff. Work alone, do not ask questions, and use only the files in this
folder.

`plans.pdf` is a residential plan set with 4 pages. Each page is one sheet (sheet ids A1-111 .. A4-111, printed
on the sheet). Among other drawings, each sheet has an ELECTRICAL PLAN of one home and its symbol LEGEND.
A Python with pymupdf, opencv, numpy, pillow and rapidocr is at `./env/bin/python`. Use any approach you like.

## What to deliver, per sheet
1. Every electrical device on the ELECTRICAL PLAN (not in the legend, not on other drawings): its type, its
   position, and the room it is in.
2. Counts per room and totals: how many ceiling fans, lights (by type), switches (by type) and receptacles
   (by type) in each room and in the whole home.

Rooms are defined by the plan labels (Living, Kitchen / Dining, Bedroom, Bath, Laundry, Closet, ...).
Using the legend is part of the challenge.

Counting rules: Kitchen and Dining are separate when labelled separately. Components outside the building count
under Exterior. A combined fixture (fan/light, smoke/CO) counts once under its combined type. Switch types follow
the letter next to the symbol (D dimmer, F fan, 3 three-way); the legend and notes define the letters.

## Types (use exactly these names)
Switches: switch, switch_dimmer, switch_3way_dimmer, switch_fan, switch_vacancy, switch_sensor.
Receptacles: receptacle, receptacle_gfi, receptacle_afi, receptacle_240v, receptacle_gfi_wp, receptacle_gfi_mw
(microwave), receptacle_gfi_dw (dishwasher), receptacle_hood, receptacle_half_hot (half-switched duplex).
Lights: light_recessed, light_recessed_vp (vapor proof), light_pendant, light_wall, light_wall_exterior.
Fans: ceiling_fan, exhaust_fan, iaq_fan (indoor air quality fan).
Detectors: smoke_detector, smoke_co. Other: junction_box, other.

## Output
Write `result.json` in this folder:

```json
{"sheets": [{"sheet": "A1-111", "page": 1,
  "devices": [{"type": "receptacle_gfi", "x": 1234.5, "y": 456.7, "room": "KITCHEN"}],
  "counts": {"KITCHEN": {"receptacle_gfi": 3}, "TOTAL": {"receptacle_gfi": 3}}}]}
```

x and y are the device's centre in PDF points on that page (origin top-left, the page is 2592 x 1728 pt,
the coordinates PyMuPDF uses). Room names as printed on the plan; outside the building is "Exterior".
Also write `notes.md`: a few lines on how you did it and what you are unsure of.
