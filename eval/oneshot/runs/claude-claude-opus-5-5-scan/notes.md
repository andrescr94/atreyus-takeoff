# Takeoff notes

**Method.** Each page is a single 5400x3600 grayscale scan with no vector or text layer, so I could not extract anything automatically. I pulled out the raster and rendered zoomed crops of each ELECTRICAL PLAN with a grid in PDF points. Then I read every symbol by eye against the sheet legend. The legend is the same on all four sheets.

To check the work, I drew every recorded device back onto the scan and confirmed each marker sits on its symbol. Coordinates are the symbol centres, read off the grid, and should be within about 2 pt. Room names are the plan labels as printed. Counts are built from the device list.

**Symbol → type decisions**
- Duplex receptacle labels: GFI → receptacle_gfi, AFI → receptacle_afi, "GFI 240V" → receptacle_240v, WP GFI → receptacle_gfi_wp, MW / DW → receptacle_gfi_mw / receptacle_gfi_dw. The half-filled receptacle under the kitchen sink → receptacle_half_hot. The FC (fan-coil) outlet → plain receptacle.
- The condenser outlet outside is labelled "WP GFI 240V". I counted it as receptacle_240v under Exterior, though receptacle_gfi_wp is also defensible.
- The yin-yang symbol at the range is counted as exhaust_fan (range hood fan), with the F switch next to it as switch_fan.
- Switches with no letter → switch. A → switch_sensor (astronomical/motion). VS → switch_vacancy. 3D → switch_3way_dimmer.
- The "IN ATTIC" fixture (plain circle with ticks, its own switch) has no legend match, so it is counted as `other`. Its switch is counted as a plain switch.
- No VP recessed lights, pendants, wall lights, junction boxes or plain smoke-only fixtures beyond the SD symbols were found on the plans.

**Uncertain**
- **Open-plan rooms:** where there are no walls (Kitchen/Dining/Living, Kitchen/Dining on A2), I assigned each device to the nearest room label, adjusted by judgement for counter and wall runs. Devices near those borders could belong to the neighbouring room.
- **Water-heater closet:** the small closet with an exterior door (keynote B14) has no label. Its 240V outlet is assigned to the nearest labelled room (BATH, BED 2, BATH 1, BATH 1).
- **Smoke detectors** that sit inside a bedroom door swing are counted in that bedroom.
- **A2-111, kitchen/dining wall:** two switches carry only one "3D" label. The upper one is counted as a plain switch.
- **A4-111:** the D / 3D / 3D switch group at the end of the kitchen wall is counted under KITCHEN, not HALLWAY.
