"""Crelan-rekeningafschriften in PDF, zonder AI.

Crelan maakt PDF's met gewone tekst, dus een vaste lezer is sneller (een fractie van een seconde), gratis en
voorspelbaarder dan een AI-model. Twee lay-outs:

- Crelan: 'Zichtrekening Uittreksels: 12/2025 - Pagina 1/1', met 'Vorig saldo op 03/02/2025 1.000,00 EUR',
  verrichtingen als '101 04/02 Betaling Bancontact 04/02 - 12,50' en 'Tussensaldo' tussen de pagina's.
- AXA Bank (vóór de overstap naar Crelan in 2024): 'comfort2bank zichtrekening ... Rekeninguittreksel 2023 / 2
  - p 1/6', met 'Vorig saldo +500,00' en verrichtingen als '02-01 Overschrijving ... 02-01 +100,00'.

Elk afschrift wordt gecontroleerd: vorig saldo + verrichtingen moet het nieuwe saldo geven. Lukt dat niet, dan
beslist lees_pdf_afschrift() of de AI het nog eens probeert.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..models import Afschrift, Transactie
from ..utils import normaliseer_iban, parse_bedrag, schoon, vind_iban, vind_ogm

BANK = "Crelan"

# --- Crelan (sinds 2024) ------------------------------------------------------------------------
TITEL = re.compile(r"^Zichtrekening Uittreksels: (\d+)/(\d{4}) - Pagina \d+/\d+")
IBAN_KOP = re.compile(r"^([A-Z]{2}\d{2}(?: [0-9A-Z]{4}){2,7}(?: [0-9A-Z]{1,4})?)$")
SALDO = re.compile(r"^(Vorig|Nieuw) saldo op (\d{2})/(\d{2})/(\d{4}) ([+-]?) ?([\d.]+,\d{2}) EUR$")
VERRICHTING = re.compile(r"^(\d+) (\d{2})/(\d{2}) (.+?) (\d{2})/(\d{2}) ([+-]) ?([\d.]+,\d{2})$")
RUIS = re.compile(
    r"^(Nr Datum Beweging Waarde Bedrag|Tussensaldo [+-]? ?[\d.]+,\d{2} EUR|-|- -"
    r"|De op deze rekening gestorte sommen komen in aanmerking.*)$"
)
NAAR_VAN = re.compile(r"^(Naar|Van) (.+)$")
SLEUTEL_WAARDE = re.compile(r"^([A-Za-zÀ-ÿ .]+?) ?: ?(.*)$")
KAART = re.compile(
    r"^(Betaling (Bancontact|Maestro|Visa Debit)|eCommerce|Uw betaling|Aankoop brandstof|Opneming|Geldopneming)", re.I
)

# --- AXA Bank (tot 2024) ------------------------------------------------------------------------
TITEL_AXA = re.compile(r"Rekeninguittreksel (\d{4}) / (\d+) - p \d+/\d+")
IBAN_AXA = re.compile(r"^IBAN ([A-Z]{2}\d{2}[\dA-Z ]+)$")
SALDO_AXA = re.compile(r"^(Vorig|Nieuw) saldo ([+-]) ?([\d.]+,\d{2})$")
VERRICHTING_AXA = re.compile(r"^(\d{2})-(\d{2}) (.+) (\d{2})-(\d{2}) ([+-])([\d.]+,\d{2})$")
STREEPJESCODE = re.compile(r"^\$\$[^$]*\$\$ ?")  # '$$123456789-1$$ ' voor sommige regels
EINDE_KOP_AXA = "verrichting valuta"
VOET_AXA = re.compile(r"maakt deel uit van de Crelan Groep|^TEL \d{2} \d{3}|^KBO Nr")


@dataclass
class _Ruw:
    nr: str | None
    dag: int
    maand: int
    kop: str
    valuta_dag: int
    valuta_maand: int
    bedrag: Decimal
    regels: list[str] = field(default_factory=list)


def is_crelan(paginas: list[str]) -> bool:
    eerste = paginas[0] if paginas else ""
    return bool(TITEL.search(eerste) or ("comfort2bank" in eerste and TITEL_AXA.search(eerste)))


def pdf_paginas(inhoud: bytes) -> list[str]:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(inhoud)) as pdf:
            return [p.extract_text() or "" for p in pdf.pages]
    except Exception:
        return []


def lees_crelan_pdf(paginas: list[str], bestandsnaam: str = "") -> Afschrift | None:
    """Afschrift uit de tekst per pagina, of None als het geen (leesbaar) Crelan-afschrift is."""
    if not paginas:
        return None
    if TITEL.search(paginas[0]):
        return _lees_crelan(paginas)
    if "comfort2bank" in paginas[0] and TITEL_AXA.search(paginas[0]):
        return _lees_axa(paginas)
    return None


# --- Crelan ----------------------------------------------------------------------------------


def _lees_crelan(paginas: list[str]) -> Afschrift | None:
    iban = nummer = None
    jaar = None
    vorig = nieuw = None
    nieuw_datum = None
    ruw: list[_Ruw] = []
    klaar = False
    for tekst in paginas:
        regels = [r.strip() for r in tekst.splitlines() if r.strip()]
        # Kop van elke pagina: titel, rekeningnummer, naam + BIC
        if regels and (m := TITEL.match(regels[0])):
            nummer, jaar = f"{m.group(1)}/{m.group(2)}", int(m.group(2))
            regels = regels[1:]
            if regels and (m := IBAN_KOP.match(regels[0])):
                iban = iban or normaliseer_iban(m.group(1))
                regels = regels[1:]
            if regels and re.search(r"BIC: [A-Z0-9]{8,11}$", regels[0]):
                regels = regels[1:]
        for r in regels:
            if m := SALDO.match(r):
                bedrag = parse_bedrag(m.group(6))
                bedrag = -bedrag if m.group(5) == "-" else bedrag
                d = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
                if m.group(1) == "Vorig":
                    vorig = bedrag
                else:
                    nieuw, nieuw_datum, klaar = bedrag, d, True
                    break
                continue
            if m := VERRICHTING.match(r):
                bedrag = parse_bedrag(m.group(8))
                ruw.append(_Ruw(m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).strip(), int(m.group(5)),
                                int(m.group(6)), -bedrag if m.group(7) == "-" else bedrag))
                continue
            if ruw and not RUIS.match(r):
                ruw[-1].regels.append(r)
        if klaar:
            break
    if not iban or not ruw:
        return None
    einde = nieuw_datum or date(jaar or date.today().year, 12, 31)
    transacties = []
    for v in ruw:
        boek = _datum_voor(v.dag, v.maand, einde)
        if boek is None:
            return None
        t = _crelan_transactie(v, iban, boek)
        t.bank_referentie = f"{nummer}-{v.nr}" if nummer else v.nr
        transacties.append(t)
    return _afschrift(transacties, vorig, nieuw, nummer)


def _crelan_transactie(v: _Ruw, iban: str, boek: date) -> Transactie:
    regels = list(v.regels)
    naam = tegen_iban = None
    mededeling: list[str] = []
    paren = {}
    for r in regels:
        if m := SLEUTEL_WAARDE.match(r):
            paren[m.group(1).strip().lower()] = m.group(2).strip()
    nv = next((i for i, r in enumerate(regels) if NAAR_VAN.match(r)), None)
    if nv is not None:
        richting, rest = NAAR_VAN.match(regels[nv]).groups()
        tegen_iban = vind_iban(rest)
        naam = rest
        if tegen_iban:
            # Het IBAN staat voor of na de naam, met of zonder spaties.
            patroon = r"\s?".join(re.escape(c) for c in tegen_iban)
            naam = re.sub(patroon, " ", rest, count=1, flags=re.I)
        naam = re.sub(r"\s+via Payconiq$", "", re.sub(r"\s+", " ", naam)).strip(" -")
        mededeling = regels[:nv]
        if not mededeling:
            na = regels[nv + 1 :]
            if richting == "Van" and na and re.search(r"\b\d{4}\b", na[0]):
                na = na[1:]  # adresregel van de opdrachtgever
            mededeling = na
    elif "schuldeiser" in paren:
        naam = paren["schuldeiser"]
        mededeling = [re.sub(r"^Mededeling ?: ?", "", r) for r in regels if not re.match(r"^(ID |Adres )?[Ss]chuldeiser ?:", r)]
    elif KAART.match(v.kop) and regels:
        naam = regels[0].rsplit(", ", 1)[0] if ", " in regels[0] else regels[0]
        mededeling = regels[1:]
    else:
        mededeling = regels
    tekst = " ".join(regels)
    return Transactie(
        rekening_iban=iban,
        boekingsdatum=boek,
        valutadatum=_valuta(v.valuta_dag, v.valuta_maand, boek),
        bedrag=v.bedrag,
        tegenpartij_naam=schoon(naam),
        tegenpartij_iban=tegen_iban,
        omschrijving=v.kop,
        mededeling=schoon(" · ".join(mededeling)),
        gestructureerde_mededeling=vind_ogm(tekst),
        ruw={"nr": v.nr, "omschrijving": v.kop, "regels": v.regels},
    )


# --- AXA Bank --------------------------------------------------------------------------------


def _lees_axa(paginas: list[str]) -> Afschrift | None:
    iban = nummer = None
    jaar = None
    vorig = nieuw = None
    ruw: list[_Ruw] = []
    klaar = False
    for tekst in paginas:
        in_kop = True
        for r in (STREEPJESCODE.sub("", r.strip()) for r in tekst.splitlines()):
            if not r:
                continue
            if m := TITEL_AXA.search(r):
                jaar, nummer = int(m.group(1)), f"{m.group(2)}/{m.group(1)}"
                continue
            if m := IBAN_AXA.match(r):
                iban = iban or normaliseer_iban(m.group(1))
                continue
            if m := SALDO_AXA.match(r):
                bedrag = parse_bedrag(m.group(3))
                bedrag = -bedrag if m.group(2) == "-" else bedrag
                if m.group(1) == "Vorig":
                    vorig = bedrag
                    continue
                nieuw, klaar = bedrag, True
                break
            if VOET_AXA.search(r):
                break  # rest van de pagina is voettekst
            if in_kop:
                in_kop = r != EINDE_KOP_AXA
                continue
            if m := VERRICHTING_AXA.match(r):
                bedrag = parse_bedrag(m.group(7))
                ruw.append(_Ruw(None, int(m.group(1)), int(m.group(2)), m.group(3).strip(), int(m.group(4)),
                                int(m.group(5)), -bedrag if m.group(6) == "-" else bedrag))
            elif ruw:
                ruw[-1].regels.append(r)
        if klaar:
            break
    if not iban or not ruw or not jaar:
        return None
    # Datums zonder jaar. Normaal vallen ze allemaal in het jaar van het uittreksel. Loopt een uittreksel over de
    # jaarwissel (bv. een Maestro-betaling van 31/12 op het eerste uittreksel van januari), dan bepaalt de
    # meerderheid welke kant van de jaarwissel bij het jaar van het uittreksel hoort.
    maanden = [v.maand for v in ruw]
    jaren = [jaar] * len(ruw)
    if any(m >= 11 for m in maanden) and any(m <= 2 for m in maanden):
        voorjaar = sum(1 for m in maanden if m <= 6) >= len(maanden) / 2
        jaren = [(jaar - 1 if m > 6 else jaar) if voorjaar else (jaar if m > 6 else jaar + 1) for m in maanden]
    transacties = []
    for v, j in zip(ruw, jaren):
        try:
            boek = date(j, v.maand, v.dag)
        except ValueError:
            return None
        t = _axa_transactie(v, iban, boek)
        t.bank_referentie = nummer
        transacties.append(t)
    return _afschrift(transacties, vorig, nieuw, nummer)


def _axa_transactie(v: _Ruw, iban: str, boek: date) -> Transactie:
    kop, regels = v.kop, list(v.regels)

    def bic_open(k: str) -> bool:
        return "(BIC" in k and ")" not in k.split("(BIC", 1)[1]

    # Een rekeningnummer met BIC loopt soms over op de volgende regel(s).
    for _ in range(2):
        if regels and (bic_open(kop) or re.search(r"rekening [A-Z]{2}\d{2}[\dA-Z ]*$", kop)):
            kop = f"{kop} {regels.pop(0)}"
    naam = tegen_iban = bic = None
    if m := re.search(r"rekening ([A-Z]{2}\d{2}[\dA-Z ]+?)\s*\(BIC:?\s*([A-Z0-9]{8,11})\)", kop):
        tegen_iban, bic = normaliseer_iban(m.group(1)), m.group(2)
    elif m := re.search(r"rekening ([A-Z]{2}\d{2}[\dA-Z ]+?)\s*(?:\(|$)", kop):
        tegen_iban = normaliseer_iban(m.group(1))
    regels = [r for r in regels if r.lower() != "van"]
    mededeling = []
    # 'Overschrijving in euro naar rekening BE.. (BIC: ..) van' -> 'Overschrijving in euro naar rekening'
    omschrijving = re.sub(r"(rekening)\s.*$", r"\1", kop)
    omschrijving = re.sub(r"\s+(-\s*)?(op )?\d{2}-\d{2}-\d{4} om [\d:]+.*$", "", omschrijving)
    omschrijving = re.sub(r"\s+(van|voor)$", "", omschrijving).strip()
    tekst = " ".join(regels)
    if "debetkaart" in kop.lower() and (m := re.search(r"\b(?:bij|te) (.+?)(?: met kaart ?nummer.*)?$", tekst)):
        naam = m.group(1).rsplit(" - ", 1)[0].strip()  # 'MC DONALDS LIER - LIER' -> handelaar zonder gemeente
    elif tegen_iban or kop.endswith(" voor") or "domicili" in kop.lower():
        naam = regels[0] if regels and not regels[0].lower().startswith(("mededeling", "ref")) else None
    for r in regels:
        if m := re.match(r"^(?:Mededeling|Vervolg med\.)\s*:?\s*(.+)$", r):
            mededeling.append(m.group(1))
    if not mededeling and not naam:
        mededeling = regels
    return Transactie(
        rekening_iban=iban,
        boekingsdatum=boek,
        valutadatum=_valuta(v.valuta_dag, v.valuta_maand, boek),
        bedrag=v.bedrag,
        tegenpartij_naam=schoon(naam),
        tegenpartij_iban=tegen_iban,
        tegenpartij_bic=bic,
        omschrijving=omschrijving,
        mededeling=schoon(" · ".join(mededeling)),
        gestructureerde_mededeling=vind_ogm(" ".join([kop, tekst])),
        ruw={"omschrijving": kop, "regels": v.regels},
    )


# --- Gemeenschappelijk --------------------------------------------------------------------------


def _datum_voor(dag: int, maand: int, einde: date) -> date | None:
    """dd/mm zonder jaar: de meest recente datum die niet (veel) na het einde van het afschrift valt."""
    for jaar in (einde.year, einde.year - 1):
        try:
            d = date(jaar, maand, dag)
        except ValueError:
            continue
        if d <= einde + timedelta(days=7):
            return d
    return None


def _valuta(dag: int, maand: int, boek: date) -> date | None:
    for jaar in (boek.year, boek.year - 1, boek.year + 1):
        try:
            d = date(jaar, maand, dag)
        except ValueError:
            continue
        if abs((d - boek).days) <= 180:
            return d
    return None


def _afschrift(transacties: list[Transactie], vorig, nieuw, nummer: str | None) -> Afschrift:
    controle, waarschuwingen = "n.v.t.", []
    if vorig is not None and nieuw is not None:
        verschil = vorig + sum((t.bedrag for t in transacties), Decimal("0")) - nieuw
        if verschil == 0:
            controle = "ok"
            lopend = vorig
            for t in transacties:
                lopend += t.bedrag
                t.saldo_na = lopend
        else:
            controle = f"afwijking van {verschil} t.o.v. het nieuwe saldo — controleer uittreksel {nummer}"
    else:
        waarschuwingen.append("Vorig of nieuw saldo niet gevonden; het saldo kon niet gecontroleerd worden.")
    return Afschrift(bron="crelan_pdf", bank=BANK, transacties=transacties, saldo_controle=controle,
                     waarschuwingen=waarschuwingen)
