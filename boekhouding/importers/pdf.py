"""PDF-rekeningafschriften (via de gekozen AI-aanbieder) en het onderscheid afschrift/factuur."""

from __future__ import annotations

import re
from decimal import Decimal

from ..llm import AIFout, PdfAfschrift, aanbieder, vraag_json
from ..models import Afschrift, ImportFout, Transactie
from ..settings import Instellingen
from ..utils import normaliseer_iban, parse_bedrag, parse_datum, pdf_tekst, schoon, vind_ogm
from .crelan_pdf import is_crelan, lees_crelan_pdf, pdf_paginas

AFSCHRIFT_WOORDEN = (
    "rekeninguittreksel", "uittreksel nr", "rekeningafschrift", "afschrift nr", "vorig saldo",
    "nieuw saldo", "oud saldo", "extrait de compte", "ancien solde", "nouveau solde", "saldo op",
    "zichtrekening", "verrichtingen van",
)
FACTUUR_WOORDEN = (
    "factuur", "facture", "invoice", "btw-nummer", "te betalen", "vervaldatum", "date d'echeance",
    "factuurnummer", "totaal incl", "total ttc", "btw ", "tva ",
)

SYSTEEM_AFSCHRIFT = """Je bent een nauwkeurige boekhoudassistent voor een Belgisch gezin.
Je krijgt een rekeningafschrift (KBC, CBC, Belfius, Crelan of een andere bank).
Extraheer ALLE verrichtingen, ook over meerdere pagina's, in de volgorde van het afschrift.
Regels:
- bedrag: negatief voor geld dat de rekening verlaat (debet, betaling, afhaling), positief voor geld dat binnenkomt.
- boekingsdatum: de uitvoerings- of boekingsdatum als YYYY-MM-DD; valutadatum apart als die vermeld is.
- tegenpartij_naam: de begunstigde of opdrachtgever, of bij kaartbetalingen de handelaar.
- tegenpartij_iban: zonder spaties.
- gestructureerde_mededeling: in de vorm +++123/4567/89012+++ als die er is.
- omschrijving: het type verrichting zoals de bank het vermeldt (bv. 'Betaling Bancontact', 'Overschrijving').
- beginsaldo en eindsaldo: zoals vermeld op het afschrift (vorig/nieuw saldo), anders null.
- Verzin niets: onleesbare of ontbrekende velden worden null."""


KAART_WOORDEN = (
    "kredietkaart", "credit card", "creditcard", "mastercard", "visa", "uitgavenstaat", "carte de credit",
    "carte de crédit", "kaartnummer", "american express",
)


def raad_pdf_soort(inhoud: bytes, bestandsnaam: str = "") -> str:
    """'afschrift', 'kredietkaart' of 'factuur' op basis van de tekst en de bestandsnaam."""
    naam = bestandsnaam.lower()
    if any(w in naam for w in ("kredietkaart", "creditcard", "credit_card", "mastercard", "visa", "uitgavenstaat")):
        return "kredietkaart"
    if any(w in naam for w in ("uittreksel", "afschrift", "extrait", "statement")):
        return "afschrift"
    if any(w in naam for w in ("factuur", "facture", "invoice", "rekening_")):
        return "factuur"
    if is_crelan([pdf_tekst(inhoud, max_paginas=1)]):
        return "afschrift"
    tekst = pdf_tekst(inhoud).lower()
    if sum(1 for w in KAART_WOORDEN if w in tekst) >= 2 and "factuurnummer" not in tekst:
        return "kredietkaart"
    score_afschrift = sum(tekst.count(w) for w in AFSCHRIFT_WOORDEN)
    score_factuur = sum(tekst.count(w) for w in FACTUUR_WOORDEN)
    # Afschriften bevatten veel datums en bedragen per regel.
    score_afschrift += len(re.findall(r"\d{2}[-/.]\d{2}[-/.]\d{2,4}", tekst)) // 15
    return "afschrift" if score_afschrift > score_factuur else "factuur"


