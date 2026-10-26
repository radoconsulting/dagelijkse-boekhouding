import pandas as pd
import streamlit as st
from gedeeld import PERIODES, euro, geld_kolom, instellingen, periode, verbinding

from boekhouding import beheer, rapporten
from boekhouding.categoriseren import categoriseer_met_ai, maak_regel, pas_regels_toe, stel_regels_voor
from boekhouding.db import categorie_labels, nog_na_te_kijken
from boekhouding.llm import AIFout

con = verbinding()
inst = instellingen()
st.title("Transacties")

eerste, laatste = rapporten.periode_grenzen(con)
if eerste is None:
    st.info("Nog geen transacties. Importeer eerst een bestand.")
    st.stop()

labels = categorie_labels(con)
alle_labels = categorie_labels(con, alleen_actief=False)
label_naar_id = {v: k for k, v in alle_labels.items()}
GEEN = "Niet gecategoriseerd"
rek_namen = dict(con.execute("SELECT iban, coalesce(naam, bank || ' ' || right(iban, 4)) FROM rekeningen ORDER BY bank").fetchall())
tags_bekend = beheer.alle_tags(con)
n_nakijken = nog_na_te_kijken(con)

# --- Filters ------------------------------------------------------------------------
f1, f2, f3, f4 = st.columns([2, 2, 3, 3])
keuze = f1.selectbox("Periode", PERIODES, index=PERIODES.index("Alles"))
van, tot = periode(keuze, eerste, laatste)
rekeningen = f2.multiselect("Rekening", list(rek_namen), format_func=rek_namen.get, placeholder="Alle")
cats = f3.multiselect("Categorie", [GEEN, "Gesplitst", *labels.values()], placeholder="Alle categorieën")
zoek = f4.text_input("Zoeken", placeholder="tegenpartij, omschrijving, mededeling, notitie")
g1, g2, g3, g4 = st.columns([2, 2, 2, 3])
nakijken = g1.toggle(f"Nog na te kijken ({n_nakijken})", value=st.query_params.get("nakijken") == "1" and n_nakijken > 0)
alleen_leeg = g2.toggle("Enkel zonder categorie")
twijfel = g3.toggle("Enkel AI met lage zekerheid", help="Voorstellen van het AI-model met minder dan 70% zekerheid")
tag_filter = g4.multiselect("Tags", tags_bekend, placeholder="Alle tags") if tags_bekend else []

w, p = ["1=1"], []
if van:
    w.append("boekingsdatum >= ?"); p.append(van)
if tot:
    w.append("boekingsdatum <= ?"); p.append(tot)
if rekeningen:
    w.append("list_contains(?::VARCHAR[], rekening_iban)"); p.append(rekeningen)
if cats:
    w.append("list_contains(?::VARCHAR[], categorie_label)"); p.append(cats)
if nakijken:
    w.append("NOT coalesce(nagekeken, FALSE)")
if alleen_leeg:
    w.append("categorie_id IS NULL AND coalesce(categorie_bron, '') <> 'gesplitst'")
if twijfel:
    w.append("categorie_bron = 'ai' AND zekerheid < 0.7")
if tag_filter:
    w.append("(list_has_any(tags, ?::VARCHAR[]) OR txn_id IN (SELECT txn_id FROM splitsingen WHERE list_has_any(tags, ?::VARCHAR[])))")
    p.extend([tag_filter, tag_filter])
if zoek:
    w.append("concat_ws(' ', tegenpartij_naam, omschrijving, mededeling, notitie, gestructureerde_mededeling, array_to_string(tags, ' ')) ILIKE ?")
    p.append(f"%{zoek}%")

