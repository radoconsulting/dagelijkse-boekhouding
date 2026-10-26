"""Vaste lasten, abonnementen en terugkerende inkomsten herkennen, en een cashflowvooruitzicht maken.

Herkenning (zonder AI, puur op het patroon):
- groepeer verrichtingen per tegenpartij (IBAN, anders naam, anders de handelaar uit de omschrijving) en richting;
- een groep is terugkerend als de tussenpozen regelmatig zijn (week, maand, 2 maanden, kwartaal, half jaar of
  jaar) en de bedragen stabiel (minstens 3/4 binnen ±25% van de mediaan);
- de volgende datum en het verwachte bedrag volgen uit het patroon; een recente stijging boven 5% wordt gemeld.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import pandas as pd

from .saldi import huidig_saldo
from .utils import normaliseer_iban, zonder_accenten


@dataclass(frozen=True)
class Frequentie:
    naam: str
    dagen: float
    tolerantie: float
    stap: pd.DateOffset
    per_jaar: float
    min_aantal: int


FREQUENTIES = (
    Frequentie("wekelijks", 7, 2, pd.DateOffset(days=7), 52, 4),
    Frequentie("maandelijks", 30.4, 5, pd.DateOffset(months=1), 12, 3),
    Frequentie("tweemaandelijks", 61, 7, pd.DateOffset(months=2), 6, 3),
    Frequentie("per kwartaal", 91, 10, pd.DateOffset(months=3), 4, 2),
    Frequentie("halfjaarlijks", 182, 15, pd.DateOffset(months=6), 2, 2),
    Frequentie("jaarlijks", 365, 25, pd.DateOffset(years=1), 1, 2),
)

_PREFIX = re.compile(
    r"^(betaling (via|met) (bancontact|debetkaart|maestro|payconiq|kaart|apple pay|google pay)|europese domiciliering|"
    r"europese overschrijving|instantoverschrijving|overschrijving|domiciliering|kredietkaart|aankoop|betaling)\b[\s:·-]*"
)
STIJGING = 0.05


def handelaar(tekst: str | None) -> str:
    """Kern van een omschrijving, bv. 'BETALING VIA BANCONTACT 24-10 COLRUYT MECHELEN' -> 'colruyt mechelen'."""
    t = zonder_accenten(tekst or "").lower()
    t = re.sub(r"\b\d{1,2}[-/.]\d{1,2}([-/.]\d{2,4})?\b", " ", t)
    t = re.sub(r"\b\d{1,2}[.:h]\d{2}\b( uur)?", " ", t)
    t = _PREFIX.sub("", t.strip())
    t = re.sub(r"[^a-z ]+", " ", t)
    woorden = [w for w in t.split() if len(w) > 1 and w not in ("om", "uur", "via", "met", "bv", "nv", "sa", "srl", "bvba")]
    return " ".join(woorden[:3])


def _sleutel(r) -> tuple[str, str]:
    iban = normaliseer_iban(r.tegenpartij_iban) if isinstance(r.tegenpartij_iban, str) else None
    naam = r.tegenpartij_naam if isinstance(r.tegenpartij_naam, str) and r.tegenpartij_naam.strip() else None
    richting = "in" if r.bedrag > 0 else "uit"
    if iban:
        return f"{richting}|iban:{iban}", naam or iban
    if naam:
        kern = handelaar(naam) or naam.lower()
        return f"{richting}|naam:{kern}", naam
    kern = handelaar(r.omschrijving)
    return (f"{richting}|oms:{kern}", kern.title()) if kern else ("", "")


def detecteer(con: duckdb.DuckDBPyConnection, vandaag: date | None = None, maanden: int = 24,
              met_genegeerd: bool = False) -> pd.DataFrame:
    vandaag = vandaag or date.today()
    kolommen = ["sleutel", "naam", "richting", "categorie", "frequentie", "aantal", "bedrag", "laatste_bedrag",
                "laatste_datum", "volgende_datum", "jaarbedrag", "prijswijziging", "actief", "status", "stap", "rekening"]
    df = con.execute(
        """SELECT boekingsdatum, CAST(bedrag AS DOUBLE) AS bedrag, tegenpartij_naam, tegenpartij_iban, omschrijving,
                  categorie_label, rekening_naam
           FROM v_boekingen
           WHERE boekingsdatum >= ? AND boekingsdatum <= ? AND soort <> 'transfer' AND bedrag <> 0
           ORDER BY boekingsdatum""",
        [vandaag - timedelta(days=int(maanden * 30.5)), vandaag],
    ).df()
    if df.empty:
        return pd.DataFrame(columns=kolommen)
    sleutels = [_sleutel(r) for r in df.itertuples()]
    df["sleutel"] = [s for s, _ in sleutels]
    df["weergave"] = [n for _, n in sleutels]
    keuzes = dict(con.execute("SELECT sleutel, status FROM terugkerend_keuzes").fetchall())

    uit = []
    for sleutel, groep in df[df["sleutel"] != ""].groupby("sleutel"):
        if len(groep) < 2:
            continue
        datums = [pd.Timestamp(d).date() for d in groep["boekingsdatum"]]
        tussen = [(b - a).days for a, b in zip(datums, datums[1:])]
        tussen = [t for t in tussen if t > 0] or [0]
        mediaan = statistics.median(tussen)
        freq = next((f for f in FREQUENTIES if abs(mediaan - f.dagen) <= f.tolerantie), None)
        if freq is None or len(groep) < freq.min_aantal:
            continue
        regelmatig = sum(1 for t in tussen if abs(t - freq.dagen) <= freq.tolerantie * 1.5) / len(tussen)
        bedragen = [abs(b) for b in groep["bedrag"]]
        med_bedrag = statistics.median(bedragen)
        stabiel = sum(1 for b in bedragen if abs(b - med_bedrag) <= 0.25 * med_bedrag) / len(bedragen)
        if regelmatig < 0.6 or stabiel < 0.75:
            continue

        laatste = datums[-1]
        volgende = (pd.Timestamp(laatste) + freq.stap).date()
        actief = (vandaag - laatste).days <= freq.dagen * 1.5 + freq.tolerantie
        teken = 1 if sleutel.startswith("in|") else -1
        wijziging = None
        if len(bedragen) >= 3:
            vorige = statistics.median(bedragen[:-1])
            if vorige:
                wijziging = (bedragen[-1] - vorige) / vorige
        status = keuzes.get(sleutel, "")
        if status == "genegeerd" and not met_genegeerd:
            continue
        uit.append([
            sleutel,
            groep["weergave"].mode().iloc[0],
            "in" if teken > 0 else "uit",
            groep["categorie_label"].mode().iloc[0],
            freq.naam,
            len(groep),
            round(teken * med_bedrag, 2),
            round(teken * bedragen[-1], 2),
            laatste,
            volgende,
            round(teken * med_bedrag * freq.per_jaar, 2),
            wijziging,
            actief,
            status,
            freq.stap,
            groep["rekening_naam"].mode().iloc[0],
        ])
    res = pd.DataFrame(uit, columns=kolommen)
    return res.sort_values(["actief", "jaarbedrag"], ascending=[False, True], ignore_index=True)


def zet_keuze(con: duckdb.DuckDBPyConnection, sleutel: str, status: str | None) -> None:
    if not status:
        con.execute("DELETE FROM terugkerend_keuzes WHERE sleutel = ?", [sleutel])
    else:
        con.execute(
            """INSERT INTO terugkerend_keuzes (sleutel, status) VALUES (?, ?)
               ON CONFLICT (sleutel) DO UPDATE SET status = excluded.status, gewijzigd_op = now()""",
            [sleutel, status],
        )


def prijsstijgingen(vaste: pd.DataFrame, drempel: float = STIJGING) -> pd.DataFrame:
    if vaste.empty:
        return vaste
    m = vaste["actief"] & (vaste["richting"] == "uit") & vaste["prijswijziging"].fillna(0).gt(drempel)
    return vaste[m]


def vooruitzicht(con: duckdb.DuckDBPyConnection, dagen: int = 90, vandaag: date | None = None,
                 met_variabel: bool = True, rekeningsoorten: tuple[str, ...] = ("zicht",)) -> dict:
    """Verwacht saldo per dag: huidig saldo + terugkerende posten (+ gemiddelde variabele uitgaven)."""
    vandaag = vandaag or date.today()
    saldi = huidig_saldo(con, vandaag)
    saldi = saldi[saldi["soort"].isin(rekeningsoorten)]
    gekend = saldi[saldi["saldo"].notna()]
    start = float(gekend["saldo"].sum()) if not gekend.empty else 0.0
    onbekend = list(saldi[saldi["saldo"].isna()]["naam"])

    vaste = detecteer(con, vandaag)
    vaste = vaste[vaste["actief"]] if not vaste.empty else vaste
    einde = vandaag + timedelta(days=dagen)
    posten = []
    for r in vaste.itertuples():
        d = pd.Timestamp(r.volgende_datum)
        while d.date() <= vandaag:
            d += r.stap
        while d.date() <= einde:
            posten.append({"datum": d.date(), "naam": r.naam, "bedrag": r.bedrag, "categorie": r.categorie, "frequentie": r.frequentie})
            d += r.stap
    posten_df = pd.DataFrame(posten, columns=["datum", "naam", "bedrag", "categorie", "frequentie"]).sort_values("datum", ignore_index=True)

    variabel = 0.0
    if met_variabel:
        vaste_sleutels = set(vaste["sleutel"]) if not vaste.empty else set()
        recent = con.execute(
            """SELECT CAST(bedrag AS DOUBLE) AS bedrag, tegenpartij_naam, tegenpartij_iban, omschrijving
               FROM v_boekingen WHERE boekingsdatum > ? AND boekingsdatum <= ? AND soort = 'uitgave' AND bedrag < 0""",
            [vandaag - timedelta(days=90), vandaag],
        ).df()
        if not recent.empty:
            niet_vast = [r.bedrag for r in recent.itertuples() if _sleutel(r)[0] not in vaste_sleutels]
            variabel = sum(niet_vast) / 90

    per_dag = posten_df.groupby("datum")["bedrag"].sum().to_dict() if not posten_df.empty else {}
    saldo, reeks = start, []
    for i in range(1, dagen + 1):
        d = vandaag + timedelta(days=i)
        saldo += per_dag.get(d, 0.0) + variabel
        reeks.append({"datum": d, "saldo": round(saldo, 2)})
    reeks_df = pd.DataFrame(reeks)
    return {
        "startsaldo": round(start, 2),
        "onbekende_rekeningen": onbekend,
        "reeks": reeks_df,
        "posten": posten_df,
        "variabel_per_dag": round(variabel, 2),
        "laagste": reeks_df.loc[reeks_df["saldo"].idxmin()].to_dict() if not reeks_df.empty else None,
    }
