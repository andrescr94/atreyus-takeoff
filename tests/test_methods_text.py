"""Rung 2, text: label grouping and label-to-type mapping on hand-made tokens, plus sheet A1-111."""
from takeoff.ingest import load_sheet
from takeoff.methods import text
from takeoff.methods.text import Token


def _slugs(tokens: list[Token]) -> list[str]:
    groups = text.group_tokens(tokens)
    return sorted(s for s in (text.label_slug([t.text for t in g]) for g in groups) if s)


def test_stacked_label_is_one_device():
    # "WP" over "GFI" over "240V", 0.5 pt apart, as on A1-111 at (1152, 180)
    stack = [Token("WP", (1146.6, 170.0, 1157.7, 176.1)), Token("GFI", (1146.7, 176.6, 1157.5, 182.7)),
             Token("240V", (1144.6, 183.8, 1159.6, 189.9))]
    assert len(text.group_tokens(stack)) == 1
    assert _slugs(stack) == ["receptacle_240v"]


def test_switch_letters_on_one_line_are_separate_devices():
    line = [Token("3D", (1085.2, 571.3, 1093.5, 577.4)), Token("3D", (1095.0, 571.3, 1103.3, 577.4))]
    assert _slugs(line) == ["switch_3way_dimmer", "switch_3way_dimmer"]
    assert _slugs([Token("D", (0, 0, 4, 6)), Token("F", (5, 0, 9, 6))]) == ["switch_dimmer", "switch_fan"]


def test_heights_and_room_names_are_ignored():
    assert _slugs([Token("GFI", (0, 0, 11, 6)), Token('+42"', (0, 7, 13, 13))]) == ["receptacle_gfi"]
    assert _slugs([Token('+42"', (0, 0, 13, 6)), Token("LIVING", (20, 0, 40, 6)), Token("J", (50, 0, 53, 6))]) == []


def test_two_stacks_far_apart_stay_two_devices():
    assert _slugs([Token("AFI", (0, 0, 10, 6)), Token("AFI", (30, 0, 40, 6))]) == ["receptacle_afi"] * 2


def test_label_slug_priorities():
    assert text.label_slug(["GFI", "DW"]) == "receptacle_gfi_dw"
    assert text.label_slug(["WP", "GFI"]) == "receptacle_gfi_wp"
    assert text.label_slug(["SD/MA"]) == "smoke_co"
    assert text.label_slug(["EXT"]) == "light_wall_exterior"
    assert text.label_slug(["A01"]) is None


def test_split_label_undoes_ocr_joins():
    assert text.split_label("3D3D") == ["3D", "3D"]
    assert text.split_label("DVS") == ["D", "VS"]
    assert text.split_label("gfi") == ["GFI"]
    assert text.split_label("LIVING") == []
    assert text.split_label("F03") == []


def test_ocr_line_split_keeps_each_word_in_its_share_of_the_box():
    parts = list(text._split_line("3D3D AFI", 0, 0, 80, 10))
    assert [p[0] for p in parts] == ["3D", "3D", "AFI"]
    assert parts[0][1] == 0 and parts[1][1] == 20 and parts[2][1] == 50


def test_a1_from_the_text_layer():
    dets = text.detect(load_sheet("A1-111"))
    assert sum(d.family == "receptacle" for d in dets) >= 20
    assert all(d.evidence["source"] == "text layer" and d.confidence == 0.6 for d in dets)