df = con.execute(
    f"""SELECT txn_id, coalesce(nagekeken, FALSE) AS ok, boekingsdatum, rekening_naam, bedrag,
               coalesce(tegenpartij_naam, '') AS tegenpartij, coalesce(omschrijving, '') AS omschrijving,
               coalesce(mededeling, gestructureerde_mededeling, '') AS mededeling,
               CASE WHEN categorie_id IS NULL THEN NULL ELSE categorie_label END AS categorie,
               categorie_bron = 'gesplitst' AS gesplitst,
               categorie_bron, zekerheid, coalesce(notitie, '') AS notitie,
               coalesce(array_to_string(tags, ', '), '') AS tags, factuur_id IS NOT NULL AS factuur
        FROM v_transacties WHERE {' AND '.join(w)}
        ORDER BY boekingsdatum DESC, txn_id LIMIT 3000""",
    p,
).df()
df["gesplitst"] = df["gesplitst"].fillna(False).astype(bool)

som = df["bedrag"].sum() if not df.empty else 0
st.caption(f"{len(df)} transacties · saldo van de selectie {euro(som)}")

bewerkt = st.data_editor(
    df,
    key=f"tx_{hash((keuze, tuple(rekeningen), tuple(cats), zoek, nakijken, alleen_leeg, twijfel, tuple(tag_filter)))}",
    hide_index=True,
    height=520,
    column_order=["ok", "boekingsdatum", "rekening_naam", "bedrag", "tegenpartij", "omschrijving", "mededeling", "categorie",
                  "gesplitst", "categorie_bron", "zekerheid", "tags", "notitie", "factuur"],
    disabled=["boekingsdatum", "rekening_naam", "bedrag", "tegenpartij", "omschrijving", "mededeling", "gesplitst",
              "categorie_bron", "zekerheid", "factuur"],
    column_config={
        "ok": st.column_config.CheckboxColumn("OK ✎", width="small", help="Nagekeken. Een categorie kiezen vinkt dit ook aan."),
        "boekingsdatum": st.column_config.DateColumn("Datum", format="DD/MM/YYYY", width="small"),
        "rekening_naam": st.column_config.TextColumn("Rekening", width="small"),
        "bedrag": geld_kolom("Bedrag", width="small"),
        "tegenpartij": st.column_config.TextColumn("Tegenpartij"),
        "omschrijving": st.column_config.TextColumn("Omschrijving", width="large"),
        "mededeling": st.column_config.TextColumn("Mededeling"),
        "categorie": st.column_config.SelectboxColumn("Categorie ✎", options=list(labels.values()), width="large"),
        "gesplitst": st.column_config.CheckboxColumn("Gesplitst", width="small", help="Pas de categorieën aan via 'Transactie splitsen'"),
        "categorie_bron": st.column_config.TextColumn("Bron", width="small", help="regel, ai, handmatig, excel, factuur, intern, kaart of gesplitst"),
        "zekerheid": st.column_config.ProgressColumn("Zekerheid", min_value=0.0, max_value=1.0, format="percent", width="small"),
        "tags": st.column_config.TextColumn("Tags ✎", help="Komma-gescheiden, bv. vakantie 2026, verbouwing"),
        "notitie": st.column_config.TextColumn("Notitie ✎"),
        "factuur": st.column_config.CheckboxColumn("Factuur", width="small"),
    },
)


def _leeg(x):
    return x is None or (isinstance(x, float) and pd.isna(x)) or x == ""


wijzigingen, genegeerd = [], 0
for i in range(len(df)):
    oud, nieuw = df.iloc[i], bewerkt.iloc[i]
    w_ = {}
    cat_anders = (_leeg(oud["categorie"]) != _leeg(nieuw["categorie"])) or (not _leeg(nieuw["categorie"]) and oud["categorie"] != nieuw["categorie"])
    if cat_anders:
        if oud["gesplitst"]:
            genegeerd += 1
        else:
            w_["categorie"] = None if _leeg(nieuw["categorie"]) else label_naar_id[nieuw["categorie"]]
    if (oud["notitie"] or "") != (nieuw["notitie"] or ""):
        w_["notitie"] = nieuw["notitie"] or None
    if beheer._schone_tags(oud["tags"]) != beheer._schone_tags(nieuw["tags"]):
        w_["tags"] = nieuw["tags"]
    if bool(oud["ok"]) != bool(nieuw["ok"]):
        w_["ok"] = bool(nieuw["ok"])
    if w_:
        wijzigingen.append((oud["txn_id"], w_))
