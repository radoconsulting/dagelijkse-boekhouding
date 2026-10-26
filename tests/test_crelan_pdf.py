"""De vaste lezer voor Crelan- en AXA Bank-uittreksels (zonder AI), en het begrenzen van AI-oproepen."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from conftest import lees
from test_llm import ANTWOORD, NepServer, json_antwoord, met

from boekhouding import categoriseren, db
from boekhouding.importers import raad_pdf_soort
from boekhouding.importers.crelan_pdf import lees_crelan_pdf
from boekhouding.importers.pdf import lees_pdf_afschrift
from boekhouding.llm import AIFout, CategorieVoorstellen, aanbieders, vraag_json
from boekhouding.pipeline import categoriseer_nieuwe_met_ai, importeer_bestand
from boekhouding.utils import iban_geldig, vind_iban

from fixtures.maak_voorbeelden import CRELAN_NIEUW


def test_crelan_zonder_ai_over_jaarwissel_en_paginas(inst):
    assert not inst.ai_actief("documenten")  # bewijs dat er geen AI nodig is
    a = lees_pdf_afschrift(lees("crelan_uittreksel.pdf"), "123456789012_2025_1.pdf", inst)
    assert (a.bron, a.bank, a.saldo_controle) == ("crelan_pdf", "Crelan", "ok")
    t = a.transacties
    assert len(t) == 4  # de 'verrichting' in de berichten na het nieuwe saldo telt niet mee
    assert [x.boekingsdatum for x in t] == [date(2024, 12, 30), date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 3)]
    assert t[1].valutadatum == date(2024, 12, 31)
    assert [x.bedrag for x in t] == [Decimal("-42.54"), Decimal("2500.00"), Decimal("-27.00"), Decimal("-79.99")]
    assert (t[0].tegenpartij_naam, t[0].rekening_iban) == ("COLRUYT MECHELEN-NOO", "BE68539007547034")
    assert (t[1].tegenpartij_naam, t[1].tegenpartij_iban, t[1].mededeling) == ("Werkgever BV", "BE71096123456769", "Loon december")
    assert (t[2].tegenpartij_naam, t[2].tegenpartij_iban) == ("SALON VOORBEELD BV", "NL91ABNA0417164300")
    assert (t[3].tegenpartij_naam, t[3].gestructureerde_mededeling) == ("PROXIMUS", "+++090/9337/55493+++")
    assert t[-1].saldo_na == Decimal("3350.47")
    assert t[0].bank_referentie == "1/2025-101"


def test_axa_bank_oude_layout(inst):
    a = lees_pdf_afschrift(lees("axa_uittreksel.pdf"), "123456789012_2024_0001.pdf", inst)
    assert a.saldo_controle == "ok"
    t = a.transacties
    # De Maestro-betaling van 31/12 op het eerste uittreksel van januari hoort bij het vorige jaar.
    assert [x.boekingsdatum for x in t] == [date(2024, 1, 2), date(2023, 12, 31), date(2024, 1, 3), date(2024, 1, 3)]
    assert (t[0].tegenpartij_naam, t[0].tegenpartij_iban, t[0].tegenpartij_bic) == (
        "FONS (VUTG) - GROEIPAKKET", "BE71096123456769", "GKCCBEBB")
    assert t[0].omschrijving == "Overschrijving in euro van rekening"
    # Rekeningnummer en BIC lopen over twee regels.
    assert (t[2].tegenpartij_iban, t[2].tegenpartij_bic, t[2].tegenpartij_naam, t[2].mededeling) == (
        "NL91ABNA0417164300", "ABNANL2A", "Jan de Vries", "Huur januari")
    assert t[1].tegenpartij_naam == "HEMA MECHELEN"
    assert t[3].tegenpartij_naam == "MC DONALDS LIER"


def test_herkenning_als_afschrift():
    assert raad_pdf_soort(lees("crelan_uittreksel.pdf"), "123456789012_2025_1.pdf") == "afschrift"
    assert raad_pdf_soort(lees("axa_uittreksel.pdf"), "123456789012_2024_0001.pdf") == "afschrift"


def test_saldo_sluit_niet_zonder_ai_geeft_toch_resultaat(inst):
    paginas = ["\n".join(p) for p in CRELAN_NIEUW]
    paginas[1] = paginas[1].replace("3.350,47", "3.350,48")
    a = lees_crelan_pdf(paginas)
    assert a.saldo_controle.startswith("afwijking van -0.01")
    assert all(t.saldo_na is None for t in a.transacties)


def test_ander_document_is_geen_crelan():
    assert lees_crelan_pdf(["Rekeninguittreksel KBC\nVorig saldo 100,00"]) is None
    assert lees_crelan_pdf([]) is None


def test_import_via_pijplijn(con, inst):
    v = importeer_bestand(con, "123456789012_2025_1.pdf", lees("crelan_uittreksel.pdf"), inst=inst)
    assert (v.fout, v.nieuw, v.saldo_controle, v.bron) == (None, 4, "ok", "crelan_pdf")
    assert con.execute("SELECT bank FROM rekeningen").fetchall() == [("Crelan",)]
    # Hetzelfde afschrift onder een andere naam: alle verrichtingen zijn al gekend.
    v2 = importeer_bestand(con, "kopie.pdf", lees("crelan_uittreksel.pdf"), inst=inst, forceer=True)
    assert (v2.nieuw, v2.dubbel) == (0, 4)


def test_vind_iban_met_naam_in_hoofdletters_erachter():
    assert vind_iban("Naar NL91ABNA0417164300 SALON VOORBEELD BV") == "NL91ABNA0417164300"
    assert vind_iban("Van JANSSENS AN BE71 0961 2345 6769") == "BE71096123456769"
    assert iban_geldig("BE71096123456769") and not iban_geldig("BE71096123456768")


# --- AI begrenzen ------------------------------------------------------------------------------


@pytest.fixture
def server(monkeypatch):
    def maak(*antwoorden):
        s = NepServer(antwoorden)
        monkeypatch.setattr(aanbieders, "http_handler", s)
        return s

    return maak


def test_gemini_laag_denkniveau_en_tokengrens(server, inst):
    import json

    s = server(json_antwoord({
        "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(ANTWOORD)}]}, "finishReason": "STOP"}],
    }))
    vraag_json(met(inst, "gemini", "gemini-3.8-flash"), "documenten", CategorieVoorstellen, "x")
    gen = s.body()["generationConfig"]
    denken = gen["thinkingConfig"]
    assert (denken.get("thinkingLevel") or denken.get("thinking_level")) == "LOW"  # proto-JSON aanvaardt beide
    assert gen["maxOutputTokens"] == 32768


def test_gemini_afgekapt_antwoord_wordt_niet_herhaald(server, inst, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = server(json_antwoord({
        "candidates": [{"content": {"role": "model", "parts": [{"text": '{"voorstellen": [{"id": 0, "categ'}]},
                        "finishReason": "MAX_TOKENS"}],
    }))
    with pytest.raises(AIFout, match="te lang"):
        vraag_json(met(inst, "gemini", "gemini-3.8-flash"), "documenten", CategorieVoorstellen, "x")
    assert len(s.verzoeken) == 1


def test_timeout_wordt_niet_herhaald(server, inst, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def traag(request):
        raise httpx.ReadTimeout("timed out", request=request)

    s = server(traag, traag, traag)
    with pytest.raises(AIFout, match="(?i)geen antwoord .*binnen 180 seconden"):
        vraag_json(met(inst, "mistral", "mistral-small-latest"), "categorisatie", CategorieVoorstellen, "x")
    assert len(s.verzoeken) == 1


def test_wachttijd_uit_limietmelding(server, inst, monkeypatch):
    gewacht = []
    monkeypatch.setattr("time.sleep", gewacht.append)
    s = server(
        json_antwoord({"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED",
                                 "details": [{"retryDelay": "21s"}]}}, 429),
        json_antwoord({"candidates": [{"content": {"role": "model", "parts": [{"text": '{"voorstellen": []}'}]},
                                       "finishReason": "STOP"}]}),
    )
    vraag_json(met(inst, "gemini", "gemini-3.5-flash-lite"), "categorisatie", CategorieVoorstellen, "x")
    assert len(s.verzoeken) == 2 and gewacht == [22.0]


def test_ai_categorisatie_een_keer_voor_een_reeks(con, inst, monkeypatch):
    oproepen = []

    def nep(inst_, taak, schema, opdracht, **kw):
        oproepen.append(opdracht.count('"id"'))
        return CategorieVoorstellen(voorstellen=[])

    monkeypatch.setattr(categoriseren, "vraag_json", nep)
    monkeypatch.setattr(type(inst), "ai_actief", lambda self, taak: True)
    v1 = importeer_bestand(con, "a.pdf", lees("crelan_uittreksel.pdf"), inst=inst, gebruik_ai=False)
    v2 = importeer_bestand(con, "b.pdf", lees("axa_uittreksel.pdf"), inst=inst, gebruik_ai=False)
    assert oproepen == []
    categoriseer_nieuwe_met_ai(con, inst, [v1, v2])
    zonder = con.execute("SELECT count(*) FROM transacties WHERE categorie_id IS NULL").fetchall()[0][0]
    assert len(oproepen) == 1 and oproepen[0] == zonder


def test_migratie_v4_breidt_standaardregels_uit():
    con = db.verbind(":memory:")
    con.execute("UPDATE regels SET patroon = ? WHERE naam = 'Kredietkaartafrekening'", [db._KAART_V3])
    con.execute("UPDATE instellingen SET waarde = '3' WHERE sleutel = 'schema_versie'")
    db.initialiseer(con)
    (patroon,) = con.execute("SELECT patroon FROM regels WHERE naam = 'Kredietkaartafrekening'").fetchall()[0]
    assert patroon == db.REGEL_KAARTAFREKENING
    assert con.execute("SELECT waarde FROM instellingen WHERE sleutel = 'schema_versie'").fetchall() == [("4",)]
