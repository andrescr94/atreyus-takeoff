"""Controlled device types: one slug per legend row kind, selected by keywords in the description.

Rows are tried top to bottom, so the more specific entries (two keywords) come before the family
fallbacks (one keyword). Every keyword must appear in the normalised description (upper case,
hyphens and slashes turned into spaces, runs of spaces collapsed). `is_electrical` marks what the
takeoff counts: plumbing stub-outs, hose bibbs, the attic panel, the wiring arc and the fan coil
unit (HVAC equipment; its outlet is drawn as a receptacle) are not devices.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceType:
    slug: str
    family: str  # switch | receptacle | light | fan | detector | junction | other
    keywords: tuple[str, ...]
    is_electrical: bool = True


# fmt: off
TYPES: tuple[DeviceType, ...] = (
    DeviceType("switch_3way_dimmer", "switch", ("SWITCH", "THREE WAY")),
    DeviceType("switch_vacancy", "switch", ("SWITCH", "VACANCY")),
    DeviceType("switch_dimmer", "switch", ("SWITCH", "DIMMER")),
    DeviceType("switch_fan", "switch", ("SWITCH", "FAN")),
    DeviceType("switch_sensor", "switch", ("SENSOR SWITCH",)),
    DeviceType("switch", "switch", ("SWITCH",)),
    DeviceType("receptacle_afi", "receptacle", ("DUPLEX", "ARC FAULT")),
    DeviceType("receptacle_240v", "receptacle", ("DUPLEX", "240")),
    DeviceType("receptacle_gfi_wp", "receptacle", ("DUPLEX", "WATERPROOF")),
    DeviceType("receptacle_half_hot", "receptacle", ("DUPLEX", "HALF HOT")),
    DeviceType("receptacle_gfi_mw", "receptacle", ("DUPLEX", "MICROWAVE")),
    DeviceType("receptacle_gfi_dw", "receptacle", ("DUPLEX", "DISH WASHER")),
    DeviceType("receptacle_hood", "receptacle", ("DUPLEX", "RANGE HOOD")),
    DeviceType("receptacle_gfi", "receptacle", ("DUPLEX", "GROUND FAULT")),
    DeviceType("receptacle", "receptacle", ("DUPLEX",)),
    DeviceType("light_recessed_vp", "light", ("RECESSED", "VAPOR")),
    DeviceType("light_recessed", "light", ("RECESSED",)),
    DeviceType("light_pendant", "light", ("PENDANT",)),
    DeviceType("light_wall_exterior", "light", ("EXTERIOR", "LIGHT")),
    DeviceType("light_wall", "light", ("WALL MOUNTED", "LIGHT")),
    DeviceType("light", "light", ("LIGHT",)),
    DeviceType("fan_coil", "other", ("FAN COIL",), is_electrical=False),  # before the FAN fallback
    DeviceType("ceiling_fan", "fan", ("CEILING FAN",)),
    DeviceType("exhaust_fan", "fan", ("EXHAUST FAN",)),
    DeviceType("iaq_fan", "fan", ("INDOOR AIR QUALITY",)),
    DeviceType("fan", "fan", ("FAN",)),
    DeviceType("smoke_co", "detector", ("SMOKE", "CARBON")),
    DeviceType("smoke_detector", "detector", ("SMOKE",)),
    DeviceType("detector", "detector", ("DETECTOR",)),
    DeviceType("junction_box", "junction", ("JUNCTION",)),
    DeviceType("water_cold", "other", ("COLD WATER",), is_electrical=False),
    DeviceType("water_hot", "other", ("HOT WATER",), is_electrical=False),
    DeviceType("hose_bibb_sov", "other", ("HOSE BIBB", "SHUT"), is_electrical=False),
    DeviceType("hose_bibb", "other", ("HOSE BIBB",), is_electrical=False),
    DeviceType("attic_panel", "other", ("ATTIC ACCESS",), is_electrical=False),
    DeviceType("wiring", "other", ("WIRING",), is_electrical=False),
    DeviceType("other", "other", (), is_electrical=False),
)
# fmt: on

_BY_SLUG = {t.slug: t for t in TYPES}


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[-/]", " ", text.upper()).split())


def classify_description(text: str) -> str:
    """Slug of the first row whose keywords all appear in the description, else "other"."""
    norm = normalise(text)
    for t in TYPES:
        if all(k in norm for k in t.keywords):
            return t.slug
    return "other"


def lookup(slug: str) -> DeviceType:
    return _BY_SLUG[slug]


def family_base(family: str) -> str:
    """Slug of the family's unqualified type (switch, receptacle, light, fan, detector, junction_box)."""
    return {"junction": "junction_box"}.get(family, family)
