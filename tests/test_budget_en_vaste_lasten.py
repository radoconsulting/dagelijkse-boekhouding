from datetime import date, timedelta

import pandas as pd
import pytest
from conftest import lees
from hulp import rekening, txn

from boekhouding import budget, saldi, terugkerend
from boekhouding.pipeline import importeer_bestand

SUPER = "boodschappen_supermarkt"
RESTO = "vrije_tijd_restaurants_en_cafes"
VANDAAG = date(2026, 10, 26)


# --- Budgetten ------------------------------------------------------------------------------


def test_budgetstatus_met_terugbetaling_en_drempels(con):
    rekening(con)
    budget.bewaar_budget(con, SUPER, 400)
    budget.bewaar_budget(con, RESTO, 100)
    txn(con, date(2026, 10, 3), -250, cat=SUPER)
    txn(con, date(2026, 10, 10), -100, cat=SUPER)
    txn(con, date(2026, 10, 12), 20, cat=SUPER)  # terugbetaling vermindert de uitgave
    txn(con, date(2026, 10, 15), -120, cat=RESTO)
    df = budget.budget_status(con, 2026, 10, VANDAAG).set_index("categorie_id")
    assert df.loc[SUPER, "besteed"] == pytest.approx(330)
    assert df.loc[SUPER, "status"] == "let op"  # 82,5%
    assert df.loc[RESTO, "status"] == "over"
    assert df.loc[SUPER, "verwacht_aandeel"] == pytest.approx(26 / 31)


def test_meenemen_schuift_door(con):
    rekening(con)
    budget.bewaar_budget(con, RESTO, 100, meenemen=True, vanaf=date(2026, 8, 1))
    txn(con, date(2026, 8, 5), -60, cat=RESTO)    # 40 over
    txn(con, date(2026, 9, 5), -130, cat=RESTO)   # 30 tekort
    df = budget.budget_status(con, 2026, 10, VANDAAG).set_index("categorie_id")
    assert df.loc[RESTO, "meegenomen"] == pytest.approx(10)
    assert df.loc[RESTO, "beschikbaar"] == pytest.approx(110)


def test_nul_verwijdert_budget(con):
    budget.bewaar_budget(con, SUPER, 300)
    budget.bewaar_budget(con, SUPER, 0)
    assert budget.budgetten(con).empty


def test_voorstel_deelt_door_maanden_met_gegevens(con):
    rekening(con)
    for m in (7, 8, 9):  # maar 3 maanden gegevens in een venster van 6
        txn(con, date(2026, m, 10), -102, cat=SUPER)
    v = budget.voorstel(con, vandaag=VANDAAG).set_index("categorie_id")
    assert v.loc[SUPER, "gemiddeld"] == pytest.approx(102)
    assert v.loc[SUPER, "voorstel"] == 105


def test_maandvergelijking(con):
    rekening(con)
    for m, b in ((7, -100), (8, -200), (9, -300), (10, -400)):
        txn(con, date(2026, m, 10), b, cat=SUPER)
    txn(con, date(2025, 10, 10), -50, cat=SUPER)
    v = budget.maandvergelijking(con, 2026, 10).set_index("hoofdcategorie")
    assert v.loc["Boodschappen", "deze_maand"] == 400
    assert v.loc["Boodschappen", "gemiddeld"] == pytest.approx(200)
    assert v.loc["Boodschappen", "vorig_jaar"] == 50


# --- Vaste lasten ----------------------------------------------------------------------------


def _maandelijks(con, naam, bedragen, start=date(2026, 5, 3), **kw):
    for i, b in enumerate(bedragen):
        txn(con, (pd.Timestamp(start) + pd.DateOffset(months=i)).date(), b, naam=naam, **kw)


def test_handelaar_uit_omschrijving():
    assert terugkerend.handelaar("BETALING VIA BANCONTACT 24-10 COLRUYT MECHELEN") == "colruyt mechelen"
    assert terugkerend.handelaar("EUROPESE DOMICILIERING Proximus 2026/10") == "proximus"


