import streamlit as st
from gedeeld import PERIODES, categoriegrafiek, euro, geld_kolom, maandgrafiek, periode, verbinding

from datetime import date

from boekhouding import budget, rapporten, terugkerend
from boekhouding.db import nog_na_te_kijken

con = verbinding()
st.title("Overzicht")

eerste, laatste = rapporten.periode_grenzen(con)
if eerste is None:
    st.info(
        "Nog geen transacties. Begin met het importeren van een CSV- of CODA-export van je bank, "
        "of een PDF-rekeningafschrift.",
        icon=":material/waving_hand:",
    )
    st.page_link("paginas/importeren.py", label="Naar Importeren", icon=":material/upload_file:")
    st.stop()

rekeningen = con.execute(
    "SELECT iban, coalesce(naam, bank || ' ' || right(iban, 4)) FROM rekeningen ORDER BY bank, iban"
).fetchall()
rek_namen = dict(rekeningen)

keuze = st.segmented_control("Periode", PERIODES, default="Dit jaar", key="ov_periode") or "Dit jaar"
f2, f3 = st.columns([2, 3])
with f2:
    van, tot = periode(keuze, eerste, laatste)
    bereik = st.date_input("Van – tot", value=(van or eerste, tot or laatste), format="DD/MM/YYYY", key=f"ov_bereik_{keuze}")
    if isinstance(bereik, tuple) and len(bereik) == 2:
        van, tot = bereik
with f3:
    gekozen = st.multiselect("Rekeningen", list(rek_namen), format_func=lambda i: rek_namen[i], placeholder="Alle rekeningen")

k = rapporten.kerncijfers(con, van, tot, gekozen)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Inkomsten", euro(k["inkomsten"]), border=True)
c2.metric("Uitgaven", euro(k["uitgaven"]), border=True)
c3.metric("Gespaard en belegd", euro(k["sparen"]), border=True)
c4.metric(
    "Netto (inkomsten − uitgaven)",
    euro(k["netto"]),
    delta=f"{k['spaarquote']:.0%} van je inkomen" if k["spaarquote"] is not None else None,
    delta_color="normal",
    border=True,
)

if k["ongecategoriseerd_aantal"]:
    st.warning(
        f"{k['ongecategoriseerd_aantal']} transacties ({euro(k['ongecategoriseerd_bedrag'])}) hebben nog geen categorie. "
        "Ze tellen mee volgens hun teken, maar staan als 'Niet gecategoriseerd' in de grafieken.",
        icon=":material/label_off:",
    )
    st.page_link("paginas/transacties.py", label="Categoriseer ze", icon=":material/arrow_forward:")

# --- Signalen: nakijken, budgetten, prijsstijgingen -------------------------------------------
n_nakijken = nog_na_te_kijken(con)
vandaag_ = date.today()
budget_nu = budget.budget_status(con, vandaag_.year, vandaag_.month, vandaag_)
duurder = terugkerend.prijsstijgingen(terugkerend.detecteer(con, vandaag_))
s1, s2, s3 = st.columns(3)
with s1:
    if n_nakijken:
        st.info(f"**{n_nakijken}** transactie(s) nog na te kijken.", icon=":material/fact_check:")
        st.page_link("paginas/transacties.py", label="Naar de nakijklijst", icon=":material/arrow_forward:",
                     query_params={"nakijken": "1"})
with s2:
    if not budget_nu.empty:
        over = budget_nu[budget_nu["status"] == "over"]
        bijna = budget_nu[budget_nu["status"] == "let op"]
        if len(over) or len(bijna):
            namen = ", ".join(over["subcategorie"].head(3))
            st.warning(
                f"Budget deze maand: **{len(over)}** over" + (f" ({namen})" if namen else "") + f", **{len(bijna)}** boven 80%.",
                icon=":material/savings:",
            )
        else:
            st.success("Alle budgetten van deze maand zitten nog goed.", icon=":material/savings:")
        st.page_link("paginas/budgetten.py", label="Naar budgetten", icon=":material/arrow_forward:")
with s3:
    if not duurder.empty:
        st.warning("Duurder geworden: " + ", ".join(f"{r.naam} ({r.prijswijziging:+.0%})" for r in duurder.head(3).itertuples()),
                   icon=":material/trending_up:")
        st.page_link("paginas/vaste_lasten.py", label="Naar vaste lasten", icon=":material/arrow_forward:")

links, rechts = st.columns([3, 2], gap="large")
with links:
    st.subheader("Per maand")
    pm = rapporten.per_maand(con, van, tot, gekozen)
    if pm.empty:
        st.caption("Geen gegevens in deze periode.")
    else:
        st.altair_chart(maandgrafiek(pm), width="stretch")
        with st.expander("Als tabel"):
            tabel = pm.pivot_table(index="maand", columns="reeks", values="bedrag", aggfunc="sum").fillna(0).reset_index()
            tabel["maand"] = tabel["maand"].map(lambda d: d.strftime("%Y-%m"))
            st.dataframe(tabel, hide_index=True, column_config={c: geld_kolom(c) for c in tabel.columns if c != "maand"})

with rechts:
    st.subheader("Uitgaven per categorie")
    pc = rapporten.per_categorie(con, van, tot, gekozen, soort="uitgave")
    if pc.empty:
        st.caption("Geen uitgaven in deze periode.")
    else:
        st.altair_chart(categoriegrafiek(pc), width="stretch")

st.divider()
d1, d2 = st.columns(2, gap="large")
with d1:
    st.subheader("Detail van een categorie")
    hoofd = [c for c in pc["categorie"] if c != "Niet gecategoriseerd"] if not pc.empty else []
    if hoofd:
        gekozen_hoofd = st.selectbox("Hoofdcategorie", hoofd, label_visibility="collapsed")
        sub = rapporten.subcategorieen(con, gekozen_hoofd, van, tot, gekozen)
        st.dataframe(sub, hide_index=True, column_config={"subcategorie": "Subcategorie", "bedrag": geld_kolom("Bedrag"), "aantal": "Aantal"})
with d2:
    st.subheader("Grootste tegenpartijen")
    tp = rapporten.top_tegenpartijen(con, van, tot, gekozen)
    st.dataframe(
        tp, hide_index=True,
        column_config={"tegenpartij": "Tegenpartij", "bedrag": geld_kolom("Uitgegeven"), "aantal": "Aantal", "categorie": "Categorie"},
    )

st.subheader("Inkomsten per categorie")
ink = rapporten.per_categorie(con, van, tot, gekozen, soort="inkomst", niveau="sub")
st.dataframe(ink, hide_index=True, column_config={"categorie": "Categorie", "bedrag": geld_kolom("Bedrag"), "aantal": "Aantal"})

tags_df = rapporten.per_tag(con, van, tot, gekozen)
if not tags_df.empty:
    st.subheader("Per tag")
    st.dataframe(
        tags_df, hide_index=True,
        column_config={
            "tag": "Tag", "uitgegeven": geld_kolom("Uitgegeven"), "netto": geld_kolom("Netto"), "aantal": "Aantal",
            "van": st.column_config.DateColumn("Van", format="DD/MM/YYYY"), "tot": st.column_config.DateColumn("Tot", format="DD/MM/YYYY"),
        },
    )
