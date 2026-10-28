"""Tests for takeoff.vocabulary and takeoff.legend on the Escondido sheets.

Numbers measured first-hand on 2026-10-27 with `uv run python -m takeoff.legend A1-111` and by
looking at data/legend_A1-111.png. Change them only with a new measurement.
"""
import pymupdf
import pytest

from takeoff import ingest, legend, vocabulary


class TestClassifyDescription:
    def test_legend_rows_of_sheet_1_map_to_the_controlled_list(self):
        cases = {
            "ELECTRICAL SWITCH": "switch",
            "ELECTRICAL SWITCH-THREE WAY-DIMMER": "switch_3way_dimmer",
            "ELECTRICAL SWITCH- VACANCY SENSOR": "switch_vacancy",
            "ELECTRICAL SWITCH-DIMMER": "switch_dimmer",
            "ELECTRICAL SWITCH-FAN": "switch_fan",
            "ASTRONOMICAL TIME OR MOTION SENSOR SWITCH (AT LEAST 1 REQ.)": "switch_sensor",
            "EXHAUST FAN": "exhaust_fan",
            "INDOOR AIR QUALITY FAN": "iaq_fan",
            "PENDANT LIGHT HIGH-EFFICACY": "light_pendant",
            "WALL MOUNTED HIGH-EFFICACY LIGHT": "light_wall",
            "EXTERIOR WALL MOUNTED HIGH- EFFICACY LIGHT W/ PHOTO SENSOR": "light_wall_exterior",
            "RECESSED HIGH- EFFICACY DOWNLIGHT": "light_recessed",
            "RECESSED HIGH- EFFICACY DOWNLIGHT-VAPOR PROOF": "light_recessed_vp",
            "SMOKE DETECTOR/ALARM": "smoke_detector",
            "COMBINATION SMOKE/CARBON MONOXIDE": "smoke_co",
            "ELECTRICAL JUNCTION BOX": "junction_box",
            "CEILING FAN OPTIONAL (PRE WIRE FOR CEILING FAN ONLY)": "ceiling_fan",
            "DUPLEX OUTLET ARC-FAULT CIRCUIT INTERRUPTER": "receptacle_afi",
            "DUPLEX OUTLET 240 VOLTS": "receptacle_240v",
            "DUPLEX OUTLET GROUND FAULT INTERRUPTER": "receptacle_gfi",
            "DUPLEX OUTLET WATERPROOF GROUND FAULT INTERRUPTER": "receptacle_gfi_wp",
            "DUPLEX OUTLET AFCI-HALF HOT": "receptacle_half_hot",
            "DUPLEX OUTLET MICROWAVE GROUND FAULT INTERRUPTER": "receptacle_gfi_mw",
            "DUPLEX OUTLET DISH WASHER GROUND FAULT INTERRUPTER": "receptacle_gfi_dw",
            "DUPLEX OUTLET RANGE HOOD": "receptacle_hood",
            "COLD WATER STUB OUT": "water_cold",
            "WATER HOSE BIBB WITH SHUT OF VALVE": "hose_bibb_sov",
            "FAN COIL UNIT +80\" TO BOTTOM OF UNIT PROVIDE DEDICATED OUTLET": "fan_coil",
            '22"X30" MIN. ATTIC ACCESS PANEL (WHERE REQ.)': "attic_panel",
            "ELECTRICAL WIRING": "wiring",
            "SOMETHING NEVER SEEN": "other",
        }
        for text, slug in cases.items():
            assert vocabulary.classify_description(text) == slug, text

    def test_family_and_electrical_flag(self):
        assert vocabulary.lookup("receptacle_gfi_wp").family == "receptacle"
        assert vocabulary.lookup("ceiling_fan").family == "fan"
        assert vocabulary.lookup("smoke_co").family == "detector"
        assert vocabulary.lookup("switch_3way_dimmer").is_electrical
        for slug in ("water_cold", "water_hot", "hose_bibb", "hose_bibb_sov", "attic_panel", "wiring"):
            assert not vocabulary.lookup(slug).is_electrical, slug
        assert vocabulary.lookup("other").family == "other"


PDF = "data/sheets/A1-111/page.pdf"


@pytest.fixture(scope="module")
def templates():
    sheet = ingest.load_sheet("A1-111")
    page = pymupdf.open(PDF)[0]
    return legend.parse_legend(page, sheet.legend_frame)


def _by_slug(templates, slug):
    found = [t for t in templates if t.slug == slug]
    assert len(found) == 1, [t.description for t in found]
    return found[0]


class TestParseLegendSheet1:
    def test_waterproof_gfi_row(self, templates):
        t = _by_slug(templates, "receptacle_gfi_wp")
        assert t.family == "receptacle"
        assert "WATERPROOF" in t.description
        assert {"GFI", "WP"} <= set(t.qualifiers)
        assert t.is_electrical
        assert t.signature[0] >= 12  # the duplex symbol: 12-gon circle + two verticals (14 segments)

    def test_plain_switch_row(self, templates):
        t = _by_slug(templates, "switch")
        assert t.description == "ELECTRICAL SWITCH"
        assert t.qualifiers == []
        assert t.signature[:3] == (15, 5.3, 10.2)  # "$": 15 segments, 5.3 x 10.2 pt

    def test_ceiling_fan_row_merges_the_blades(self, templates):
        t = _by_slug(templates, "ceiling_fan")
        x0, y0, x1, y1 = t.bbox
        assert (x1 - x0, y1 - y0) == pytest.approx((114.8, 114.8), abs=0.5)
        assert len(t.component_ids) == 3  # horizontal blades + hub, upper blade, lower blade

    def test_note_blocks_have_no_template(self, templates):
        assert not [t for t in templates if "NOTE" in t.description]
        assert not [t for t in templates if "IONIZATION" in t.description]

    def test_template_count_and_unpaired_rows(self, templates):
        # 32 rows on the sheet-1 legend: 28 have a symbol-sized component; the four plumbing rows
        # (COLD WATER, HOT WATER, HOSE BIBB, HOSE BIBB WITH SHUT OFF VALVE) draw a cross smaller
        # than 3 pt and so give no template, which is fine because they are not counted anyway.
        assert len(templates) == 28
        assert sum(t.is_electrical for t in templates) == 25  # 6 switches, 2 fans, 5 lights, 2 detectors, J, ceiling fan, 8 receptacles
        assert not {t.slug for t in templates} & {"water_cold", "water_hot", "hose_bibb", "hose_bibb_sov"}
        assert [t.row_id for t in templates] == [f"L{i:02d}" for i in range(1, 29)]
        assert {t.family for t in templates} == {"switch", "receptacle", "light", "fan", "detector", "junction", "other"}

    def test_detector_rows_share_a_shape_and_differ_by_qualifier(self, templates):
        sd, co = _by_slug(templates, "smoke_detector"), _by_slug(templates, "smoke_co")
        assert sd.signature[:4] == co.signature[:4] == (17, 13.3, 13.4, 42.2)  # circle + dot
        assert sd.qualifiers == ["SD"] and co.qualifiers == ["SD/MA"]


def test_main_prints_rows_and_writes_overlay(capsys):
    assert legend.main(["A1-111"]) == 0
    out = capsys.readouterr().out
    assert "A1-111: 28 templates" in out
    assert "L24 receptacle_gfi_wp" in out
    assert pymupdf.Pixmap("data/legend_A1-111.png").width > 100
