import pandas as pd
import streamlit as st
from gedeeld import geld_kolom, verbinding

from boekhouding import saldi
from boekhouding.categoriseren import TEKENS, VELDEN, Regel, pas_regels_toe, regel_treffers
from boekhouding.db import categorie_id as maak_categorie_id, categorie_labels
from boekhouding.utils import normaliseer_iban

con = verbinding()
st.title("Categorieën & regels")

labels = categorie_labels(con, alleen_actief=False)
actieve_labels = categorie_labels(con)
label_naar_id = {v: k for k, v in labels.items()}

tab_regels, tab_test, tab_cat, tab_rek = st.tabs(["Regels", "Regel testen", "Categorieën", "Rekeningen"])


def _leeg(x) -> bool:
    if isinstance(x, str):
        return not x.strip()
    try:
        return x is None or bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


with tab_regels:
    st.caption(
        "De eerste actieve regel die past (laagste prioriteit eerst) bepaalt de categorie. "
        "Patroon = reguliere expressie, hoofdletter- en accentongevoelig (bv. `colruyt|delhaize`). "
        "Veld 'alles' zoekt in tegenpartij, omschrijving en mededeling samen. Teken: in = geld komt binnen, uit = geld gaat buiten."
    )
    df = con.execute(
        """SELECT regel_id, actief, prioriteit, naam, veld, patroon, teken, min_bedrag, max_bedrag, rekening_iban,
                  categorie_id, bron FROM regels ORDER BY prioriteit, regel_id"""
    ).df()
    df["categorie"] = df["categorie_id"].map(labels)
    df = df.drop(columns=["categorie_id"])
    bewerkt = st.data_editor(
        df, num_rows="dynamic", hide_index=True, key="regels_editor", height=520,
        column_order=["actief", "prioriteit", "naam", "veld", "patroon", "teken", "categorie", "min_bedrag", "max_bedrag", "rekening_iban", "bron", "regel_id"],
        disabled=["regel_id", "bron"],
        column_config={
            "regel_id": st.column_config.NumberColumn("id", width="small"),
            "actief": st.column_config.CheckboxColumn("Actief", default=True, width="small"),
            "prioriteit": st.column_config.NumberColumn("Prioriteit", default=50, step=1, width="small"),
            "naam": "Naam",
            "veld": st.column_config.SelectboxColumn("Veld", options=list(VELDEN), default="alles", width="small"),
            "patroon": st.column_config.TextColumn("Patroon", width="medium", required=True),
            "teken": st.column_config.SelectboxColumn("Teken", options=list(TEKENS), default="beide", width="small"),
            "categorie": st.column_config.SelectboxColumn("Categorie", options=list(actieve_labels.values()), width="medium", required=True),
            "min_bedrag": geld_kolom("Min"),
            "max_bedrag": geld_kolom("Max"),
            "rekening_iban": st.column_config.TextColumn("Enkel rekening"),
            "bron": st.column_config.TextColumn("Bron", width="small"),
        },
    )
    if st.button("Bewaar regels en pas opnieuw toe", type="primary", icon=":material/save:"):
        fouten, n = [], 0
        for i, r in enumerate(bewerkt.to_dict("records"), start=1):
            if _leeg(r.get("patroon")) or _leeg(r.get("categorie")):
                continue
            cid = label_naar_id.get(r["categorie"])
            try:
                Regel(None, r["patroon"], cid, veld=r.get("veld") or "alles", teken=r.get("teken") or "beide")
            except ValueError as e:
                fouten.append(f"Rij {i}: {e}")
                continue
            waarden = [
                int(r["prioriteit"]) if not _leeg(r.get("prioriteit")) else 50, None if _leeg(r.get("naam")) else r["naam"],
                r.get("veld") or "alles", r["patroon"], r.get("teken") or "beide",
                None if _leeg(r.get("min_bedrag")) else float(r["min_bedrag"]),
                None if _leeg(r.get("max_bedrag")) else float(r["max_bedrag"]),
                normaliseer_iban(r.get("rekening_iban")) if not _leeg(r.get("rekening_iban")) else None,
                cid, bool(r.get("actief")) if r.get("actief") is not None else True,
            ]
            if not _leeg(r.get("regel_id")):
                rid = int(r["regel_id"])
                con.execute(
                    """UPDATE regels SET prioriteit = ?, naam = ?, veld = ?, patroon = ?, teken = ?, min_bedrag = ?,
                       max_bedrag = ?, rekening_iban = ?, categorie_id = ?, actief = ? WHERE regel_id = ?""",
                    [*waarden, rid],
                )
            else:
                con.execute(
                    """INSERT INTO regels (prioriteit, naam, veld, patroon, teken, min_bedrag, max_bedrag, rekening_iban,
                       categorie_id, actief, bron) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'handmatig')""",
                    waarden,
                )
            n += 1
        nog_aanwezig = {int(r["regel_id"]) for r in bewerkt.to_dict("records") if not _leeg(r.get("regel_id"))}
        verwijderd = {int(x) for x in df["regel_id"].dropna()} - nog_aanwezig
        for rid in verwijderd:
            con.execute("DELETE FROM regels WHERE regel_id = ?", [rid])
        for f in fouten:
            st.error(f)
        if not fouten:
            gewijzigd = pas_regels_toe(con)
            st.toast(f"{n} regels bewaard, {len(verwijderd)} verwijderd, {gewijzigd} transacties opnieuw gecategoriseerd")
            st.rerun()

