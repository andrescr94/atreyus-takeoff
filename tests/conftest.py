"""Tests read the sheets the way `takeoff ingest` writes them (data/sheets/<id>/). data/ is not in the repo, so a
fresh clone ingests the PDF once before the first test."""
from pathlib import Path

import pytest

from takeoff import ingest

PDF = "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"


@pytest.fixture(scope="session", autouse=True)
def ingested_sheets():
    if not Path("data/sheets/A4-111/sheet.json").exists():
        ingest.ingest(PDF)
