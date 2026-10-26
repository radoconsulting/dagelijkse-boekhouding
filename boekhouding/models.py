"""Gemeenschappelijke datastructuren voor alle importers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class Transactie:
    rekening_iban: str | None
    boekingsdatum: date
    bedrag: Decimal
    valutadatum: date | None = None
    munt: str = "EUR"
    tegenpartij_naam: str | None = None
    tegenpartij_iban: str | None = None
    tegenpartij_bic: str | None = None
    omschrijving: str | None = None
    mededeling: str | None = None
    gestructureerde_mededeling: str | None = None
    bank_referentie: str | None = None
    saldo_na: Decimal | None = None
    ruw: dict = field(default_factory=dict)


@dataclass
class Afschrift:
    """Resultaat van het inlezen van één bestand met verrichtingen."""

    bron: str  # kbc_csv | belfius_csv | crelan_csv | coda | pdf_afschrift
    bank: str | None
    transacties: list[Transactie]
    saldo_controle: str = "n.v.t."
    waarschuwingen: list[str] = field(default_factory=list)
    # Gekend saldo per rekening op een datum, als het bestand dat apart vermeldt (bv. Belfius 'Laatste saldo').
    gekende_saldi: dict = field(default_factory=dict)  # iban -> (Decimal saldo, date datum)

    @property
    def rekeningen(self) -> set[str]:
        return {t.rekening_iban for t in self.transacties if t.rekening_iban}


class ImportFout(Exception):
    """Een bestand kon niet ingelezen worden; de boodschap is bedoeld voor de gebruiker."""


class RekeningOntbreekt(ImportFout):
    """Het bestand bevat geen eigen rekeningnummer; de gebruiker moet er een kiezen."""