def lees_pdf_afschrift(inhoud: bytes, bestandsnaam: str, inst: Instellingen) -> Afschrift:
    """Eerst een vaste lezer (Crelan/AXA Bank, zonder AI); sluit het saldo daarmee niet, dan de AI."""
    vast = lees_crelan_pdf(pdf_paginas(inhoud), bestandsnaam)
    if vast is not None and vast.saldo_controle == "ok":
        return vast
    if vast is not None:
        try:
            met_ai = _lees_met_ai(inhoud, bestandsnaam, inst) if inst.ai_actief("documenten") else None
        except ImportFout as e:
            vast.waarschuwingen.append(f"Nagelezen met AI mislukt: {e}")
            met_ai = None
        if met_ai is not None and met_ai.saldo_controle == "ok":
            return met_ai
        return vast
    return _lees_met_ai(inhoud, bestandsnaam, inst)


def _lees_met_ai(inhoud: bytes, bestandsnaam: str, inst: Instellingen) -> Afschrift:
    if not inst.ai_actief("documenten"):
        keuze = inst.ai("documenten")
        raise ImportFout(
            f"Dit PDF-afschrift is niet van Crelan en moet met AI gelezen worden, maar {aanbieder(keuze.provider).label} is niet ingesteld "
            f"({inst.provider_klaar(keuze.provider)[1]}). Gebruik een CSV- of CODA-export of stel een aanbieder in "
            "op de pagina AI-modellen."
        )
    try:
        resultaat = vraag_json(
            inst,
            "documenten",
            PdfAfschrift,
            "Extraheer het rekeningafschrift in het gevraagde JSON-formaat.",
            bijlage=inhoud,
            mime_type="application/pdf",
            systeem=SYSTEEM_AFSCHRIFT,
            bestandsnaam=bestandsnaam,
        )
    except AIFout as e:
        raise ImportFout(str(e)) from e
    return _naar_afschrift(resultaat, bestandsnaam)


def _naar_afschrift(r: PdfAfschrift, bestandsnaam: str) -> Afschrift:
    iban = normaliseer_iban(r.rekening_iban)
    transacties: list[Transactie] = []
    waarschuwingen: list[str] = []
    for i, v in enumerate(r.verrichtingen, start=1):
        datum = parse_datum(v.boekingsdatum)
        bedrag = parse_bedrag(v.bedrag)
        if datum is None or bedrag is None:
            waarschuwingen.append(f"Verrichting {i} overgeslagen: geen geldige datum of bedrag.")
            continue
        transacties.append(
            Transactie(
                rekening_iban=iban,
                boekingsdatum=datum,
                valutadatum=parse_datum(v.valutadatum),
                bedrag=bedrag,
                tegenpartij_naam=schoon(v.tegenpartij_naam),
                tegenpartij_iban=normaliseer_iban(v.tegenpartij_iban),
                omschrijving=schoon(v.omschrijving),
                mededeling=schoon(v.mededeling),
                gestructureerde_mededeling=vind_ogm(v.gestructureerde_mededeling) or vind_ogm(v.mededeling),
                bank_referentie=r.afschriftnummer,
                ruw=v.model_dump(),
            )
        )
    if not transacties:
        raise ImportFout(f"In '{bestandsnaam}' werden geen verrichtingen gevonden.")

    controle = "n.v.t."
    if r.beginsaldo is not None and r.eindsaldo is not None:
        som = sum((t.bedrag for t in transacties), Decimal("0"))
        verschil = parse_bedrag(r.beginsaldo) + som - parse_bedrag(r.eindsaldo)
        controle = "ok" if verschil == 0 else f"afwijking van {verschil} t.o.v. eindsaldo — controleer dit afschrift"
        if verschil == 0:  # lopend saldo enkel als het afschrift sluit
            lopend = parse_bedrag(r.beginsaldo)
            for t in transacties:
                lopend += t.bedrag
                t.saldo_na = lopend
    return Afschrift(
        bron="pdf_afschrift",
        bank=schoon(r.bank),
        transacties=transacties,
        saldo_controle=controle,
        waarschuwingen=waarschuwingen,
    )