with tab_test:
    st.caption("Probeer een patroon uit voor je er een regel van maakt.")
    t1, t2, t3 = st.columns([4, 2, 2])
    patroon = t1.text_input("Patroon", placeholder="bv. speelplein|jeugdbeweging")
    veld = t2.selectbox("Veld", VELDEN)
    teken = t3.selectbox("Teken", TEKENS)
    if patroon:
        try:
            test = Regel(None, patroon, "test", veld=veld, teken=teken)
            treffers = regel_treffers(con, test)
            st.write(f"**{len(treffers)}** transacties passen.")
            st.dataframe(
                treffers.drop(columns=["txn_id"]), hide_index=True,
                column_config={"boekingsdatum": st.column_config.DateColumn("Datum", format="DD/MM/YYYY"), "bedrag": geld_kolom("Bedrag")},
            )
            c1, c2, c3 = st.columns([4, 1, 2])
            cat = c1.selectbox("Maak er een regel van met categorie", list(actieve_labels.values()), index=None, placeholder="Kies een categorie")
            prio = c2.number_input("Prioriteit", value=40, step=5)
            if c3.button("Maak regel", disabled=cat is None, type="primary"):
                con.execute(
                    "INSERT INTO regels (prioriteit, naam, veld, patroon, teken, categorie_id, bron) VALUES (?, ?, ?, ?, ?, ?, 'handmatig')",
                    [int(prio), patroon[:60], veld, patroon, teken, label_naar_id[cat]],
                )
                st.toast(f"Regel gemaakt; {pas_regels_toe(con)} transacties aangepast")
        except ValueError as e:
            st.error(str(e))

