"""Actuele saldi per rekening.

Bronnen, in volgorde van voorkeur:
1. het lopende saldo uit het bankbestand (KBC- en Crelan-CSV, CODA, PDF-afschrift dat sluit), aangevuld met
   wat daarna nog geboekt werd;
2. een startsaldo dat je zelf invult (pagina Categorieën & regels → Rekeningen), plus alle latere verrichtingen;
3. anders is het saldo onbekend.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import duckdb
import pandas as pd


def _einde_dag(rijen: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    """Saldo op het einde van een dag uit (bedrag, saldo_na)-paren in onbekende volgorde.
    Het eindsaldo is het saldo_na dat niet het 'saldo voor' van een andere verrichting is."""
    na = [s for _, s in rijen]
    voor = {s - b for b, s in rijen}
    eind = [s for s in na if s not in voor]
    if len(eind) == 1:
        return eind[0]
    return max(na) if na else None  # keten niet eenduidig: beste benadering


def huidig_saldo(con: duckdb.DuckDBPyConnection, op: date | None = None) -> pd.DataFrame:
    op = op or date.today()
    rekeningen = con.execute(
        """SELECT iban, coalesce(naam, bank || ' ' || right(iban, 4)) AS naam, coalesce(soort, 'zicht') AS soort,
                  startsaldo, startsaldo_datum FROM rekeningen ORDER BY soort, naam"""
    ).fetchall()
    uit = []
    for iban, naam, soort, startsaldo, startdatum in rekeningen:
        laatste = con.execute(
            "SELECT max(boekingsdatum) FROM transacties WHERE rekening_iban = ? AND saldo_na IS NOT NULL AND boekingsdatum <= ?",
            [iban, op],
        ).fetchall()[0][0]
        saldo, sinds, bron = None, None, "onbekend"
        if laatste is not None:
            rijen = con.execute(
                "SELECT bedrag, saldo_na FROM transacties WHERE rekening_iban = ? AND boekingsdatum = ? AND saldo_na IS NOT NULL",
                [iban, laatste],
            ).fetchall()
            saldo, sinds, bron = _einde_dag(rijen), laatste, "bankbestand"
        elif startsaldo is not None and startdatum is not None:
            saldo, sinds, bron = Decimal(startsaldo), startdatum, "startsaldo"
        if saldo is not None:
            (later,) = con.execute(
                "SELECT coalesce(sum(bedrag), 0) FROM transacties WHERE rekening_iban = ? AND boekingsdatum > ? AND boekingsdatum <= ?",
                [iban, sinds, op],
            ).fetchall()[0]
            saldo = Decimal(saldo) + Decimal(later)
        uit.append({"iban": iban, "naam": naam, "soort": soort, "saldo": float(saldo) if saldo is not None else None,
                    "bron": bron, "sinds": sinds})
    return pd.DataFrame(uit, columns=["iban", "naam", "soort", "saldo", "bron", "sinds"])