if genegeerd:
    st.warning(f"{genegeerd} gesplitste transactie(s): pas de categorie van de delen aan via 'Transactie splitsen' hieronder.")

a1, a2, a3, a4 = st.columns([2, 2, 2, 3])
if a1.button(f"Bewaar {len(wijzigingen)} wijziging(en)", type="primary", disabled=not wijzigingen, icon=":material/save:"):
    for txn_id, w_ in wijzigingen:
        if "categorie" in w_:
            beheer.zet_categorie(con, txn_id, w_["categorie"])
        if "notitie" in w_:
            beheer.zet_notitie(con, txn_id, w_["notitie"])
        if "tags" in w_:
            beheer.zet_tags(con, txn_id, w_["tags"])
        if "ok" in w_:
            beheer.markeer_nagekeken(con, [txn_id], w_["ok"])
    st.toast(f"{len(wijzigingen)} transactie(s) bewaard", icon=":material/check:")
    st.rerun()

open_in_beeld = [t for t, ok in zip(df["txn_id"], df["ok"]) if not ok]
if a2.button(f"Vink {len(open_in_beeld)} getoonde af", disabled=not open_in_beeld or bool(wijzigingen), icon=":material/done_all:",
             help="Markeer alle getoonde transacties als nagekeken" + (" (bewaar eerst je wijzigingen)" if wijzigingen else "")):
    beheer.markeer_nagekeken(con, open_in_beeld)
    st.toast(f"{len(open_in_beeld)} transactie(s) nagekeken", icon=":material/done_all:")
    st.rerun()

if a3.button("Regels opnieuw toepassen", icon=":material/refresh:", help="Handmatige keuzes blijven altijd staan."):
    n = pas_regels_toe(con)
    st.toast(f"{n} transactie(s) aangepast", icon=":material/rule:")
    st.rerun()

(n_leeg,) = con.execute(
    "SELECT count(*) FROM transacties WHERE categorie_id IS NULL AND coalesce(categorie_bron, '') <> 'gesplitst'"
).fetchall()[0]
ai_ok = inst.ai_actief("categorisatie")
if a4.button(f"AI: categoriseer {n_leeg} zonder categorie", disabled=not (ai_ok and n_leeg), icon=":material/auto_awesome:",
             help=f"Met {inst.ai('categorisatie')}" if ai_ok else "Stel eerst een AI-model in (pagina AI-modellen)"):
    try:
        with st.spinner(f"{inst.ai('categorisatie').model} denkt na…"):
            n = categoriseer_met_ai(con, inst)
        st.toast(f"{n} AI-voorstel(len) toegepast. Controleer ze via 'Enkel AI met lage zekerheid'.", icon=":material/auto_awesome:")
        st.rerun()
    except AIFout as e:
        st.error(str(e))

