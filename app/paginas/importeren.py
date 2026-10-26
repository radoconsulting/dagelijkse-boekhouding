import time

import pandas as pd
import streamlit as st
from gedeeld import instellingen, verbinding

from boekhouding import kredietkaart as kaart
from boekhouding.models import RekeningOntbreekt
from boekhouding.pipeline import (
    ONDERSTEUND,
    ImportVerslag,
    bewaar_origineel,
    categoriseer_nieuwe_met_ai,
    importeer_bestand,
    verwerk_inbox,
)
from boekhouding.utils import formatteer_iban, normaliseer_iban

con = verbinding()
inst = instellingen()
st.title("Importeren")
st.caption(
    "CSV-exports van KBC/CBC, Belfius en Crelan · CODA-bestanden (.cod) · PDF-rekeningafschriften · "
    "facturen als PDF of foto. Hetzelfde bestand of dezelfde verrichting twee keer importeren is geen probleem: "
    "dubbels worden herkend, ook tussen CSV, CODA en PDF."
)

st.session_state.setdefault("upload_sleutel", 0)
st.session_state.setdefault("wachtend", {})  # bestandsnaam -> bytes, voor bestanden zonder rekeningnummer
st.session_state.setdefault("verslagen", [])


def toon_verslag(v: ImportVerslag) -> None:
    if v.fout:
        (st.info if v.al_geimporteerd else st.error)(f"**{v.bestandsnaam}**: {v.fout}")
        return
    if v.soort == "factuur":
        tekst = f"**{v.bestandsnaam}**: factuur ingelezen"
        tekst += " en gekoppeld aan een betaling." if v.facturen_gekoppeld else ". Nog geen betaling gevonden; die wordt gekoppeld zodra ze geïmporteerd is."
        st.success(tekst, icon=":material/description:")
    else:
        rek = ", ".join(formatteer_iban(r) for r in v.rekeningen)
        saldo = {"ok": "saldo klopt ✓", "n.v.t.": "geen saldo om te controleren"}.get(v.saldo_controle, f"⚠ {v.saldo_controle}")
        tekst = (
            f"**{v.bestandsnaam}** ({v.bank or v.bron}, {rek}): {v.nieuw} nieuw, {v.dubbel} al gekend · {saldo}"
            f" · {v.gecategoriseerd_regels} via regels gecategoriseerd"
        )
        if v.gecategoriseerd_ai:
            tekst += f", {v.gecategoriseerd_ai} via AI"
        if v.facturen_gekoppeld:
            tekst += f" · {v.facturen_gekoppeld} factuur/facturen gekoppeld"
        if v.soort == "kredietkaart":
            tekst = (
                f"**{v.bestandsnaam}**: kredietkaartafrekening met {v.nieuw} nieuwe verrichting(en) · "
                + ({"ok": "totaal klopt ✓"}.get(v.saldo_controle, f"⚠ {v.saldo_controle}"))
                + (" · betaling op de zichtrekening gekoppeld" if v.kaartafrekeningen_gekoppeld
                   else " · betaling nog niet gevonden (wordt gekoppeld zodra die geïmporteerd is)")
            )
        elif v.kaartafrekeningen_gekoppeld:
            tekst += f" · {v.kaartafrekeningen_gekoppeld} kredietkaartafrekening(en) gekoppeld"
        (st.success if v.saldo_controle in ("ok", "n.v.t.") else st.warning)(tekst)
    for w in v.waarschuwingen:
        st.caption(f"⚠ {w}")


def _status(v: ImportVerslag) -> tuple[str, str]:
    """Icoon en korte tekst per bestand, voor het lopende logboek en de samenvattende tabel."""
    if v.al_geimporteerd:
        return "↺", "al eerder ingelezen"
    if v.fout:
        return "✗", v.fout
    if v.soort == "factuur":
        return "✓", "factuur" + (", gekoppeld aan een betaling" if v.facturen_gekoppeld else "")
    tekst = f"{v.nieuw} nieuw, {v.dubbel} al gekend"
    if v.saldo_controle not in ("ok", "n.v.t."):
        return "⚠", f"{tekst} · {v.saldo_controle}"
    return "✓", tekst + (" · saldo klopt" if v.saldo_controle == "ok" else "")


