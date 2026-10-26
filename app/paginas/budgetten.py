from datetime import date

import pandas as pd
import streamlit as st
from gedeeld import STATUS, budgetgrafiek, euro, geld_kolom, maandlabel, verbinding

from boekhouding import budget
from boekhouding.db import categorie_labels

con = verbinding()
st.title("Budgetten")
st.caption(
    "Een maandbudget per categorie. 'Besteed' is de netto uitstroom: terugbetalingen verminderen het bedrag, en "
    "gesplitste transacties tellen per deel. Met **meenemen** schuift een over- of onderbesteding door naar de "
    "volgende maand (binnen hetzelfde jaar)."
)

vandaag = date.today()
eerste = con.execute("SELECT min(boekingsdatum) FROM transacties").fetchall()[0][0] or vandaag
maanden = pd.period_range(pd.Timestamp(eerste).to_period("M"), pd.Timestamp(vandaag).to_period("M"), freq="M")
maanden = [m.to_timestamp().date() for m in reversed(maanden)]
gekozen = st.selectbox("Maand", maanden, format_func=maandlabel, index=0)
jaar, maand = gekozen.year, gekozen.month

status = budget.budget_status(con, jaar, maand, vandaag)

# --- Stand van deze maand -----------------------------------------------------------------
if status.empty:
    st.info("Nog geen budgetten. Stel ze hieronder in, of neem het voorstel op basis van je gemiddelde uitgaven over.",
            icon=":material/savings:")
else:
    verwacht = status["verwacht_aandeel"].iloc[0]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Beschikbaar", euro(status["beschikbaar"].sum()), border=True)
    k2.metric("Besteed", euro(status["besteed"].sum()), border=True,
              help=f"We zijn {verwacht:.0%} door de maand" if 0 < verwacht < 1 else None)
    k3.metric("Resterend", euro(status["resterend"].sum()), border=True)
    n_over, n_let = int((status["status"] == "over").sum()), int((status["status"] == "let op").sum())
    k4.metric("Over budget", f"{n_over} van {len(status)}", border=True,
              help=f"Nog {n_let} categorie(ën) zitten boven 80% van hun budget" if n_let else None)
    if 0 < verwacht < 1:
        st.caption(f"We zijn {verwacht:.0%} door {maandlabel(gekozen)}: een categorie die verder staat dan dat, gaat sneller dan gepland.")

    links, rechts = st.columns([3, 2], gap="large")
    with links:
        tabel = status.assign(
            categorie=status["hoofdcategorie"] + " / " + status["subcategorie"],
            verbruik=status["aandeel"].clip(upper=1.5),
            status_tekst=status["status"].map(STATUS),
        )
        st.dataframe(
            tabel, hide_index=True,
            column_order=["categorie", "budget", "meegenomen", "beschikbaar", "besteed", "resterend", "verbruik", "status_tekst"],
            column_config={
                "categorie": st.column_config.TextColumn("Categorie", width="large"),
                "budget": geld_kolom("Budget"),
                "meegenomen": geld_kolom("Meegenomen", help="Over- of onderbesteding van vorige maanden (bij 'meenemen')"),
                "beschikbaar": geld_kolom("Beschikbaar"),
                "besteed": geld_kolom("Besteed"),
                "resterend": geld_kolom("Resterend"),
                "verbruik": st.column_config.ProgressColumn("Verbruik", min_value=0.0, max_value=1.0, format="percent"),
                "status_tekst": st.column_config.TextColumn("Status"),
            },
        )
    with rechts:
        st.altair_chart(budgetgrafiek(status), width="stretch")
        st.caption("Balk = besteed, streepje = beschikbaar budget.")

# --- Vergelijking ---------------------------------------------------------------------------
st.subheader(f"{maandlabel(gekozen)} tegenover de vorige 3 maanden")
verg = budget.maandvergelijking(con, jaar, maand)
if verg.empty:
    st.caption("Nog geen uitgaven om te vergelijken.")
