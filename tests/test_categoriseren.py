import json
from decimal import Decimal

from conftest import lees

from boekhouding import categoriseren, llm
from boekhouding.categoriseren import Regel, categoriseer_met_ai, maak_regel, pas_regels_toe, stel_regels_voor
from boekhouding.importers import pdf as pdf_mod
from boekhouding.pipeline import importeer_bestand


def _cat(con, filter_sql: str):
    return con.execute(f"SELECT categorie_id, categorie_bron FROM transacties WHERE {filter_sql}").fetchall()[0]


def test_standaardregels(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    assert _cat(con, "bedrag = -87.45") == ("boodschappen_supermarkt", "regel")  # Colruyt in omschrijving
    assert _cat(con, "bedrag = 174.52") == ("inkomsten_kindergeld_groeipakket", "regel")  # FONS, teken in
    assert _cat(con, "bedrag = -13.99") == ("vrije_tijd_abonnementen_en_streaming", "regel")
    assert _cat(con, "bedrag = -25.00") == (None, None)


def test_teken_en_bedrag():
    r = Regel(None, "mutualiteit", "x", teken="in", max_bedrag=Decimal("100"))
    assert r.past({"bedrag": 20, "omschrijving": "Terugbetaling Mutualiteit"})
    assert not r.past({"bedrag": -20, "omschrijving": "Mutualiteit"})
    assert not r.past({"bedrag": 250, "omschrijving": "Mutualiteit"})


def test_accentongevoelig():
    assert Regel(None, "cafe", "x").past({"bedrag": -5, "tegenpartij_naam": "Café Den Hoek"})


def test_handmatig_wordt_niet_overschreven(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    con.execute("UPDATE transacties SET categorie_id = 'diversen_overige_uitgaven', categorie_bron = 'handmatig' WHERE bedrag = -87.45")
    pas_regels_toe(con)
    assert _cat(con, "bedrag = -87.45") == ("diversen_overige_uitgaven", "handmatig")


def test_prioriteit_en_herberekening(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    maak_regel(con, "colruyt", "vrije_tijd_hobby", prioriteit=1)
    pas_regels_toe(con)
    assert _cat(con, "bedrag = -87.45") == ("vrije_tijd_hobby", "regel")
    con.execute("UPDATE regels SET actief = FALSE WHERE prioriteit = 1")
    pas_regels_toe(con)
    assert _cat(con, "bedrag = -87.45") == ("boodschappen_supermarkt", "regel")


def test_regelvoorstel_uit_handmatige_keuzes(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    con.execute("UPDATE transacties SET categorie_id = 'kinderen_jeugdbeweging_en_sport', categorie_bron = 'handmatig' WHERE bedrag = -25")
    v = stel_regels_voor(con)
    assert len(v) == 1 and v.iloc[0]["veld"] == "tegenpartij_iban"
    maak_regel(con, v.iloc[0]["patroon"], v.iloc[0]["categorie_id"], veld=v.iloc[0]["veld"])
    assert stel_regels_voor(con).empty


def test_gemini_categorisatie(con, inst_gemini, monkeypatch):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst_gemini)
    gezien = {}

    def nep(inst, model, schema, opdracht, **kw):
        gezien["opdracht"] = opdracht
        regels = [json.loads(r) for r in opdracht.splitlines() if r.startswith("{")]
        speelplein = next(r["id"] for r in regels if "Speelpleintjes" in r["tegenpartij"])
        ander = next(r["id"] for r in regels if r["id"] != speelplein)
        return llm.CategorieVoorstellen(voorstellen=[
            llm.CategorieVoorstel(id=speelplein, categorie_id="kinderen_jeugdbeweging_en_sport", zekerheid=0.8),
            llm.CategorieVoorstel(id=ander, categorie_id="bestaat_niet", zekerheid=0.9),
        ])

    monkeypatch.setattr(categoriseren, "vraag_json", nep)
    assert categoriseer_met_ai(con, inst_gemini) == 1
    assert _cat(con, "bedrag = -25.00") == ("kinderen_jeugdbeweging_en_sport", "ai")
    assert "VZW De Speelpleintjes" in gezien["opdracht"]
    assert "BE77001234567842" not in gezien["opdracht"]  # rekeningnummers gaan niet mee naar de AI
    pas_regels_toe(con)  # een AI-voorstel blijft staan zolang geen regel past
    assert _cat(con, "bedrag = -25.00")[1] == "ai"


def test_pdf_afschrift_via_gemini(con, inst_gemini, monkeypatch):
    def nep(inst, model, schema, opdracht, **kw):
        return llm.PdfAfschrift(
            bank="Belfius", rekening_iban="BE34 0680 0000 0002", afschriftnummer="2026/11", beginsaldo=100.0, eindsaldo=40.0,
            verrichtingen=[
                llm.PdfVerrichting(boekingsdatum="2026-11-02", valutadatum=None, bedrag=-54.2, tegenpartij_naam="Delhaize",
                                   tegenpartij_iban=None, omschrijving="Betaling debetkaart", mededeling=None, gestructureerde_mededeling=None),
                llm.PdfVerrichting(boekingsdatum="2026-11-03", valutadatum=None, bedrag=-5.8, tegenpartij_naam="Bakkerij Maes",
                                   tegenpartij_iban=None, omschrijving=None, mededeling=None, gestructureerde_mededeling=None),
            ],
        )

    monkeypatch.setattr(pdf_mod, "vraag_json", nep)
    v = importeer_bestand(con, "uittreksel.pdf", lees("factuur_kinderopvang.pdf"), pdf_soort="afschrift", inst=inst_gemini)
    assert v.gelukt and v.nieuw == 2 and v.saldo_controle == "ok"
    assert _cat(con, "bedrag = -54.20") == ("boodschappen_supermarkt", "regel")


def test_pdf_afschrift_saldo_afwijking(con, inst_gemini, monkeypatch):
    def nep(inst, model, schema, opdracht, **kw):
        return llm.PdfAfschrift(
            bank=None, rekening_iban="BE34068000000002", afschriftnummer=None, beginsaldo=100.0, eindsaldo=50.0,
            verrichtingen=[llm.PdfVerrichting(boekingsdatum="2026-11-02", valutadatum=None, bedrag=-54.2, tegenpartij_naam="X",
                                              tegenpartij_iban=None, omschrijving=None, mededeling=None, gestructureerde_mededeling=None)],
        )

    monkeypatch.setattr(pdf_mod, "vraag_json", nep)
    v = importeer_bestand(con, "uittreksel.pdf", lees("factuur_kinderopvang.pdf"), pdf_soort="afschrift", inst=inst_gemini)
    assert v.saldo_controle.startswith("afwijking")
