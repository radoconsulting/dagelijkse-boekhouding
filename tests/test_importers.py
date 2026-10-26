from datetime import date
from decimal import Decimal

import pytest
from conftest import lees

from boekhouding.importers import herken_bestand, lees_coda, lees_csv
from boekhouding.models import ImportFout


def test_kbc_csv():
    a = lees_csv(lees("kbc_voorbeeld.csv"), "kbc.csv")
    assert a.bron == "kbc_csv" and a.bank == "KBC"
    assert len(a.transacties) == 11
    assert a.rekeningen == {"BE12734000000001"}
    assert a.saldo_controle == "ok"
    loon = next(t for t in a.transacties if t.bedrag == Decimal("4250.00"))
    assert loon.tegenpartij_naam == "Voorbeeld Werkgever NV"
    assert loon.tegenpartij_iban == "BE68539007547034"
    assert loon.mededeling == "Loon oktober 2026"
    opvang = next(t for t in a.transacties if t.bedrag == Decimal("-312.50"))
    assert opvang.gestructureerde_mededeling == "+++090/9337/55493+++"


def test_belfius_csv_met_inleidende_regels():
    a = lees_csv(lees("belfius_voorbeeld.csv"), "belfius.csv")
    assert a.bron == "belfius_csv"
    assert len(a.transacties) == 5
    assert a.rekeningen == {"BE34068000000002"}
    cafe = next(t for t in a.transacties if t.bedrag == Decimal("-23.50"))
    assert cafe.tegenpartij_naam == "Café Den Hoek"  # cp1252 correct gedecodeerd
    assert cafe.boekingsdatum == date(2026, 10, 26)


def test_crelan_csv_punt_als_decimaal():
    a = lees_csv(lees("crelan_voorbeeld.csv"), "crelan.csv")
    assert a.bron == "crelan_csv"
    assert [t.bedrag for t in a.transacties] == [Decimal("-21.85"), Decimal("-0.99"), Decimal("-45.00")]
    assert a.saldo_controle == "ok"
    assert a.transacties[2].gestructureerde_mededeling == "+++120/0000/04345+++"


def test_onbekende_csv():
    with pytest.raises(ImportFout):
        lees_csv(b"a,b,c\n1,2,3\n", "x.csv")


def test_coda():
    a = lees_coda(lees("kbc_voorbeeld.cod"), "x.cod")
    assert a.bron == "coda"
    assert len(a.transacties) == 3
    assert a.saldo_controle == "ok"
    telenet = a.transacties[2]
    assert telenet.tegenpartij_naam == "Telenet BV"
    assert telenet.tegenpartij_iban == "BE15000000001234"
    assert telenet.gestructureerde_mededeling == "+++100/0001/23407+++"
    assert telenet.bedrag == Decimal("-36.20")
    assert a.transacties[0].mededeling == "Voorschot elektriciteit en gas"


def test_herken_bestand():
    assert herken_bestand("x.cod", lees("kbc_voorbeeld.cod")) == "coda"
    assert herken_bestand("x.txt", lees("kbc_voorbeeld.cod")) == "coda"
    assert herken_bestand("x.csv", lees("kbc_voorbeeld.csv")) == "csv"
    assert herken_bestand("f.pdf", lees("factuur_kinderopvang.pdf")) == "pdf"
    assert herken_bestand("foto.jpg", b"\xff\xd8") == "afbeelding"
