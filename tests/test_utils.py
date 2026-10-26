from datetime import date
from decimal import Decimal

import pytest

from boekhouding.utils import formatteer_ogm, normaliseer_iban, ogm_geldig, parse_bedrag, parse_datum, vind_ogm


@pytest.mark.parametrize(
    "tekst, decimaal, verwacht",
    [
        ("-12,50", ",", "-12.50"),
        ("1.234,56", ",", "1234.56"),
        ("1 234,56 EUR", ",", "1234.56"),
        ("-.99", ".", "-0.99"),
        ("1234.5", ".", "1234.50"),
        ("1,234.56", None, "1234.56"),
        ("1.000", ",", "1000.00"),
        ("12,50-", ",", "-12.50"),
        ("€ 4.250,00", ",", "4250.00"),
        ("− 8,60", ",", "-8.60"),
    ],
)
def test_parse_bedrag(tekst, decimaal, verwacht):
    assert parse_bedrag(tekst, decimaal) == Decimal(verwacht)


def test_parse_bedrag_leeg():
    assert parse_bedrag("") is None
    assert parse_bedrag("abc") is None


def test_parse_datum():
    assert parse_datum("24/10/2026") == date(2026, 10, 24)
    assert parse_datum("2026-10-24") == date(2026, 10, 24)
    assert parse_datum("24.10.2026") == date(2026, 10, 24)
    assert parse_datum("niet") is None


def test_ogm():
    assert ogm_geldig("090933755493")
    assert not ogm_geldig("090933755494")
    assert formatteer_ogm("090933755493") == "+++090/9337/55493+++"
    assert vind_ogm("Betaling +++090/9337/55493+++ dank") == "+++090/9337/55493+++"
    assert vind_ogm("***090/9337/55493***") == "+++090/9337/55493+++"
    assert vind_ogm("Factuur 2026/10") is None


def test_iban():
    assert normaliseer_iban("be12 7340-0000 0001") == "BE12734000000001"
    assert normaliseer_iban("") is None
