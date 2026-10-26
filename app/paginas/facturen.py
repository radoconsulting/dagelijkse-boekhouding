from datetime import date, timedelta

import pandas as pd
import streamlit as st
from gedeeld import euro, geld_kolom, instellingen, verbinding

from boekhouding.db import BRON_FACTUUR, categorie_labels
from boekhouding.facturen import koppel_facturen
from boekhouding.pipeline import bewaar_origineel, importeer_bestand

con = verbinding()
inst = instellingen()
st.title("Facturen")
st.caption(
    "Facturen worden automatisch aan hun betaling gekoppeld: eerst via de gestructureerde mededeling, "
    "anders via bedrag en rekeningnummer of naam van de leverancier."
)

labels = categorie_labels(con)
label_naar_id = {v: k for k, v in labels.items()}

with st.expander("Facturen toevoegen", icon=":material/add:", expanded=False):
    st.session_state.setdefault("factuur_sleutel", 0)
    bestanden = st.file_uploader(
        "PDF of foto", type=["pdf", "jpg", "jpeg", "png", "webp", "heic"], accept_multiple_files=True,
        key=f"fact_{st.session_state.factuur_sleutel}",
    )
    if inst.ai_actief("documenten"):
        st.caption(f"Facturen worden gelezen met {inst.ai('documenten')}.")
    else:
        st.caption("Zonder AI worden enkel PDF's met tekst gelezen (OGM, IBAN, totaal, datums); foto's niet.")
    if st.button("Lees in", disabled=not bestanden, type="primary"):
        for b in bestanden:
            v = importeer_bestand(con, b.name, b.getvalue(), pdf_soort="factuur", inst=inst)
            if v.gelukt:
                bewaar_origineel(inst, b.name, b.getvalue())
                st.success(f"{b.name}: ingelezen" + (" en gekoppeld" if v.facturen_gekoppeld else ""))
            else:
                st.error(f"{b.name}: {v.fout}")
        st.session_state.factuur_sleutel += 1

df = con.execute(
    """SELECT f.factuur_id, f.status, f.leverancier, f.omschrijving, f.factuurnummer, f.factuurdatum, f.vervaldatum,
              f.bedrag_totaal, f.gestructureerde_mededeling,
              c.hoofdcategorie || ' / ' || c.subcategorie AS categorie,
              t.boekingsdatum AS betaald_op, f.bestandsnaam, f.extractie
       FROM facturen f
       LEFT JOIN categorieen c ON c.categorie_id = f.categorie_id
       LEFT JOIN transacties t ON t.txn_id = f.txn_id
       ORDER BY f.status DESC, f.vervaldatum NULLS LAST, f.factuurdatum DESC"""
).df()

if df.empty:
    st.info("Nog geen facturen.")
    st.stop()

open_ = df[df["status"] == "open"]
vervaldata = pd.to_datetime(open_["vervaldatum"])
te_laat = open_[vervaldata.notna() & (vervaldata < pd.Timestamp(date.today()))]
m1, m2, m3 = st.columns(3)
m1.metric("Openstaand", f"{len(open_)}", help="Facturen zonder gevonden betaling", border=True)
m2.metric("Openstaand bedrag", euro(float(open_["bedrag_totaal"].sum() or 0)), border=True)
m3.metric("Vervaldatum voorbij", f"{len(te_laat)}", border=True)

bewerkt = st.data_editor(
    df, hide_index=True, key="facturen_editor",
    column_order=["status", "leverancier", "omschrijving", "factuurnummer", "factuurdatum", "vervaldatum", "bedrag_totaal",
                  "gestructureerde_mededeling", "categorie", "betaald_op", "bestandsnaam", "extractie"],
    disabled=["status", "factuurnummer", "factuurdatum", "vervaldatum", "bedrag_totaal", "gestructureerde_mededeling", "betaald_op", "bestandsnaam", "extractie"],
    column_config={
        "status": st.column_config.TextColumn("Status", width="small"),
        "leverancier": "Leverancier ✎",
        "omschrijving": "Omschrijving ✎",
        "factuurnummer": "Nummer",
        "factuurdatum": st.column_config.DateColumn("Datum", format="DD/MM/YYYY"),
        "vervaldatum": st.column_config.DateColumn("Vervalt", format="DD/MM/YYYY"),
        "bedrag_totaal": geld_kolom("Bedrag"),
        "gestructureerde_mededeling": "OGM",
        "categorie": st.column_config.SelectboxColumn("Categorie ✎", options=list(labels.values())),
        "betaald_op": st.column_config.DateColumn("Betaald op", format="DD/MM/YYYY"),
        "bestandsnaam": "Bestand",
        "extractie": st.column_config.TextColumn("Gelezen via", width="small"),
    },
)

