"""Gedeelde helpers voor de Streamlit-pagina's: verbinding, opmaak, kleuren en grafieken."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from boekhouding import db  # noqa: E402
from boekhouding.llm import aanbieder  # noqa: E402
from boekhouding.settings import TAKEN, Instellingen, laad_instellingen, met_database_keuzes  # noqa: E402

MAANDEN = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
REEKSEN = ["Inkomsten", "Uitgaven", "Sparen en beleggen"]
NL_GETAL = "replace(format(datum.value, ',.0f'), ',', '.')"  # 4.000 in plaats van 4,000

# Categorische kleuren (gevalideerd op kleurenblindheid en contrast), per thema apart gestapt.
KLEUREN = {
    "light": {"reeksen": ["#2a78d6", "#eb6834", "#1baf7a"], "enkel": "#2a78d6", "tekst": "#52514e", "raster": "#e4e3df"},
    "dark": {"reeksen": ["#3987e5", "#d95926", "#199e70"], "enkel": "#3987e5", "tekst": "#c3c2b7", "raster": "#383835"},
}


@st.cache_resource
def _basis_instellingen() -> Instellingen:
    inst = laad_instellingen()
    inst.maak_mappen()
    return inst


def instellingen() -> Instellingen:
    """Instellingen uit .env, met de AI-keuzes uit de database (pagina AI-modellen) erover."""
    try:
        keuzes = db.lees_instellingen(_database(str(_basis_instellingen().db_pad)).cursor())
    except Exception:
        keuzes = {}
    return met_database_keuzes(_basis_instellingen(), keuzes)


@st.cache_resource
def _database(pad: str):
    return db.verbind(pad)


def verbinding():
    """Een cursor per scriptrun: DuckDB-verbindingen zijn niet thread-safe, cursors wel."""
    try:
        return _database(str(_basis_instellingen().db_pad)).cursor()
    except Exception as e:  # meestal: database vergrendeld door een ander proces
        st.error(
            f"De database kon niet geopend worden: {e}\n\n"
            "Staat de app al open in een ander venster, of draait er een opdracht via de opdrachtregel?"
        )
        st.stop()


def thema() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def kleuren() -> dict:
    return KLEUREN[thema()]


def euro(x, decimalen: int = 2) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "–"
    s = f"{abs(x):,.{decimalen}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if x < 0 else ''}€ {s}"


def maandlabel(d) -> str:
    d = pd.Timestamp(d)
    return f"{MAANDEN[d.month - 1]} {d.year}"


PERIODES = ["Deze maand", "Vorige maand", "Laatste 3 maanden", "Dit jaar", "Vorig jaar", "Laatste 12 maanden", "Alles"]


def periode(keuze: str, eerste: date | None, laatste: date | None) -> tuple[date | None, date | None]:
    vandaag = date.today()
    begin_maand = vandaag.replace(day=1)
    if keuze == "Deze maand":
        return begin_maand, vandaag
    if keuze == "Vorige maand":
        einde = begin_maand - timedelta(days=1)
        return einde.replace(day=1), einde
    if keuze == "Laatste 3 maanden":
        m = begin_maand
        for _ in range(2):
            m = (m - timedelta(days=1)).replace(day=1)
        return m, vandaag
    if keuze == "Dit jaar":
        return date(vandaag.year, 1, 1), vandaag
    if keuze == "Vorig jaar":
        return date(vandaag.year - 1, 1, 1), date(vandaag.year - 1, 12, 31)
    if keuze == "Laatste 12 maanden":
        return (begin_maand - timedelta(days=335)).replace(day=1), vandaag
    return eerste, laatste


def zijbalk() -> None:
    inst = instellingen()
    with st.sidebar:
        st.caption(f"Gegevens: {inst.db_pad}")
        regels = []
        for taak, titel in zip(TAKEN, ("Documenten", "Categoriseren")):
            keuze = inst.ai(taak)
            teken = "✓" if inst.ai_actief(taak) else "✗"
            regels.append(f"{teken} **{titel}**: {aanbieder(keuze.provider).label.split(' (')[0]} · `{keuze.model or '—'}`")
        if all(inst.ai_actief(t) for t in TAKEN):
            st.success("  \n".join(regels), icon=":material/auto_awesome:")
        else:
            st.info("  \n".join(regels) + "  \nZonder AI werken CSV, CODA en regels gewoon.", icon=":material/info:")
        st.page_link("paginas/ai_modellen.py", label="AI-modellen instellen", icon=":material/tune:")


# --- Grafieken -----------------------------------------------------------------------


def _basis(chart: alt.Chart) -> alt.Chart:
    k = kleuren()
    return chart.configure_view(strokeWidth=0).configure_axis(
        labelColor=k["tekst"], titleColor=k["tekst"], gridColor=k["raster"], domain=False, tickColor=k["raster"]
    ).configure_legend(labelColor=k["tekst"], orient="top", title=None, symbolType="square")


def maandgrafiek(df: pd.DataFrame) -> alt.Chart:
    k = kleuren()
    df = df.copy()
    df["maand_label"] = df["maand"].map(maandlabel)
    volgorde = list(dict.fromkeys(df.sort_values("maand")["maand_label"]))
    df["bedrag_tekst"] = df["bedrag"].map(euro)
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, stroke=None)
        .encode(
            x=alt.X("maand_label:N", sort=volgorde, title=None, axis=alt.Axis(labelAngle=0), scale=alt.Scale(paddingInner=0.3)),
            xOffset=alt.XOffset("reeks:N", sort=REEKSEN, scale=alt.Scale(paddingInner=0.12)),
            y=alt.Y("bedrag:Q", title=None, axis=alt.Axis(labelExpr=NL_GETAL, tickCount=5)),
            color=alt.Color("reeks:N", scale=alt.Scale(domain=REEKSEN, range=k["reeksen"]), legend=alt.Legend()),
            tooltip=[
                alt.Tooltip("maand_label:N", title="Maand"),
                alt.Tooltip("reeks:N", title="Soort"),
                alt.Tooltip("bedrag_tekst:N", title="Bedrag"),
            ],
        )
        .properties(height=300)
    )
    return _basis(chart)


def categoriegrafiek(df: pd.DataFrame, hoogte_per_balk: int = 28) -> alt.Chart:
    k = kleuren()
    df = df.copy()
    df["bedrag_tekst"] = df["bedrag"].map(lambda x: euro(x, 0))
    basis = alt.Chart(df).encode(
        y=alt.Y("categorie:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220), scale=alt.Scale(paddingInner=0.35)),
        x=alt.X("bedrag:Q", title=None, axis=alt.Axis(labelExpr=NL_GETAL, tickCount=4)),
        tooltip=[
            alt.Tooltip("categorie:N", title="Categorie"),
            alt.Tooltip("bedrag_tekst:N", title="Bedrag"),
            alt.Tooltip("aantal:Q", title="Transacties"),
        ],
    )
    balken = basis.mark_bar(color=k["enkel"], cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
    labels = basis.mark_text(align="left", dx=4, color=k["tekst"], fontSize=11).encode(text="bedrag_tekst:N")
    return _basis((balken + labels).properties(height=max(120, hoogte_per_balk * len(df))))


def geld_kolom(label: str, **kw):
    return st.column_config.NumberColumn(label, format="euro", **kw)


STATUS = {"ok": "✔ Binnen budget", "let op": "⚠ Let op (80%+)", "over": "✖ Over budget"}


def budgetgrafiek(df: pd.DataFrame) -> alt.Chart:
    """Besteed (balk) tegenover beschikbaar budget (verticaal streepje) per categorie."""
    k = kleuren()
    df = df.copy()
    df["label"] = df["subcategorie"]
    df["besteed_tekst"] = df["besteed"].map(lambda x: euro(x, 0))
    df["budget_tekst"] = df["beschikbaar"].map(lambda x: euro(x, 0))
    df["status_tekst"] = df["status"].map(STATUS)
    volgorde = list(df.sort_values("beschikbaar", ascending=False)["label"])
    basis = alt.Chart(df).encode(
        y=alt.Y("label:N", sort=volgorde, title=None, axis=alt.Axis(labelLimit=220), scale=alt.Scale(paddingInner=0.35)),
        tooltip=[
            alt.Tooltip("label:N", title="Categorie"),
            alt.Tooltip("besteed_tekst:N", title="Besteed"),
            alt.Tooltip("budget_tekst:N", title="Beschikbaar"),
            alt.Tooltip("status_tekst:N", title="Status"),
        ],
    )
    balken = basis.mark_bar(color=k["enkel"], cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        x=alt.X("besteed:Q", title=None, axis=alt.Axis(labelExpr=NL_GETAL, tickCount=4))
    )
    streep = basis.mark_tick(color=k["tekst"], thickness=2, size=18).encode(x="beschikbaar:Q")
    labels = basis.mark_text(align="left", dx=8, color=k["tekst"], fontSize=11).encode(
        x=alt.X("max_x:Q"), text="besteed_tekst:N"
    ).transform_calculate(max_x="max(datum.besteed, datum.beschikbaar)")
    return _basis((balken + streep + labels).properties(height=max(120, 30 * len(df))))


def vooruitzichtgrafiek(reeks: pd.DataFrame, posten: pd.DataFrame) -> alt.Chart:
    """Verwacht saldo per dag (één lijn) met een nullijn en de terugkerende posten als punten."""
    k = kleuren()
    r = reeks.copy()
    r["datum"] = pd.to_datetime(r["datum"])
    r["saldo_tekst"] = r["saldo"].map(euro)
    # Nul enkel tonen als het saldo er in de buurt komt; anders wordt de lijn plat gedrukt.
    laag, hoog = float(r["saldo"].min()), float(r["saldo"].max())
    met_nul = laag <= 0 or laag < 0.2 * hoog
    lijn = alt.Chart(r).mark_line(color=k["enkel"], strokeWidth=2).encode(
        x=alt.X("datum:T", title=None, axis=alt.Axis(format="%d/%m", tickCount=8)),
        y=alt.Y("saldo:Q", title=None, axis=alt.Axis(labelExpr=NL_GETAL, tickCount=5), scale=alt.Scale(zero=met_nul)),
    )
    hover = alt.selection_point(fields=["datum"], nearest=True, on="pointerover", empty=False)
    punten = alt.Chart(r).mark_circle(size=60, color=k["enkel"]).encode(
        x="datum:T", y="saldo:Q",
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("datum:T", title="Datum", format="%d/%m/%Y"), alt.Tooltip("saldo_tekst:N", title="Verwacht saldo")],
    ).add_params(hover)
    nul = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=k["tekst"], strokeDash=[4, 4]).encode(y="y:Q")
    lagen = [nul, lijn, punten] if met_nul else [lijn, punten]
    if not posten.empty:
        p = posten.copy()
        p["datum"] = pd.to_datetime(p["datum"])
        p = p.merge(r[["datum", "saldo"]], on="datum", how="left")
        p["bedrag_tekst"] = p["bedrag"].map(euro)
        lagen.append(
            alt.Chart(p).mark_point(shape="diamond", size=70, filled=True, color=k["tekst"]).encode(
                x="datum:T", y="saldo:Q",
                tooltip=[alt.Tooltip("datum:T", title="Datum", format="%d/%m/%Y"), alt.Tooltip("naam:N", title="Post"),
                         alt.Tooltip("bedrag_tekst:N", title="Bedrag")],
            )
        )
    return _basis(alt.layer(*lagen).properties(height=320))