def test_detectie_en_prijsstijging(con):
    rekening(con)
    _maandelijks(con, "NETFLIX", [-13.99] * 5 + [-15.99], cat="vrije_tijd_abonnementen_en_streaming")
    _maandelijks(con, "Werkgever", [3000.0] * 6, iban_tegen="BE68539007547034")
    # Boodschappen: onregelmatig ritme en wisselende bedragen -> geen vaste last
    for i, b in enumerate([-45, -112, -23, -87, -150, -64, -33, -98]):
        txn(con, date(2026, 5, 2) + timedelta(days=[0, 3, 11, 12, 30, 31, 47, 70][i]), b, naam="Colruyt")
    # Jaarlijkse verzekering
    txn(con, date(2025, 3, 1), -480, naam="Ethias")
    txn(con, date(2026, 3, 2), -480, naam="Ethias")

    v = terugkerend.detecteer(con, VANDAAG).set_index("naam")
    assert set(v.index) == {"NETFLIX", "Werkgever", "Ethias"}
    assert v.loc["NETFLIX", "frequentie"] == "maandelijks"
    assert v.loc["NETFLIX", "prijswijziging"] == pytest.approx(2 / 13.99, rel=1e-3)
    assert v.loc["NETFLIX", "volgende_datum"] == date(2026, 11, 3)
    assert v.loc["Ethias", "frequentie"] == "jaarlijks"
    assert v.loc["Werkgever", "richting"] == "in"
    assert list(terugkerend.prijsstijgingen(v.reset_index())["naam"]) == ["NETFLIX"]


def test_gestopt_en_verbergen(con):
    rekening(con)
    _maandelijks(con, "Oude sportclub", [-30] * 4, start=date(2026, 1, 5))  # laatste betaling april
    v = terugkerend.detecteer(con, VANDAAG)
    assert not v.set_index("naam").loc["Oude sportclub", "actief"]
    terugkerend.zet_keuze(con, v.iloc[0]["sleutel"], "genegeerd")
    assert terugkerend.detecteer(con, VANDAAG).empty
    assert len(terugkerend.detecteer(con, VANDAAG, met_genegeerd=True)) == 1


# --- Saldi en vooruitzicht -----------------------------------------------------------------------


def test_saldo_uit_bankbestand_en_startsaldo(con):
    rekening(con, "BE11000000000001")
    rekening(con, "BE22000000000002")
    # Twee verrichtingen op dezelfde dag, in onbekende volgorde: het eindsaldo is 900.
    txn(con, date(2026, 10, 20), -100, saldo_na=950)
    txn(con, date(2026, 10, 20), -50, saldo_na=900)
    txn(con, date(2026, 10, 22), -25)  # zonder saldo: wordt bijgeteld
    con.execute("UPDATE rekeningen SET startsaldo = 500, startsaldo_datum = '2026-10-01' WHERE iban = 'BE22000000000002'")
    txn(con, date(2026, 10, 5), 70, rek="BE22000000000002")
    s = saldi.huidig_saldo(con, VANDAAG).set_index("iban")
    assert s.loc["BE11000000000001", "saldo"] == pytest.approx(875)
    assert s.loc["BE22000000000002", "saldo"] == pytest.approx(570)
    assert s.loc["BE22000000000002", "bron"] == "startsaldo"


def test_belfius_laatste_saldo_wordt_gekend_saldo(con, inst):
    importeer_bestand(con, "belfius.csv", lees("belfius_voorbeeld.csv"), inst=inst)
    s = saldi.huidig_saldo(con, VANDAAG).set_index("iban")
    assert s.loc["BE34068000000002", "saldo"] == pytest.approx(1234.56)


def test_vooruitzicht(con):
    rekening(con)
    txn(con, date(2026, 10, 25), -10, saldo_na=1000)
    _maandelijks(con, "Huur", [-800] * 6, start=date(2026, 5, 1))
    _maandelijks(con, "Werkgever", [2500] * 6, start=date(2026, 5, 28))
    vz = terugkerend.vooruitzicht(con, 30, VANDAAG, met_variabel=False)
    assert vz["startsaldo"] == 1000
    assert list(vz["posten"]["naam"]) == ["Werkgever", "Huur"]
    assert vz["reeks"]["saldo"].iloc[-1] == pytest.approx(1000 + 2500 - 800)
    assert vz["laagste"]["saldo"] == pytest.approx(1000)
