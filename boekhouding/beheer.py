"""Transacties beheren: nakijken, handmatig categoriseren, tags en splitsen.

- Nakijken: elke nieuw geïmporteerde transactie staat op 'niet nagekeken'. Zelf een categorie kiezen of iets
  afvinken in de nakijklijst zet ze op 'nagekeken'.
- Tags: vrije labels naast de categorie (bv. 'vakantie 2026', 'verbouwing'), handig voor projecten die over
  categorieën heen lopen.
- Splitsen: één transactie verdelen over meerdere categorieën. De delen moeten samen precies het bedrag van de
  transactie geven. Rapporten en budgetten tellen dan de delen (view v_boekingen), niet de transactie zelf.
"""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pandas as pd

from .db import BRON_GESPLITST, BRON_HANDMATIG, een


def _schone_tags(tags) -> list[str]:
    if tags is None or (isinstance(tags, float) and pd.isna(tags)):
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    uit = []
    for t in tags:
        t = " ".join(str(t).split()).lower()
        if t and t not in uit:
            uit.append(t)
    return uit


def markeer_nagekeken(con: duckdb.DuckDBPyConnection, txn_ids: list[str], nagekeken: bool = True) -> int:
    if not txn_ids:
        return 0
    con.execute(
        "UPDATE transacties SET nagekeken = ? WHERE list_contains(?::VARCHAR[], txn_id)", [nagekeken, list(txn_ids)]
    )
    return len(txn_ids)


def zet_categorie(con: duckdb.DuckDBPyConnection, txn_id: str, categorie_id: str | None) -> None:
    """Handmatige keuze: wordt nooit door regels of AI overschreven. Geen categorie = terug vrijgeven."""
    if een(con.execute("SELECT 1 FROM splitsingen WHERE txn_id = ?", [txn_id])):
        raise ValueError("Deze transactie is gesplitst: pas de categorieën van de delen aan.")
    con.execute(
        """UPDATE transacties SET categorie_id = ?, categorie_bron = ?, zekerheid = ?, regel_id = NULL, ai_model = NULL,
                  nagekeken = TRUE, gewijzigd_op = current_timestamp WHERE txn_id = ?""",
        [categorie_id, BRON_HANDMATIG if categorie_id else None, 1.0 if categorie_id else None, txn_id],
    )


def zet_tags(con: duckdb.DuckDBPyConnection, txn_id: str, tags) -> None:
    con.execute("UPDATE transacties SET tags = ?, gewijzigd_op = current_timestamp WHERE txn_id = ?", [_schone_tags(tags), txn_id])


def zet_notitie(con: duckdb.DuckDBPyConnection, txn_id: str, notitie: str | None) -> None:
    con.execute("UPDATE transacties SET notitie = ?, gewijzigd_op = current_timestamp WHERE txn_id = ?", [notitie or None, txn_id])


def alle_tags(con: duckdb.DuckDBPyConnection) -> list[str]:
    rijen = con.execute(
        """SELECT DISTINCT unnest(tags) AS tag FROM (
               SELECT tags FROM transacties UNION ALL SELECT tags FROM splitsingen) WHERE tags IS NOT NULL ORDER BY 1"""
    ).fetchall()
    return [r[0] for r in rijen]


def splitsingen(con: duckdb.DuckDBPyConnection, txn_id: str) -> pd.DataFrame:
    return con.execute(
        """SELECT volgnr, CAST(bedrag AS DOUBLE) AS bedrag, categorie_id, notitie, tags
           FROM splitsingen WHERE txn_id = ? ORDER BY volgnr""",
        [txn_id],
    ).df()


def splits(con: duckdb.DuckDBPyConnection, txn_id: str, delen: list[dict]) -> None:
    """Vervang de splitsing van een transactie. delen: [{'bedrag', 'categorie_id', 'notitie', 'tags'}, ...]"""
    rij = een(con.execute("SELECT bedrag FROM transacties WHERE txn_id = ?", [txn_id]))
    if not rij:
        raise ValueError("Onbekende transactie")
    totaal = Decimal(rij[0])
    schoon = []
    for d in delen:
        bedrag = d.get("bedrag")
        if bedrag is None or (isinstance(bedrag, float) and pd.isna(bedrag)) or Decimal(str(bedrag)) == 0:
            continue
        schoon.append((Decimal(str(bedrag)).quantize(Decimal("0.01")), d.get("categorie_id") or None,
                       d.get("notitie") or None, _schone_tags(d.get("tags"))))
    if len(schoon) < 2:
        raise ValueError("Een splitsing heeft minstens twee delen nodig.")
    som = sum(b for b, *_ in schoon)
    if som != totaal:
        raise ValueError(f"De delen tellen op tot {som:.2f}, maar de transactie is {totaal:.2f} (verschil {totaal - som:.2f}).")
    if any((b > 0) != (totaal > 0) for b, *_ in schoon):
        raise ValueError("Alle delen moeten hetzelfde teken hebben als de transactie.")
    con.execute("DELETE FROM splitsingen WHERE txn_id = ?", [txn_id])
    con.executemany(
        "INSERT INTO splitsingen (txn_id, volgnr, bedrag, categorie_id, notitie, tags) VALUES (?, ?, ?, ?, ?, ?)",
        [(txn_id, i, b, c, n, t) for i, (b, c, n, t) in enumerate(schoon, start=1)],
    )
    con.execute(
        """UPDATE transacties SET categorie_id = NULL, categorie_bron = ?, regel_id = NULL, zekerheid = NULL,
                  nagekeken = TRUE, gewijzigd_op = current_timestamp WHERE txn_id = ?""",
        [BRON_GESPLITST, txn_id],
    )


def verwijder_splitsing(con: duckdb.DuckDBPyConnection, txn_id: str) -> None:
    from .categoriseren import pas_regels_toe

    con.execute("DELETE FROM splitsingen WHERE txn_id = ?", [txn_id])
    con.execute("UPDATE transacties SET categorie_bron = NULL WHERE txn_id = ? AND categorie_bron = ?", [txn_id, BRON_GESPLITST])
    pas_regels_toe(con, txn_ids=[txn_id])


def zet_splitsdeel(con: duckdb.DuckDBPyConnection, txn_id: str, volgnr: int, categorie_id: str | None = None,
                   notitie: str | None = None, tags=None, velden: tuple[str, ...] = ("categorie_id", "notitie", "tags")) -> None:
    """Wijzig één deel van een splitsing (bv. vanuit Excel)."""
    sets, params = [], []
    if "categorie_id" in velden:
        sets.append("categorie_id = ?"); params.append(categorie_id)
    if "notitie" in velden:
        sets.append("notitie = ?"); params.append(notitie or None)
    if "tags" in velden:
        sets.append("tags = ?"); params.append(_schone_tags(tags))
    con.execute(f"UPDATE splitsingen SET {', '.join(sets)} WHERE txn_id = ? AND volgnr = ?", [*params, txn_id, volgnr])
