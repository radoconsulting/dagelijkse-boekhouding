"""Query's voor het dashboard. Alles leest uit de view v_boekingen (gesplitste transacties tellen per deel).

Conventies: inkomsten zijn positief; uitgaven en sparen worden als positief bedrag getoond
(geld dat vertrekt). Terugbetalingen in een uitgavencategorie verminderen die uitgave.
Overboekingen tussen eigen rekeningen (soort 'transfer') tellen nergens mee.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd


def _filter(van: date | None, tot: date | None, rekeningen: list[str] | None, tags: list[str] | None = None) -> tuple[str, list]:
    delen, params = ["1=1"], []
    if tags:
        delen.append("list_has_any(tags, ?::VARCHAR[])")
        params.append(list(tags))
    if van:
        delen.append("boekingsdatum >= ?")
        params.append(van)
    if tot:
        delen.append("boekingsdatum <= ?")
        params.append(tot)
    if rekeningen:
        delen.append("list_contains(?::VARCHAR[], rekening_iban)")
        params.append(list(rekeningen))
    return " AND ".join(delen), params


def kerncijfers(con: duckdb.DuckDBPyConnection, van=None, tot=None, rekeningen=None) -> dict:
    w, p = _filter(van, tot, rekeningen)
    r = con.execute(
        f"""SELECT
                coalesce(sum(bedrag) FILTER (WHERE soort = 'inkomst'), 0),
                coalesce(-sum(bedrag) FILTER (WHERE soort = 'uitgave'), 0),
                coalesce(-sum(bedrag) FILTER (WHERE soort = 'sparen'), 0),
                count(*) FILTER (WHERE categorie_id IS NULL),
                coalesce(sum(abs(bedrag)) FILTER (WHERE categorie_id IS NULL), 0),
                count(*)
            FROM v_boekingen WHERE {w}""",
        p,
    ).fetchall()[0]
    inkomsten, uitgaven, sparen, n_leeg, bedrag_leeg, n = (float(x) if i not in (3, 5) else int(x) for i, x in enumerate(r))
    return {
        "inkomsten": inkomsten,
        "uitgaven": uitgaven,
        "sparen": sparen,
        "netto": inkomsten - uitgaven,
        "spaarquote": (inkomsten - uitgaven) / inkomsten if inkomsten else None,
        "ongecategoriseerd_aantal": n_leeg,
        "ongecategoriseerd_bedrag": bedrag_leeg,
        "aantal": n,
    }


def per_maand(con, van=None, tot=None, rekeningen=None) -> pd.DataFrame:
    w, p = _filter(van, tot, rekeningen)
    return con.execute(
        f"""SELECT maand,
                   CASE soort WHEN 'inkomst' THEN 'Inkomsten' WHEN 'uitgave' THEN 'Uitgaven' ELSE 'Sparen en beleggen' END AS reeks,
                   CAST(CASE WHEN soort = 'inkomst' THEN sum(bedrag) ELSE -sum(bedrag) END AS DOUBLE) AS bedrag
            FROM v_boekingen
            WHERE {w} AND soort IN ('inkomst', 'uitgave', 'sparen')
            GROUP BY maand, soort ORDER BY maand""",
        p,
    ).df()


def per_categorie(con, van=None, tot=None, rekeningen=None, soort: str = "uitgave", niveau: str = "hoofdcategorie") -> pd.DataFrame:
    w, p = _filter(van, tot, rekeningen)
    kolom = "coalesce(hoofdcategorie, 'Niet gecategoriseerd')" if niveau == "hoofdcategorie" else "categorie_label"
    teken = "" if soort == "inkomst" else "-"
    return con.execute(
        f"""SELECT {kolom} AS categorie, CAST({teken}sum(bedrag) AS DOUBLE) AS bedrag, count(*) AS aantal
            FROM v_boekingen WHERE {w} AND soort = ?
            GROUP BY 1 HAVING abs(sum(bedrag)) >= 0.005 ORDER BY bedrag DESC""",
        [*p, soort],
    ).df()


def subcategorieen(con, hoofdcategorie: str, van=None, tot=None, rekeningen=None) -> pd.DataFrame:
    w, p = _filter(van, tot, rekeningen)
    return con.execute(
        f"""SELECT subcategorie, CAST(-sum(bedrag) AS DOUBLE) AS bedrag, count(*) AS aantal
            FROM v_boekingen WHERE {w} AND hoofdcategorie = ? AND soort = 'uitgave'
            GROUP BY 1 ORDER BY bedrag DESC""",
        [*p, hoofdcategorie],
    ).df()


def top_tegenpartijen(con, van=None, tot=None, rekeningen=None, n: int = 10) -> pd.DataFrame:
    w, p = _filter(van, tot, rekeningen)
    return con.execute(
        f"""SELECT coalesce(tegenpartij_naam, left(omschrijving, 50), '(onbekend)') AS tegenpartij,
                   CAST(-sum(bedrag) AS DOUBLE) AS bedrag, count(*) AS aantal, any_value(categorie_label) AS categorie
            FROM v_boekingen WHERE {w} AND soort = 'uitgave'
            GROUP BY 1 ORDER BY bedrag DESC LIMIT ?""",
        [*p, n],
    ).df()


def per_tag(con, van=None, tot=None, rekeningen=None) -> pd.DataFrame:
    """Netto per tag (negatief = uitgegeven), inclusief transfers niet."""
    w, p = _filter(van, tot, rekeningen)
    return con.execute(
        f"""SELECT tag, CAST(sum(bedrag) AS DOUBLE) AS netto, CAST(-sum(bedrag) FILTER (WHERE soort = 'uitgave') AS DOUBLE) AS uitgegeven,
                   count(*) AS aantal, min(boekingsdatum) AS van, max(boekingsdatum) AS tot
            FROM (SELECT unnest(tags) AS tag, * EXCLUDE (tags) FROM v_boekingen WHERE {w} AND soort <> 'transfer')
            GROUP BY tag ORDER BY uitgegeven DESC NULLS LAST""",
        p,
    ).df()


def periode_grenzen(con) -> tuple[date | None, date | None]:
    r = con.execute("SELECT min(boekingsdatum), max(boekingsdatum) FROM transacties").fetchall()[0]
    return r[0], r[1]
