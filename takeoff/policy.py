"""Counting policy: the rules an estimator sets for a project, kept apart from the detection prompts.

Detection prompts say only what holds for any electrical plan (read the legend, count symbols on the plan).
Everything that is a decision about *how to count* lives here, in plain sentences, so it can be read, changed per
project and versioned. BRIEF rules come from the Atreyus brief. MAPPING rules say which of our types to use where
the plan's legend has a row our type list lacks; they were written after reading the Escondido legends.
"""
from __future__ import annotations

VERSION = 1

BRIEF = [
    "A combined fixture (fan with light, smoke with CO) counts once, under its combined type.",
    "Switch types follow the letters printed next to the switch symbol, as the legend defines them.",
    "Rooms are the room labels printed on the plan; devices outside the building are in room Exterior.",
    "Kitchen and Dining are separate rooms when the plan labels them separately.",
]

MAPPING = [
    "A receptacle labelled with both 240V and GFI or WP is receptacle_240v.",
    "A receptacle labelled only with a fan-coil or equipment tag (FC) is receptacle.",
    "A half-filled duplex receptacle is receptacle_half_hot.",
]


def text() -> str:
    """The policy as it is appended to a detection prompt."""
    return "Counting policy for this project:\n" + "\n".join(f"- {r}" for r in BRIEF + MAPPING)
