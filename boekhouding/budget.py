"""Budgetten per (sub)categorie per maand.

- Een budget is een positief maandbedrag voor één categorie.
- 'Meenemen': het overschot (of tekort) van vorige maanden in hetzelfde jaar schuift door, vanaf de maand
  waarin je het budget instelde.
- 'Besteed' is de netto uitstroom in die categorie (terugbetalingen verminderen het bestede bedrag) en komt uit
  v_boekingen, dus gesplitste transacties tellen per deel.
"""

from __future__ import annotations

import calendar
import math
from datetime import date

import duckdb
import pandas as pd

GRENS_LET_OP = 0.8


def budgetten(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        """SELECT b.categorie_id, c.hoofdcategorie, c.subcategorie, c.soort,
                  CAST(b.maandbedrag AS DOUBLE) AS maandbedrag, b.meenemen, b.vanaf
           FROM budgetten b JOIN categorieen c USING (categorie_id)
           ORDER BY c.volgorde NULLS LAST, c.hoofdcategorie, c.subcategorie"""
    ).df()


def bewaar_budget(con, categorie_id: str, maandbedrag: float | None, meenemen: bool = False, vanaf: date | None = None) -> None:
    """Zet of wijzig een budget; een leeg of nul bedrag verwijdert het."""
    if not maandbedrag or maandbedrag <= 0:
        con.execute("DELETE FROM budgetten WHERE categorie_id = ?", [categorie_id])
        return
    vanaf = (vanaf or date.today()).replace(day=1)
    con.execute(
        """INSERT INTO budgetten (categorie_id, maandbedrag, meenemen, vanaf) VALUES (?, ?, ?, ?)
           ON CONFLICT (categorie_id) DO UPDATE SET maandbedrag = excluded.maandbedrag, meenemen = excluded.meenemen""",
        [categorie_id, round(float(maandbedrag), 2), bool(meenemen), vanaf],
    )


def _besteed_per_maand(con, jaar: int) -> pd.DataFrame:
    return con.execute(
        """SELECT categorie_id, month(boekingsdatum) AS maand, CAST(-sum(bedrag) AS DOUBLE) AS besteed
           FROM v_boekingen WHERE jaar = ? AND categorie_id IS NOT NULL
           GROUP BY ALL""",
        [jaar],
    ).df()


def budget_status(con: duckdb.DuckDBPyConnection, jaar: int, maand: int, vandaag: date | None = None) -> pd.DataFrame:
    """Per gebudgetteerde categorie: budget, meegenomen, beschikbaar, besteed, resterend, aandeel en status."""
    vandaag = vandaag or date.today()
    b = budgetten(con)
    kolommen = ["categorie_id", "hoofdcategorie", "subcategorie", "budget", "meegenomen", "beschikbaar", "besteed",
                "resterend", "aandeel", "verwacht_aandeel", "status"]
    if b.empty:
        return pd.DataFrame(columns=kolommen)
    besteed = _besteed_per_maand(con, jaar)
    idx = {(r.categorie_id, int(r.maand)): r.besteed for r in besteed.itertuples()}

    dagen = calendar.monthrange(jaar, maand)[1]
    if (jaar, maand) == (vandaag.year, vandaag.month):
        verwacht = vandaag.day / dagen
    elif (jaar, maand) < (vandaag.year, vandaag.month):
        verwacht = 1.0
    else:
        verwacht = 0.0

    rijen = []
    for r in b.itertuples():
        mee = 0.0
        if r.meenemen and r.vanaf is not None:
            start = pd.Timestamp(r.vanaf)
            eerste = start.month if start.year == jaar else (1 if start.year < jaar else maand)
            for m in range(eerste, maand):
                mee += r.maandbedrag - idx.get((r.categorie_id, m), 0.0)
        nu = idx.get((r.categorie_id, maand), 0.0)
        beschikbaar = r.maandbedrag + mee
        aandeel = nu / beschikbaar if beschikbaar > 0 else (math.inf if nu > 0 else 0.0)
        status = "over" if aandeel > 1 else ("let op" if aandeel >= GRENS_LET_OP else "ok")
        rijen.append([r.categorie_id, r.hoofdcategorie, r.subcategorie, r.maandbedrag, round(mee, 2), round(beschikbaar, 2),
                      round(nu, 2), round(beschikbaar - nu, 2), aandeel, verwacht, status])
    return pd.DataFrame(rijen, columns=kolommen)