def toon_verslagen(verslagen: list[ImportVerslag]) -> None:
    """Een paar bestanden: een melding per bestand. Veel bestanden: een samenvatting en een tabel."""
    if len(verslagen) <= 5:
        for v in verslagen:
            toon_verslag(v)
        return
    rijen = [(v, *_status(v)) for v in verslagen]
    fouten = [v for v, icoon, _ in rijen if icoon == "✗"]
    let_op = [v for v, icoon, _ in rijen if icoon == "⚠"]
    nieuw = sum(v.nieuw for v in verslagen)
    regels = sum(v.gecategoriseerd_regels for v in verslagen)
    ai = sum(v.gecategoriseerd_ai for v in verslagen)
    samenvatting = (
        f"**{len(verslagen)} bestanden**: {sum(1 for _, i, _ in rijen if i == '✓')} ingelezen, "
        f"{sum(1 for _, i, _ in rijen if i == '↺')} al eerder ingelezen, {len(let_op)} om na te kijken, "
        f"{len(fouten)} mislukt · **{nieuw} nieuwe transacties**, waarvan {regels} via regels"
        + (f" en {ai} via AI" if ai else "") + " gecategoriseerd."
    )
    (st.error if fouten else st.warning if let_op else st.success)(samenvatting)
    st.dataframe(
        pd.DataFrame([{
            "": icoon, "bestand": v.bestandsnaam, "nieuw": v.nieuw, "gekend": v.dubbel, "resultaat": tekst,
            "duur": round(v.duur, 1),
        } for v, icoon, tekst in rijen]),
        hide_index=True, height=min(420, 35 * (len(rijen) + 1) + 3),
        column_config={
            "": st.column_config.TextColumn("", width="small"),
            "bestand": st.column_config.TextColumn("Bestand", width="medium"),
            "nieuw": st.column_config.NumberColumn("Nieuw"),
            "gekend": st.column_config.NumberColumn("Al gekend"),
            "resultaat": st.column_config.TextColumn("Resultaat", width="large"),
            "duur": st.column_config.NumberColumn("Duur", format="%.1f s"),
        },
    )
    for v in verslagen:
        for w in v.waarschuwingen:
            st.caption(f"⚠ {v.bestandsnaam}: {w}")


def importeer_reeks(bestanden: list[tuple[str, bytes]], pdf_soort: str, gebruik_ai: bool) -> list[ImportVerslag]:
    """Importeer bestand per bestand met een voortgangsbalk en een lopend logboek. AI-categorisatie gebeurt één
    keer op het einde voor alle nieuwe transacties samen: dat zijn veel minder oproepen dan per bestand."""
    verslagen: list[ImportVerslag] = []
    n = len(bestanden)
    balk = st.progress(0.0, text=f"0 van {n} bestanden")
    with st.status(f"Bezig met {n} bestand(en)…", expanded=True) as status:
        for i, (naam, inhoud) in enumerate(bestanden, start=1):
            balk.progress((i - 1) / n, text=f"Bestand {i} van {n}: {naam}")
            begin = time.monotonic()
            try:
                v = importeer_bestand(con, naam, inhoud, pdf_soort=pdf_soort, gebruik_ai=False, inst=inst)
            except RekeningOntbreekt:
                st.session_state.wachtend[naam] = inhoud
                st.write(f"⏸ **{naam}**: kies hieronder van welke rekening dit is")
                continue
            v.duur = time.monotonic() - begin
            if v.gelukt:
                bewaar_origineel(inst, naam, inhoud)
            verslagen.append(v)
            icoon, tekst = _status(v)
            st.write(f"{icoon} **{naam}** · {tekst} · {v.duur:.1f} s")
        balk.progress(1.0, text=f"{n} van {n} bestanden ingelezen")
        if gebruik_ai and any(v.nieuwe_ids for v in verslagen):
            status.update(label="AI categoriseert de nieuwe transacties…")
            ai_balk = st.progress(0.0, text="AI-categorisatie")
            categoriseer_nieuwe_met_ai(
                con, inst, verslagen, voortgang=lambda d, t: ai_balk.progress(d / t, text=f"AI-categorisatie: {d} van {t}")
            )
        status.update(label="Klaar", state="complete", expanded=False)
    return verslagen


