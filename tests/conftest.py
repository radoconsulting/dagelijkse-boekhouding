from dataclasses import replace
from pathlib import Path

import pytest

from boekhouding import db
from boekhouding.settings import AIKeuze, laad_instellingen

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def con():
    c = db.verbind(":memory:")
    yield c
    c.close()


@pytest.fixture
def inst(tmp_path):
    """Instellingen zonder AI-sleutels (Gemini als aanbieder), met mappen in een tijdelijke map."""
    return replace(
        laad_instellingen(),
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "inbox",
        ai_documenten=AIKeuze("gemini", "gemini-3.8-flash"),
        ai_categorisatie=AIKeuze("gemini", "gemini-3.5-flash-lite"),
        sleutels={},
        azure_endpoint=None,
        compat_base_url=None,
        pdf_als_tekst=False,
    )


@pytest.fixture
def inst_gemini(inst):
    return replace(inst, sleutels={"gemini": "test-sleutel"})


def lees(naam: str) -> bytes:
    return (FIXTURES / naam).read_bytes()
