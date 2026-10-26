"""CSV-exports van KBC/CBC, Belfius en Crelan.

Elke bank heeft een profiel met kolomnamen (Nederlands en Frans). De kopregel wordt
automatisch gezocht, ook als er inleidende regels boven staan (Belfius heeft er een twaalftal).
Kolommen worden op naam gekoppeld, niet op positie, zodat een extra of verschoven kolom
het inlezen niet breekt.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from ..models import Afschrift, ImportFout, Transactie
from ..utils import (
    formatteer_ogm,
    normaliseer_iban,
    normaliseer_kop,
    parse_bedrag,
    parse_datum,
    schoon,
    vind_ogm,
)


@dataclass(frozen=True)
class CsvProfiel:
    code: str
    bank: str
    kolommen: dict[str, tuple[str, ...]]  # canoniek veld -> mogelijke kolomnamen
    herkenning: tuple[str, ...]  # canonieke velden die aanwezig moeten zijn om het profiel te herkennen
    decimaal: str = ","
    extra: dict[str, tuple[str, ...]] = field(default_factory=dict)  # bijkomende velden voor ruwe data


KBC = CsvProfiel(
    code="kbc_csv",
    bank="KBC",
    kolommen={
        "rekening_iban": ("rekeningnummer", "numero de compte"),
        "rubriek": ("rubrieknaam", "nom de la rubrique"),
        "rekening_naam": ("naam", "nom"),
        "munt": ("munt", "devise"),
        "bank_referentie": ("afschriftnummer", "numero d'extrait", "numero de l'extrait"),
        "boekingsdatum": ("datum", "date"),
        "omschrijving": ("omschrijving", "description"),
        "valutadatum": ("valuta", "valeur", "date valeur"),
        "bedrag": ("bedrag", "montant"),
        "saldo_na": ("saldo", "solde"),
        "tegenpartij_iban": ("rekeningnummer tegenpartij", "numero de compte contrepartie", "compte contrepartie"),
        "tegenpartij_bic": ("bic tegenpartij", "bic contrepartie"),
        "tegenpartij_naam": ("naam tegenpartij", "nom contrepartie", "nom de la contrepartie"),
        "tegenpartij_adres": ("adres tegenpartij", "adresse contrepartie"),
        "gestructureerde_mededeling": ("gestructureerde mededeling", "communication structuree"),
        "mededeling": ("vrije mededeling", "communication libre"),
    },
    herkenning=("rekening_iban", "boekingsdatum", "omschrijving", "bedrag", "saldo_na"),
)

BELFIUS = CsvProfiel(
    code="belfius_csv",
    bank="Belfius",
    kolommen={
        "rekening_iban": ("rekening", "compte"),
        "boekingsdatum": ("boekingsdatum", "date de comptabilisation"),
        "afschrift": ("rekeninguittrekselnummer", "numero d'extrait"),
        "transactienummer": ("transactienummer", "numero de transaction"),
        "tegenpartij_iban": ("rekening tegenpartij", "compte contrepartie"),
        "tegenpartij_naam": ("naam tegenpartij bevat", "nom contrepartie contient", "naam tegenpartij"),
        "tegenpartij_straat": ("straat en nummer", "rue et numero"),
        "tegenpartij_plaats": ("postcode en plaats", "code postal et localite"),
        "omschrijving": ("transactie", "transaction"),
        "valutadatum": ("valutadatum", "date valeur"),
        "bedrag": ("bedrag", "montant"),
        "munt": ("devies", "devise"),
        "tegenpartij_bic": ("bic",),
        "landcode": ("landcode", "code pays"),
        "mededeling": ("mededelingen", "communications", "mededeling"),
    },
    herkenning=("boekingsdatum", "tegenpartij_iban", "tegenpartij_naam", "bedrag", "valutadatum"),
)

CRELAN = CsvProfiel(
    code="crelan_csv",
    bank="Crelan",
    kolommen={
        "boekingsdatum": ("datum", "date"),
        "bedrag": ("bedrag", "montant"),
        "saldo_na": ("saldo na verrichting", "solde apres operation"),
        "munt": ("munt", "devise"),
        "tegenpartij_naam": ("tegenpartij", "contrepartie"),
        "tegenpartij_iban": ("rekening tegenpartij", "compte contrepartie"),
        "omschrijving": ("type verrichting", "type d'operation"),
        "mededeling": ("mededeling", "communication"),
        "rekening_iban": ("rekening opdrachtgever", "compte donneur d'ordre"),
    },
    herkenning=("boekingsdatum", "bedrag", "tegenpartij_naam", "omschrijving", "mededeling"),
    decimaal=".",
)

PROFIELEN = (KBC, BELFIUS, CRELAN)
SCHEIDINGSTEKENS = (";", ",", "\t")


def decodeer(inhoud: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return inhoud.decode(enc)
        except UnicodeDecodeError:
            continue
    return inhoud.decode("utf-8", errors="replace")


def _kolomindex(kop: list[str], profiel: CsvProfiel) -> dict[str, int]:
    genorm = [normaliseer_kop(k) for k in kop]
    index: dict[str, int] = {}
    gebruikt: set[int] = set()
    # Langste aliassen eerst, zodat 'rekening tegenpartij' niet als 'rekening' gekoppeld wordt.
    paren = sorted(
        ((veld, alias) for veld, aliassen in profiel.kolommen.items() for alias in aliassen),
        key=lambda p: -len(p[1]),
    )
    for veld, alias in paren:
        if veld in index:
            continue
        for i, k in enumerate(genorm):
            if i not in gebruikt and k == alias:
                index[veld] = i
                gebruikt.add(i)
                break
    return index


def zoek_profiel(tekst: str, max_regels: int = 40):
    """Geef (profiel, regelnummer van de kop, scheidingsteken, kolomindex) of None."""
    regels = tekst.splitlines()[:max_regels]
    beste = None
    for nr, regel in enumerate(regels):
        for sep in SCHEIDINGSTEKENS:
            if sep not in regel:
                continue
            kop = next(csv.reader([regel], delimiter=sep))
            if len(kop) < 4:
                continue
            for profiel in PROFIELEN:
                idx = _kolomindex(kop, profiel)
                if all(v in idx for v in profiel.herkenning):
                    score = len(idx)
                    if beste is None or score > beste[4]:
                        beste = (profiel, nr, sep, idx, score)
        if beste:
            break
    return beste[:4] if beste else None


def is_bank_csv(inhoud: bytes) -> bool:
    return zoek_profiel(decodeer(inhoud)) is not None


def lees_csv(inhoud: bytes, bestandsnaam: str = "") -> Afschrift:
    tekst = decodeer(inhoud)
    gevonden = zoek_profiel(tekst)
    if not gevonden:
        eerste = " | ".join(tekst.splitlines()[:3])[:300]
        raise ImportFout(
            f"'{bestandsnaam}' is geen herkende CSV-export van KBC/CBC, Belfius of Crelan. "
            f"Begin van het bestand: {eerste}"
        )
    profiel, kop_nr, sep, idx = gevonden
    regels = tekst.splitlines()[kop_nr:]
    lezer = csv.reader(io.StringIO("\n".join(regels)), delimiter=sep)
    kop = next(lezer)

    def waarde(rij: list[str], veld: str) -> str | None:
        i = idx.get(veld)
        return schoon(rij[i]) if i is not None and i < len(rij) else None

    transacties: list[Transactie] = []
    waarschuwingen: list[str] = []
    for regelnr, rij in enumerate(lezer, start=kop_nr + 2):
        if not any(c.strip() for c in rij):
            continue
        datum = parse_datum(waarde(rij, "boekingsdatum"))
        bedrag = parse_bedrag(waarde(rij, "bedrag"), profiel.decimaal)
        if datum is None or bedrag is None:
            waarschuwingen.append(f"Regel {regelnr} overgeslagen (geen geldige datum of bedrag).")
            continue

        mededeling = waarde(rij, "mededeling")
        ogm = formatteer_ogm(waarde(rij, "gestructureerde_mededeling")) or vind_ogm(mededeling)
        tegenpartij_naam = waarde(rij, "tegenpartij_naam")
        bank_ref = waarde(rij, "bank_referentie")
        if profiel is BELFIUS:
            delen = [waarde(rij, "afschrift"), waarde(rij, "transactienummer")]
            bank_ref = "/".join(d for d in delen if d) or None

        transacties.append(
            Transactie(
                rekening_iban=normaliseer_iban(waarde(rij, "rekening_iban")),
                boekingsdatum=datum,
                valutadatum=parse_datum(waarde(rij, "valutadatum")),
                bedrag=bedrag,
                munt=(waarde(rij, "munt") or "EUR").upper(),
                tegenpartij_naam=tegenpartij_naam,
                tegenpartij_iban=normaliseer_iban(waarde(rij, "tegenpartij_iban")),
                tegenpartij_bic=waarde(rij, "tegenpartij_bic"),
                omschrijving=waarde(rij, "omschrijving"),
                mededeling=mededeling,
                gestructureerde_mededeling=ogm,
                bank_referentie=bank_ref,
                saldo_na=parse_bedrag(waarde(rij, "saldo_na"), profiel.decimaal),
                ruw={k: (rij[i] if i < len(rij) else "") for i, k in enumerate(kop)},
            )
        )

    if not transacties:
        raise ImportFout(f"'{bestandsnaam}' bevat geen verrichtingen.")
    gekende_saldi = {}
    if profiel is BELFIUS:
        # De inleidende regels van een Belfius-export vermelden het laatste saldo en het tijdstip ervan.
        saldo = datum = None
        for regel in tekst.splitlines()[:kop_nr]:
            sleutel, _, waarde = regel.partition(";")
            sleutel = normaliseer_kop(sleutel)
            if sleutel == "laatste saldo":
                saldo = parse_bedrag(waarde.split(";")[0], ",")
            elif sleutel.startswith("datum/uur van het laatste saldo"):
                datum = parse_datum(waarde.split(";")[0])
        rekeningen = {t.rekening_iban for t in transacties if t.rekening_iban}
        if saldo is not None and datum is not None and len(rekeningen) == 1:
            gekende_saldi[rekeningen.pop()] = (saldo, datum)
    return Afschrift(
        gekende_saldi=gekende_saldi,
        bron=profiel.code,
        bank=profiel.bank,
        transacties=transacties,
        saldo_controle=controleer_lopend_saldo(transacties),
        waarschuwingen=waarschuwingen,
    )


def controleer_lopend_saldo(transacties: list[Transactie]) -> str:
    """Controleer per rekening of saldo_na consistent is met de bedragen (oplopend of aflopend gesorteerd)."""
    per_rekening: dict[str | None, list[Transactie]] = {}
    for t in transacties:
        per_rekening.setdefault(t.rekening_iban, []).append(t)
    if not any(t.saldo_na is not None for t in transacties):
        return "n.v.t."
    fouten = 0
    paren = 0
    for lijst in per_rekening.values():
        met_saldo = [t for t in lijst if t.saldo_na is not None]
        if len(met_saldo) < 2:
            continue
        oplopend = sum(1 for a, b in zip(met_saldo, met_saldo[1:]) if a.saldo_na + b.bedrag == b.saldo_na)
        aflopend = sum(1 for a, b in zip(met_saldo, met_saldo[1:]) if b.saldo_na + a.bedrag == a.saldo_na)
        paren += len(met_saldo) - 1
        fouten += len(met_saldo) - 1 - max(oplopend, aflopend)
    if paren == 0:
        return "n.v.t."
    return "ok" if fouten == 0 else f"afwijking: {fouten} van {paren} saldo-overgangen kloppen niet"