tab_upload, tab_inbox, tab_kaart, tab_historiek = st.tabs(["Bestanden opladen", "Inbox-map", "Kredietkaarten", "Historiek"])

with tab_upload:
    bestanden = st.file_uploader(
        "Sleep je bestanden hierheen",
        type=[e.lstrip(".") for e in sorted(ONDERSTEUND)],
        accept_multiple_files=True,
        key=f"upload_{st.session_state.upload_sleutel}",
    )
    o1, o2 = st.columns(2)
    with o1:
        pdf_soort = st.radio(
            "PDF's zijn", ["auto", "afschrift", "kredietkaart", "factuur"], horizontal=True,
            format_func={"auto": "automatisch herkennen", "afschrift": "rekeningafschriften",
                         "kredietkaart": "kredietkaartafrekeningen", "factuur": "facturen"}.get,
        )
    with o2:
        ai_ok = inst.ai_actief("categorisatie")
        gebruik_ai = st.toggle(
            f"Wat geen regel heeft, laten categoriseren door AI ({inst.ai('categorisatie').model or 'niet ingesteld'})",
            value=ai_ok, disabled=not ai_ok,
            help="Enkel transacties zonder passende regel worden meegestuurd (datum, bedrag, tegenpartij, omschrijving, mededeling).",
        )

    if bestanden and len(bestanden) > 1:
        st.caption(
            f"{len(bestanden)} bestanden. Je ziet de voortgang per bestand; met **Stop** rechtsboven onderbreek je de "
            "reeks. Wat al ingelezen is, blijft bewaard, en opnieuw opladen is veilig: dubbels worden herkend."
        )
    if st.button("Importeer", type="primary", disabled=not bestanden, icon=":material/download:"):
        reeks = sorted(((b.name, b.getvalue()) for b in bestanden), key=lambda x: x[0])
        st.session_state.verslagen = importeer_reeks(reeks, pdf_soort, gebruik_ai)
        st.session_state.upload_sleutel += 1
        st.rerun()

    for naam, inhoud in list(st.session_state.wachtend.items()):
        with st.container(border=True):
            st.write(f"**{naam}** vermeldt niet van welke rekening de verrichtingen zijn.")
            bekend = [r[0] for r in con.execute("SELECT iban FROM rekeningen ORDER BY iban").fetchall()]
            k1, k2, k3 = st.columns([2, 2, 1])
            keuze = k1.selectbox("Bestaande rekening", ["(nieuwe rekening)", *bekend], key=f"kies_{naam}", format_func=lambda x: formatteer_iban(x) if x.startswith("BE") else x)
            nieuw = k2.text_input("of nieuw IBAN", key=f"nieuw_{naam}", placeholder="BE.. .... .... ....")
            iban = normaliseer_iban(nieuw) if keuze == "(nieuwe rekening)" else keuze
            if k3.button("Importeer", key=f"imp_{naam}", disabled=not iban):
                v = importeer_bestand(con, naam, inhoud, rekening_iban=iban, pdf_soort=pdf_soort, gebruik_ai=gebruik_ai, inst=inst)
                if v.gelukt:
                    bewaar_origineel(inst, naam, inhoud)
                st.session_state.verslagen = [v]
                del st.session_state.wachtend[naam]
                st.rerun()

    toon_verslagen(st.session_state.verslagen)

with tab_inbox:
    inst.inbox_dir.mkdir(parents=True, exist_ok=True)
    in_inbox = sorted(p.name for p in inst.inbox_dir.iterdir() if p.is_file() and p.suffix.lower() in ONDERSTEUND)
    st.write(
        f"Zet bestanden in **{inst.inbox_dir}** en verwerk ze hier in één keer. "
        "Gelukte bestanden verhuizen naar het archief; mislukte blijven staan."
    )
    st.write(f"{len(in_inbox)} bestand(en) klaar: " + (", ".join(in_inbox) if in_inbox else "—"))
    if st.button("Verwerk inbox", disabled=not in_inbox, icon=":material/inbox:"):
        balk = st.progress(0.0, text=f"0 van {len(in_inbox)} bestanden")
        st.session_state.inbox_verslagen = verwerk_inbox(
            con, inst, gebruik_ai=inst.ai_actief("categorisatie"),
            voortgang=lambda i, n, naam: balk.progress(i / n, text=f"Bestand {i + 1} van {n}: {naam}"),
        )
        st.rerun()
    toon_verslagen(st.session_state.get("inbox_verslagen", []))