b1, b2, _ = st.columns([2, 2, 4])
if b1.button("Bewaar wijzigingen", icon=":material/save:"):
    n = 0
    for i in range(len(df)):
        o, nw = df.iloc[i], bewerkt.iloc[i]
        if (o["leverancier"], o["omschrijving"], o["categorie"]) != (nw["leverancier"], nw["omschrijving"], nw["categorie"]):
            cid = label_naar_id.get(nw["categorie"]) if isinstance(nw["categorie"], str) else None
            con.execute(
                "UPDATE facturen SET leverancier = ?, omschrijving = ?, categorie_id = ? WHERE factuur_id = ?",
                [nw["leverancier"], nw["omschrijving"], cid, o["factuur_id"]],
            )
            if cid:  # de categorie van de factuur volgt naar de gekoppelde betaling, tenzij die handmatig gekozen werd
                con.execute(
                    """UPDATE transacties SET categorie_id = ?, categorie_bron = ?, zekerheid = 1.0, regel_id = NULL
                       WHERE factuur_id = ? AND (categorie_bron IS NULL OR categorie_bron NOT IN ('handmatig', 'excel'))""",
                    [cid, BRON_FACTUUR, o["factuur_id"]],
                )
            n += 1
    st.toast(f"{n} factuur/facturen bijgewerkt")
    st.rerun()
if b2.button("Opnieuw koppelen", icon=":material/link:", help="Zoek opnieuw betalingen voor openstaande facturen"):
    st.toast(f"{koppel_facturen(con)} factuur/facturen gekoppeld")
    st.rerun()

if not open_.empty:
    st.subheader("Zelf koppelen")
    st.caption("Vond de app de betaling niet? Kies de factuur en de bijhorende transactie.")
    keuzes = {r.factuur_id: f"{r.leverancier or r.bestandsnaam} · {euro(r.bedrag_totaal)}" for r in open_.itertuples()}
    fid = st.selectbox("Factuur", list(keuzes), format_func=keuzes.get)
    f = open_[open_["factuur_id"] == fid].iloc[0]
    rond = next((pd.Timestamp(d).date() for d in (f["factuurdatum"], f["vervaldatum"]) if not pd.isna(d)), date.today())
    kandidaten = con.execute(
        """SELECT txn_id, boekingsdatum, bedrag, coalesce(tegenpartij_naam, left(omschrijving, 60)) AS tp
           FROM transacties
           WHERE factuur_id IS NULL AND bedrag < 0 AND boekingsdatum BETWEEN ? AND ?
           ORDER BY abs(abs(bedrag) - coalesce(?, 0)), boekingsdatum LIMIT 30""",
        [rond - timedelta(days=30), rond + timedelta(days=90), None if pd.isna(f["bedrag_totaal"]) else float(f["bedrag_totaal"])],
    ).fetchall()
    if kandidaten:
        opties = {k[0]: f"{k[1]:%d/%m/%Y} · {euro(float(k[2]))} · {k[3]}" for k in kandidaten}
        tid = st.selectbox("Betaling", list(opties), format_func=opties.get)
        if st.button("Koppel", type="primary"):
            con.execute("UPDATE facturen SET txn_id = ?, status = 'betaald' WHERE factuur_id = ?", [tid, fid])
            con.execute("UPDATE transacties SET factuur_id = ? WHERE txn_id = ?", [fid, tid])
            st.toast("Gekoppeld")
            st.rerun()
    else:
        st.caption("Geen betalingen gevonden rond de factuurdatum.")