def voorstel(con: duckdb.DuckDBPyConnection, maanden: int = 6, vandaag: date | None = None) -> pd.DataFrame:
    """Gemiddelde netto uitgave per categorie over de laatste volledige maanden, afgerond naar boven op €5."""
    vandaag = vandaag or date.today()
    einde = vandaag.replace(day=1)
    start = (pd.Timestamp(einde) - pd.DateOffset(months=maanden)).date()
    # Deel door het aantal maanden waarvoor er gegevens zijn, niet blind door 'maanden'.
    (n_maanden,) = con.execute(
        "SELECT count(DISTINCT date_trunc('month', boekingsdatum)) FROM transacties WHERE boekingsdatum >= ? AND boekingsdatum < ?",
        [start, einde],
    ).fetchall()[0]
    df = con.execute(
        """SELECT categorie_id, categorie_label, CAST(-sum(bedrag) AS DOUBLE) / ? AS gemiddeld,
                  count(DISTINCT maand) AS maanden_met_uitgaven
           FROM v_boekingen
           WHERE boekingsdatum >= ? AND boekingsdatum < ? AND soort = 'uitgave' AND categorie_id IS NOT NULL
           GROUP BY ALL HAVING sum(bedrag) < 0 ORDER BY gemiddeld DESC""",
        [max(int(n_maanden), 1), start, einde],
    ).df()
    df["voorstel"] = df["gemiddeld"].map(lambda x: float(math.ceil(x / 5) * 5))
    return df


def maandvergelijking(con: duckdb.DuckDBPyConnection, jaar: int, maand: int, vorige: int = 3) -> pd.DataFrame:
    """Uitgaven per hoofdcategorie: deze maand, gemiddelde van de vorige maanden en dezelfde maand vorig jaar."""
    deze = date(jaar, maand, 1)
    start = (pd.Timestamp(deze) - pd.DateOffset(months=vorige)).date()
    vorig_jaar = date(jaar - 1, maand, 1)
    (n,) = con.execute(
        "SELECT count(DISTINCT date_trunc('month', boekingsdatum)) FROM transacties WHERE boekingsdatum >= ? AND boekingsdatum < ?",
        [start, deze],
    ).fetchall()[0]
    df = con.execute(
        """WITH m AS (
               SELECT coalesce(hoofdcategorie, 'Niet gecategoriseerd') AS hoofdcategorie, maand, -sum(bedrag) AS besteed
               FROM v_boekingen WHERE soort = 'uitgave' GROUP BY ALL)
           SELECT hoofdcategorie,
                  CAST(coalesce(sum(besteed) FILTER (WHERE maand = ?), 0) AS DOUBLE) AS deze_maand,
                  CAST(coalesce(sum(besteed) FILTER (WHERE maand >= ? AND maand < ?), 0) AS DOUBLE) / ? AS gemiddeld,
                  CAST(sum(besteed) FILTER (WHERE maand = ?) AS DOUBLE) AS vorig_jaar
           FROM m GROUP BY 1""",
        [deze, start, deze, max(int(n), 1), vorig_jaar],
    ).df()
    df = df[(df["deze_maand"].abs() > 0.005) | (df["gemiddeld"].abs() > 0.005)].copy()
    df["verschil"] = df["deze_maand"] - df["gemiddeld"]
    df["verschil_pct"] = df.apply(lambda r: r["verschil"] / r["gemiddeld"] if r["gemiddeld"] > 0.005 else None, axis=1)
    return df.sort_values("deze_maand", ascending=False, ignore_index=True)