# --- Splitsen --------------------------------------------------------------------------
with st.expander("Transactie splitsen", icon=":material/call_split:", expanded=False):
    if df.empty:
        st.caption("Geen transacties in de selectie.")
    else:
        st.caption(
            "Verdeel één transactie over meerdere categorieën, bv. een supermarktticket met boodschappen en huishoudartikelen. "
            "De delen moeten samen precies het bedrag van de transactie geven. Rapporten en budgetten tellen dan per deel."
        )
        opties = {
            r.txn_id: f"{pd.Timestamp(r.boekingsdatum):%d/%m/%Y} · {euro(r.bedrag)} · {(r.tegenpartij or r.omschrijving)[:50]}"
            + (" · gesplitst" if r.gesplitst else "")
            for r in df.head(500).itertuples()
        }
        txn_id = st.selectbox("Transactie (uit de selectie hierboven)", list(opties), format_func=opties.get, key="split_txn")
        rij = df[df["txn_id"] == txn_id].iloc[0]
        bestaand = beheer.splitsingen(con, txn_id)
        if bestaand.empty:
            huidige_cat = rij["categorie"] if not _leeg(rij["categorie"]) else None
            delen = pd.DataFrame([
                {"bedrag": float(rij["bedrag"]), "categorie": huidige_cat, "notitie": "", "tags": ""},
                {"bedrag": 0.0, "categorie": None, "notitie": "", "tags": ""},
            ])
        else:
            delen = pd.DataFrame({
                "bedrag": bestaand["bedrag"],
                "categorie": [alle_labels.get(c) for c in bestaand["categorie_id"]],
                "notitie": bestaand["notitie"].fillna(""),
                "tags": [", ".join(t) if t is not None else "" for t in bestaand["tags"]],
            })
        delen_bewerkt = st.data_editor(
            delen, num_rows="dynamic", hide_index=True, key=f"delen_{txn_id}",
            column_config={
                "bedrag": geld_kolom("Bedrag"),
                "categorie": st.column_config.SelectboxColumn("Categorie", options=list(labels.values()), width="large"),
                "notitie": "Notitie",
                "tags": st.column_config.TextColumn("Tags", help="Komma-gescheiden"),
            },
        )
        totaal = float(rij["bedrag"])
        verdeeld = float(pd.to_numeric(delen_bewerkt["bedrag"], errors="coerce").fillna(0).sum())
        rest = round(totaal - verdeeld, 2)
        (st.success if rest == 0 else st.info)(f"Transactie {euro(totaal)} · verdeeld {euro(verdeeld)} · nog te verdelen {euro(rest)}")
        s1, s2, _ = st.columns([2, 2, 4])
        if s1.button("Bewaar splitsing", type="primary", disabled=rest != 0, icon=":material/call_split:"):
            try:
                beheer.splits(con, txn_id, [
                    {"bedrag": d["bedrag"], "categorie_id": label_naar_id.get(d["categorie"]) if isinstance(d["categorie"], str) else None,
                     "notitie": d["notitie"], "tags": d["tags"]}
                    for d in delen_bewerkt.to_dict("records")
                ])
                st.toast("Splitsing bewaard", icon=":material/check:")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if not bestaand.empty and s2.button("Splitsing ongedaan maken", icon=":material/undo:"):
            beheer.verwijder_splitsing(con, txn_id)
            st.toast("Splitsing verwijderd; regels opnieuw toegepast")
            st.rerun()

# --- Regels voorstellen ---------------------------------------------------------------
voorstellen = stel_regels_voor(con)
if not voorstellen.empty:
    with st.expander(f"💡 {len(voorstellen)} regel(s) voorgesteld op basis van je handmatige keuzes", expanded=False):
        st.caption("Je gaf deze tegenpartijen telkens dezelfde categorie. Met een regel gebeurt dat voortaan automatisch.")
        voorstellen.insert(0, "maken", True)
        keuze_df = st.data_editor(
            voorstellen, hide_index=True, key="regelvoorstellen",
            column_order=["maken", "voorbeeld", "categorie", "aantal", "veld", "patroon"],
            disabled=["voorbeeld", "categorie", "aantal", "veld", "patroon"],
            column_config={"maken": st.column_config.CheckboxColumn("Maak regel"), "voorbeeld": "Tegenpartij", "categorie": "Categorie", "aantal": "Keer"},
        )
        if st.button("Maak de aangevinkte regels", icon=":material/add:"):
            n = 0
            for r in keuze_df[keuze_df["maken"]].to_dict("records"):
                maak_regel(con, r["patroon"], r["categorie_id"], veld=r["veld"], naam=f"Handmatig: {r['voorbeeld']}"[:60], prioriteit=30)
                n += 1
            pas_regels_toe(con)
            st.toast(f"{n} regel(s) gemaakt", icon=":material/rule:")
            st.rerun()
