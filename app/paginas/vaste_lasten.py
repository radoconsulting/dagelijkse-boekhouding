from datetime import date

import pandas as pd
import streamlit as st
from gedeeld import euro, geld_kolom, verbinding, vooruitzichtgrafiek

from boekhouding import saldi, terugkerend

con = verbinding()
st.title("Vaste lasten & vooruitzicht")
vandaag = date.today()

tab_vast, tab_vooruit, tab_saldi = st.tabs(["Vaste lasten en abonnementen", "Vooruitzicht", "Saldi per rekening"])

with tab_vast:
    st.caption(
        "Herkend uit je transacties: dezelfde tegenpartij met een regelmatig ritme (week, maand, kwartaal, jaar) en een "
        "stabiel bedrag. Domiciliëringen, abonnementen, lening, loon en kindergeld komen hier vanzelf in."
    )
    vaste = terugkerend.detecteer(con, vandaag)
    if vaste.empty:
        st.info("Nog geen terugkerende posten gevonden. Daarvoor zijn minstens 3 maanden aan transacties nodig.")
    else:
        actief = vaste[vaste["actief"]]
        uit, in_ = actief[actief["richting"] == "uit"], actief[actief["richting"] == "in"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Vaste uitgaven per maand", euro(-uit["jaarbedrag"].sum() / 12), border=True)
        k2.metric("Vaste inkomsten per maand", euro(in_["jaarbedrag"].sum() / 12), border=True)
        k3.metric("Actieve posten", f"{len(actief)}", border=True)
        duurder = terugkerend.prijsstijgingen(vaste)
        k4.metric("Duurder geworden", f"{len(duurder)}", border=True)
        for r in duurder.itertuples():
            st.warning(
                f"**{r.naam}** ({r.categorie}) kost nu {euro(abs(r.laatste_bedrag))} in plaats van ongeveer "
                f"{euro(abs(r.bedrag))}: {r.prijswijziging:+.0%}.", icon=":material/trending_up:"
            )

        toon = vaste.assign(
            status_tekst=vaste["actief"].map({True: "actief", False: "gestopt?"}),
            per_maand=vaste["jaarbedrag"] / 12,
        )
        st.dataframe(
            toon, hide_index=True, height=min(420, 35 * (len(toon) + 1) + 3),
            column_order=["naam", "categorie", "frequentie", "bedrag", "laatste_bedrag", "prijswijziging", "per_maand",
                          "laatste_datum", "volgende_datum", "rekening", "status_tekst"],
            column_config={
                "naam": st.column_config.TextColumn("Tegenpartij", width="medium"),
                "categorie": st.column_config.TextColumn("Categorie", width="medium"),
                "frequentie": "Ritme",
                "bedrag": geld_kolom("Gewoonlijk"),
                "laatste_bedrag": geld_kolom("Laatste"),
                "prijswijziging": st.column_config.NumberColumn("Wijziging", format="percent", help="Laatste bedrag tegenover de mediaan van de vorige"),
                "per_maand": geld_kolom("Per maand"),
                "laatste_datum": st.column_config.DateColumn("Laatst", format="DD/MM/YYYY"),
                "volgende_datum": st.column_config.DateColumn("Verwacht", format="DD/MM/YYYY"),
                "rekening": "Rekening",
                "status_tekst": st.column_config.TextColumn("Status", help="'gestopt?' = de verwachte betaling bleef uit"),
            },
        )
        with st.expander("Iets dat geen vaste last is verbergen", icon=":material/visibility_off:"):
            namen = {r.sleutel: f"{r.naam} · {r.frequentie} · {euro(r.bedrag)}" for r in vaste.itertuples()}
            weg = st.multiselect("Verberg", list(namen), format_func=namen.get, placeholder="Kies posten")
            if st.button("Verberg geselecteerde", disabled=not weg):
                for s in weg:
                    terugkerend.zet_keuze(con, s, "genegeerd")
                st.rerun()
            verborgen = terugkerend.detecteer(con, vandaag, met_genegeerd=True)
            verborgen = verborgen[verborgen["status"] == "genegeerd"]
            if not verborgen.empty:
                terug = st.multiselect("Verborgen posten terug tonen", list(verborgen["sleutel"]),
                                       format_func=lambda s: verborgen.set_index("sleutel").loc[s, "naam"])
                if st.button("Toon opnieuw", disabled=not terug):
                    for s in terug:
                        terugkerend.zet_keuze(con, s, None)
                    st.rerun()

with tab_vooruit:
    c1, c2, c3 = st.columns([2, 2, 3])
    dagen = c1.segmented_control("Horizon", [30, 60, 90], default=60, format_func=lambda d: f"{d} dagen") or 60
    soorten = c2.multiselect("Rekeningen", ["zicht", "spaar"], default=["zicht"], format_func={"zicht": "zichtrekeningen", "spaar": "spaarrekeningen"}.get)
    met_variabel = c3.toggle("Reken ook de gewone dagelijkse uitgaven mee", value=True,
                             help="Gemiddelde van de niet-vaste uitgaven over de laatste 90 dagen, gespreid per dag")
    vz = terugkerend.vooruitzicht(con, int(dagen), vandaag, met_variabel=met_variabel, rekeningsoorten=tuple(soorten or ["zicht"]))
    if vz["onbekende_rekeningen"]:
        st.warning(
            "Voor deze rekeningen is het saldo onbekend en tellen ze als €0: " + ", ".join(vz["onbekende_rekeningen"])
            + ". Vul een gekend saldo in via Categorieën & regels → Rekeningen.", icon=":material/help:"
        )
    m1, m2, m3 = st.columns(3)
    m1.metric("Saldo nu", euro(vz["startsaldo"]), border=True)
    if vz["laagste"]:
        m2.metric("Laagste verwacht saldo", euro(vz["laagste"]["saldo"]), border=True,
                  help=f"Op {pd.Timestamp(vz['laagste']['datum']):%d/%m/%Y}")
    eind = vz["reeks"]["saldo"].iloc[-1] if not vz["reeks"].empty else vz["startsaldo"]
    m3.metric(f"Over {dagen} dagen", euro(eind), delta=euro(eind - vz["startsaldo"]), border=True)
    if vz["laagste"] and vz["laagste"]["saldo"] < 0:
        st.error(f"Het saldo dreigt negatief te worden rond {pd.Timestamp(vz['laagste']['datum']):%d/%m/%Y}.", icon=":material/warning:")
    if not vz["reeks"].empty:
        st.altair_chart(vooruitzichtgrafiek(vz["reeks"], vz["posten"]), width="stretch")
        st.caption(
            f"Lijn = verwacht saldo, ruitjes = vaste posten. Gewone uitgaven: {euro(vz['variabel_per_dag'])} per dag."
            if met_variabel else "Lijn = verwacht saldo, ruitjes = vaste posten."
        )
    st.subheader("Verwachte vaste posten")
    if vz["posten"].empty:
        st.caption("Geen vaste posten verwacht in deze periode.")
    else:
        st.dataframe(
            vz["posten"], hide_index=True,
            column_config={
                "datum": st.column_config.DateColumn("Datum", format="DD/MM/YYYY"),
                "naam": "Tegenpartij", "bedrag": geld_kolom("Bedrag"), "categorie": "Categorie", "frequentie": "Ritme",
            },
        )

with tab_saldi:
    st.caption(
        "Het saldo komt uit je bankbestanden (KBC- en Crelan-CSV, CODA, sluitende PDF-afschriften, het laatste saldo in "
        "een Belfius-export) of uit een gekend saldo dat je zelf invult, aangevuld met alle latere verrichtingen."
    )
    s = saldi.huidig_saldo(con, vandaag)
    st.dataframe(
        s, hide_index=True,
        column_order=["naam", "soort", "saldo", "bron", "sinds", "iban"],
        column_config={
            "naam": "Rekening", "soort": "Soort", "saldo": geld_kolom("Saldo"),
            "bron": st.column_config.TextColumn("Bron", help="bankbestand, startsaldo (gekend saldo) of onbekend"),
            "sinds": st.column_config.DateColumn("Gekend op", format="DD/MM/YYYY"), "iban": "IBAN",
        },
    )
    st.page_link("paginas/regels.py", label="Saldo of soort van een rekening aanpassen", icon=":material/edit:")