else:
    kolommen = [k for k in verg.columns if not (k == "vorig_jaar" and verg[k].isna().all())]
    st.dataframe(
        verg, hide_index=True, column_order=kolommen,
        column_config={
            "hoofdcategorie": st.column_config.TextColumn("Categorie"),
            "deze_maand": geld_kolom(maandlabel(gekozen)),
            "gemiddeld": geld_kolom("Gemiddeld vorige 3 mnd"),
            "vorig_jaar": geld_kolom(f"{maandlabel(date(jaar - 1, maand, 1))}"),
            "verschil": geld_kolom("Verschil"),
            "verschil_pct": st.column_config.NumberColumn("Verschil %", format="percent"),
        },
    )

# --- Instellen ------------------------------------------------------------------------------
st.subheader("Budgetten instellen")
labels = categorie_labels(con)
soorten = dict(con.execute("SELECT categorie_id, soort FROM categorieen").fetchall())
huidig = budget.budgetten(con).set_index("categorie_id") if not budget.budgetten(con).empty else pd.DataFrame()
voorstel = budget.voorstel(con).set_index("categorie_id")
rijen = []
for cid, label in labels.items():
    if soorten.get(cid) != "uitgave":
        continue
    b = huidig.loc[cid] if cid in huidig.index else None
    v = voorstel.loc[cid] if cid in voorstel.index else None
    rijen.append({
        "categorie_id": cid, "categorie": label,
        "maandbedrag": float(b["maandbedrag"]) if b is not None else None,
        "meenemen": bool(b["meenemen"]) if b is not None else False,
        "gemiddeld": float(v["gemiddeld"]) if v is not None else None,
        "voorstel": float(v["voorstel"]) if v is not None else None,
    })
instel = pd.DataFrame(rijen)
alleen_met = st.toggle("Enkel categorieën met uitgaven of een budget", value=True)
zicht = instel[instel["maandbedrag"].notna() | instel["gemiddeld"].notna()] if alleen_met else instel
bewerkt = st.data_editor(
    zicht, hide_index=True, key="budget_editor", height=420,
    column_order=["categorie", "maandbedrag", "meenemen", "gemiddeld", "voorstel"],
    disabled=["categorie", "gemiddeld", "voorstel"],
    column_config={
        "categorie": st.column_config.TextColumn("Categorie", width="large"),
        "maandbedrag": st.column_config.NumberColumn("Maandbudget ✎", format="€ %.2f", min_value=0.0, step=5.0),
        "meenemen": st.column_config.CheckboxColumn("Meenemen ✎"),
        "gemiddeld": geld_kolom("Gemiddeld (6 mnd)"),
        "voorstel": geld_kolom("Voorstel"),
    },
)
b1, b2, _ = st.columns([2, 3, 3])
if b1.button("Bewaar budgetten", type="primary", icon=":material/save:"):
    n = 0
    for oud, nieuw in zip(zicht.to_dict("records"), bewerkt.to_dict("records")):
        oud_b = None if pd.isna(oud["maandbedrag"]) else round(oud["maandbedrag"], 2)
        nieuw_b = None if pd.isna(nieuw["maandbedrag"]) else round(float(nieuw["maandbedrag"]), 2)
        if (oud_b, bool(oud["meenemen"])) != (nieuw_b, bool(nieuw["meenemen"])):
            budget.bewaar_budget(con, nieuw["categorie_id"], nieuw_b, bool(nieuw["meenemen"]))
            n += 1
    st.toast(f"{n} budget(ten) bewaard", icon=":material/check:")
    st.rerun()
leeg_met_voorstel = zicht[zicht["maandbedrag"].isna() & zicht["voorstel"].notna()]
if b2.button(f"Neem het voorstel over voor {len(leeg_met_voorstel)} categorie(ën) zonder budget",
             disabled=leeg_met_voorstel.empty, icon=":material/auto_fix_high:"):
    for r in leeg_met_voorstel.itertuples():
        budget.bewaar_budget(con, r.categorie_id, r.voorstel)
    st.toast("Voorstel overgenomen; pas aan waar nodig", icon=":material/auto_fix_high:")
    st.rerun()
st.caption("Voorstel = gemiddelde netto uitgave per maand over de laatste 6 volledige maanden, naar boven afgerond op €5. "
           "Een leeg of nul budget verwijdert het budget.")
