from datetime import date

import pytest
from conftest import lees

from boekhouding import kredietkaart, rapporten
from boekhouding.importers.pdf import raad_pdf_soort
from boekhouding.llm import KaartAfrekening, KaartVerrichting
from boekhouding.pipeline import importeer_bestand

KOP = (
    "Rekeningnummer;Rubrieknaam;Naam;Munt;Afschriftnummer;Datum;Omschrijving;Valuta;Bedrag;Saldo;credit;debet;"
    "rekeningnummer tegenpartij;BIC tegenpartij;Naam tegenpartij;Adres tegenpartij;gestructureerde mededeling;Vrije mededeling"
)
AFREKENING_CSV = (
    KOP + "\nBE12 7340 0000 0001;Gezinsrekening;GEZIN;EUR;2026044;05/11/2026;AFREKENING KREDIETKAART MASTERCARD 5412XXXXXXXX1234;"
    "05/11/2026;-152,30;1847,70;;-152,30;;;;;;\n"
).encode("cp1252")


def nep_afrekening(totaal=152.30):
    def v(datum, handelaar, bedrag, land="BE", munt=None, orig=None):
        return KaartVerrichting(datum=datum, boekingsdatum=None, handelaar=handelaar, bedrag=bedrag,
                                origineel_bedrag=orig, originele_munt=munt, land=land)

    return KaartAfrekening(
        bank="KBC", kaartnaam="KBC Mastercard", kaart_laatste_cijfers="5412 XXXX XXXX 1234",
        periode_van="2026-10-01", periode_tot="2026-10-31", afrekeningsdatum="2026-11-05", totaal_te_betalen=totaal,
        debet_rekening_iban="BE12 7340 0000 0001",
        verrichtingen=[
            v("2026-10-03", "COLRUYT MECHELEN", -45.10),
            v("2026-10-09", "NETFLIX.COM", -13.99, land="NL"),
            v("2026-10-14", "RYANAIR", -103.59, land="IE", munt="GBP", orig=-89.0),
            v("2026-10-20", "COLRUYT MECHELEN terugbetaling", 10.38),
        ],
    )


@pytest.fixture
def ai_kaart(monkeypatch):
    def zet(afrekening):
        monkeypatch.setattr(kredietkaart, "vraag_json", lambda *a, **k: afrekening)
    zet(nep_afrekening())
    return zet


def _importeer_kaart(con, inst):
    return importeer_bestand(con, "uitgavenstaat_okt.pdf", lees("factuur_kinderopvang.pdf"), inst=inst)


def test_herkenning_op_bestandsnaam():
    assert raad_pdf_soort(b"", "Mastercard_uitgavenstaat_2026-10.pdf") == "kredietkaart"


def test_afrekening_wordt_overboeking_en_aankopen_tellen(con, inst_gemini, ai_kaart):
    v = importeer_bestand(con, "kbc.csv", AFREKENING_CSV, inst=inst_gemini)
    assert v.gelukt
    # Zonder kaartafrekening telt het totaal als uitgave 'Kredietkaart (zonder detail)'.
    assert con.execute("SELECT categorie_id FROM transacties WHERE bedrag = -152.30").fetchall()[0][0] == "diversen_kredietkaart_zonder_detail"

    v = _importeer_kaart(con, inst_gemini)
    assert v.gelukt and v.soort == "kredietkaart" and v.saldo_controle == "ok"
    assert v.kaartafrekeningen_gekoppeld == 1

    bank = con.execute("SELECT categorie_id, categorie_bron FROM transacties WHERE bedrag = -152.30").fetchall()[0]
    assert bank == ("overboekingen_eigen_rekeningen", "kaart")
    kaart = con.execute("SELECT iban, soort, naam FROM rekeningen WHERE soort = 'kredietkaart'").fetchall()
    assert kaart == [("KAART-KBC-1234", "kredietkaart", "KBC Mastercard ****1234")]

    cats = dict(con.execute("SELECT tegenpartij_naam, categorie_id FROM transacties WHERE rekening_iban = 'KAART-KBC-1234'").fetchall())
    assert cats["NETFLIX.COM"] == "vrije_tijd_abonnementen_en_streaming"
    assert cats["RYANAIR"] == "vrije_tijd_reizen_en_vakantie"
    assert cats["Afrekening via zichtrekening"] == "overboekingen_eigen_rekeningen"

    k = rapporten.kerncijfers(con, date(2026, 10, 1), date(2026, 11, 30))
    assert k["uitgaven"] == pytest.approx(152.30)  # de aankopen, niet nog eens de afrekening


def test_omgekeerde_volgorde_en_dubbel_inlezen(con, inst_gemini, ai_kaart):
    _importeer_kaart(con, inst_gemini)
    v = importeer_bestand(con, "kbc.csv", AFREKENING_CSV, inst=inst_gemini)
    assert v.kaartafrekeningen_gekoppeld == 1
    # Dezelfde afrekening als ander bestand opnieuw: geen dubbele aankopen.
    v = importeer_bestand(con, "kopie.pdf", lees("factuur_kinderopvang.pdf") + b"\n%kopie", pdf_soort="kredietkaart", inst=inst_gemini)
    assert v.nieuw == 0 and v.dubbel == 5


def test_afwijking_in_totaal(con, inst_gemini, ai_kaart):
    ai_kaart(nep_afrekening(totaal=180.00))
    v = _importeer_kaart(con, inst_gemini)
    assert v.saldo_controle.startswith("afwijking")


def test_handmatig_koppelen(con, inst_gemini, ai_kaart):
    importeer_bestand(con, "kbc.csv", AFREKENING_CSV.replace(b"AFREKENING KREDIETKAART MASTERCARD", b"DOMICILIERING"), inst=inst_gemini)
    con.execute("UPDATE transacties SET categorie_bron = 'handmatig' WHERE bedrag = -152.30")
    v = _importeer_kaart(con, inst_gemini)
    assert v.kaartafrekeningen_gekoppeld == 0  # handmatige keuze blijft staan
    afr = con.execute("SELECT afrekening_id FROM kaartafrekeningen").fetchall()[0][0]
    kandidaten = kredietkaart.kandidaten_voor(con, afr)
    assert float(kandidaten[0][2]) == -152.30
    kredietkaart.koppel_handmatig(con, afr, kandidaten[0][0])
    assert con.execute("SELECT categorie_bron FROM transacties WHERE bedrag = -152.30").fetchall()[0][0] == "kaart"


def test_zonder_ai_duidelijke_fout(con, inst):
    v = importeer_bestand(con, "uitgavenstaat.pdf", lees("factuur_kinderopvang.pdf"), pdf_soort="kredietkaart", inst=inst)
    assert not v.gelukt and "AI-modellen" in v.fout