with tab_kaart:
    st.caption(
        "Laad de maandelijkse afrekening (uitgavenstaat) van je kredietkaart op als PDF. De aankopen komen op een aparte "
        "kaartrekening en worden gewoon gecategoriseerd; het totaal dat van je zichtrekening gaat, telt dan als interne "
        "overboeking. Zo tel je niets dubbel en zie je toch waaraan het geld opging."
    )
    afr = con.execute(
        """SELECT k.afrekening_id, k.kaartnaam, k.afrekeningsdatum, CAST(k.totaal AS DOUBLE) AS totaal, k.controle,
                  t.boekingsdatum AS betaald_op, k.bestandsnaam, k.extractie
           FROM kaartafrekeningen k LEFT JOIN transacties t ON t.txn_id = k.betaald_txn_id
           ORDER BY k.afrekeningsdatum DESC"""
    ).df()
    if afr.empty:
        st.info("Nog geen kredietkaartafrekeningen ingelezen.", icon=":material/credit_card:")
    else:
        st.dataframe(
            afr, hide_index=True,
            column_order=["kaartnaam", "afrekeningsdatum", "totaal", "controle", "betaald_op", "bestandsnaam", "extractie"],
            column_config={
                "kaartnaam": "Kaart",
                "afrekeningsdatum": st.column_config.DateColumn("Afrekening", format="DD/MM/YYYY"),
                "totaal": st.column_config.NumberColumn("Totaal", format="euro"),
                "controle": st.column_config.TextColumn("Controle", help="ok = de verrichtingen tellen op tot het totaal"),
                "betaald_op": st.column_config.DateColumn("Betaald op", format="DD/MM/YYYY", help="Leeg = betaling nog niet gevonden"),
                "bestandsnaam": "Bestand", "extractie": "Gelezen met",
            },
        )
        open_ = afr[afr["betaald_op"].isna()]
        if not open_.empty:
            st.subheader("Zelf koppelen")
            keuzes = {r.afrekening_id: f"{r.kaartnaam} · {pd.Timestamp(r.afrekeningsdatum):%d/%m/%Y} · € {r.totaal:,.2f}"
                      for r in open_.itertuples()}
            afr_id = st.selectbox("Afrekening", list(keuzes), format_func=keuzes.get)
            kand = kaart.kandidaten_voor(con, afr_id)
            if kand:
                opties = {k[0]: f"{k[1]:%d/%m/%Y} · € {float(k[2]):,.2f} · {k[3]}" for k in kand}
                tid = st.selectbox("Betaling op de zichtrekening", list(opties), format_func=opties.get)
                if st.button("Koppel", type="primary", key="kaart_koppel"):
                    kaart.koppel_handmatig(con, afr_id, tid)
                    st.toast("Gekoppeld: de betaling telt nu als interne overboeking")
                    st.rerun()
            else:
                st.caption("Geen betalingen gevonden rond de afrekeningsdatum.")

with tab_historiek:
    hist = con.execute(
        """SELECT geimporteerd_op, bestandsnaam, bron, bank, rekening_iban, aantal_gelezen, aantal_nieuw, aantal_dubbel, saldo_controle
           FROM imports ORDER BY geimporteerd_op DESC LIMIT 500"""
    ).df()
    if hist.empty:
        st.caption("Nog niets geïmporteerd.")
    else:
        st.dataframe(
            hist, hide_index=True,
            column_config={
                "geimporteerd_op": st.column_config.DatetimeColumn("Wanneer", format="DD/MM/YYYY HH:mm"),
                "bestandsnaam": "Bestand", "bron": "Formaat", "bank": "Bank", "rekening_iban": "Rekening",
                "aantal_gelezen": "Gelezen", "aantal_nieuw": "Nieuw", "aantal_dubbel": "Dubbel", "saldo_controle": "Saldocontrole",
            },
        )