with tab_cat:
    st.caption(
        "Soort bepaalt hoe een categorie meetelt: inkomst, uitgave, sparen (geld opzij, geen uitgave) of transfer "
        "(tussen eigen rekeningen, telt nergens mee). Een categorie in gebruik kan je niet verwijderen, wel op inactief zetten."
    )
    cdf = con.execute(
        """SELECT c.categorie_id, c.hoofdcategorie, c.subcategorie, c.soort, c.actief, count(t.txn_id) AS gebruikt
           FROM categorieen c LEFT JOIN transacties t USING (categorie_id)
           GROUP BY ALL ORDER BY min(c.volgorde) NULLS LAST, c.hoofdcategorie, c.subcategorie"""
    ).df()
    cbew = st.data_editor(
        cdf, num_rows="dynamic", hide_index=True, key="cat_editor", height=520,
        disabled=["categorie_id", "gebruikt"],
        column_config={
            "categorie_id": st.column_config.TextColumn("id", width="medium"),
            "hoofdcategorie": st.column_config.TextColumn("Hoofdcategorie", required=True),
            "subcategorie": st.column_config.TextColumn("Subcategorie", required=True),
            "soort": st.column_config.SelectboxColumn("Soort", options=["inkomst", "uitgave", "sparen", "transfer"], default="uitgave", required=True),
            "actief": st.column_config.CheckboxColumn("Actief", default=True),
            "gebruikt": st.column_config.NumberColumn("Transacties", width="small"),
        },
    )
    if st.button("Bewaar categorieën", type="primary", icon=":material/save:"):
        (volgorde,) = con.execute("SELECT coalesce(max(volgorde), 0) FROM categorieen").fetchall()[0]
        behouden = set()
        for r in cbew.to_dict("records"):
            if _leeg(r.get("hoofdcategorie")) or _leeg(r.get("subcategorie")):
                continue
            actief = True if r.get("actief") is None else bool(r["actief"])
            if not _leeg(r.get("categorie_id")):
                behouden.add(r["categorie_id"])
                con.execute(
                    "UPDATE categorieen SET hoofdcategorie = ?, subcategorie = ?, soort = ?, actief = ? WHERE categorie_id = ?",
                    [r["hoofdcategorie"].strip(), r["subcategorie"].strip(), r["soort"] or "uitgave", actief, r["categorie_id"]],
                )
            else:
                cid = maak_categorie_id(r["hoofdcategorie"], r["subcategorie"])
                volgorde += 10
                con.execute(
                    "INSERT INTO categorieen VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [cid, r["hoofdcategorie"].strip(), r["subcategorie"].strip(), r["soort"] or "uitgave", actief, volgorde],
                )
                behouden.add(cid)
        weg = set(cdf["categorie_id"]) - behouden
        geweigerd = []
        for cid in weg:
            in_gebruik = con.execute(
                "SELECT (SELECT count(*) FROM transacties WHERE categorie_id = ?) + (SELECT count(*) FROM regels WHERE categorie_id = ?)",
                [cid, cid],
            ).fetchall()[0][0]
            if in_gebruik:
                geweigerd.append(labels.get(cid, cid))
            else:
                con.execute("DELETE FROM categorieen WHERE categorie_id = ?", [cid])
        if geweigerd:
            st.warning("Niet verwijderd (nog in gebruik door transacties of regels; zet ze op inactief): " + ", ".join(geweigerd))
        else:
            st.toast("Categorieën bewaard")
            st.rerun()

with tab_rek:
    st.caption(
        "Rekeningen worden automatisch toegevoegd bij het importeren. Geef ze een herkenbare naam en kies de soort: "
        "zicht, spaar of kredietkaart. Het **gekend saldo op een datum** gebruikt de app voor het saldo en het vooruitzicht "
        "als je bankbestand zelf geen lopend saldo bevat (bv. Belfius zonder inleidende regels)."
    )
    huidige_saldi = saldi.huidig_saldo(con).set_index("iban")
    rdf = con.execute(
        """SELECT iban, bank, naam, eigenaar, coalesce(soort, 'zicht') AS soort,
                  CAST(startsaldo AS DOUBLE) AS startsaldo, startsaldo_datum FROM rekeningen ORDER BY bank, iban"""
    ).df()
    rdf["saldo_nu"] = [huidige_saldi["saldo"].get(i) for i in rdf["iban"]]
    rbew = st.data_editor(
        rdf, hide_index=True, key="rek_editor", disabled=["iban", "saldo_nu"],
        column_config={
            "iban": "IBAN", "bank": "Bank", "naam": "Naam ✎", "eigenaar": "Eigenaar ✎",
            "soort": st.column_config.SelectboxColumn("Soort ✎", options=["zicht", "spaar", "kredietkaart"], required=True),
            "startsaldo": st.column_config.NumberColumn("Gekend saldo ✎", format="€ %.2f"),
            "startsaldo_datum": st.column_config.DateColumn("Op datum ✎", format="DD/MM/YYYY"),
            "saldo_nu": geld_kolom("Saldo nu"),
        },
    )
    if st.button("Bewaar rekeningen", icon=":material/save:"):
        for r in rbew.to_dict("records"):
            saldo = None if _leeg(r["startsaldo"]) else float(r["startsaldo"])
            datum = None if _leeg(r["startsaldo_datum"]) else pd.Timestamp(r["startsaldo_datum"]).date()
            con.execute(
                """UPDATE rekeningen SET bank = ?, naam = ?, eigenaar = ?, soort = ?, startsaldo = ?, startsaldo_datum = ?
                   WHERE iban = ?""",
                [None if _leeg(r["bank"]) else r["bank"], None if _leeg(r["naam"]) else r["naam"],
                 None if _leeg(r["eigenaar"]) else r["eigenaar"], r["soort"] or "zicht", saldo, datum, r["iban"]],
            )
        st.toast("Rekeningen bewaard")
        st.rerun()
