import pandas as pd
import streamlit as st
from gedeeld import instellingen, verbinding

from boekhouding import db
from boekhouding.llm import AIFout, catalogus, controleer_verbinding, lijst_modellen
from boekhouding.settings import PROVIDERS, SLEUTEL_VARIABELEN, TAKEN, standaard_model

con = verbinding()
inst = instellingen()
cat = catalogus()

st.title("AI-modellen")
st.caption(
    "De app gebruikt AI voor twee taken: **documenten lezen** (PDF-afschriften, facturen, foto's) en "
    "**categoriseren** (transacties zonder passende regel). Per taak kies je een aanbieder en een model. "
    "API-sleutels zet je in het bestand `.env` (zie onderaan); je keuze hier wordt in de database bewaard."
)

TITELS = {"documenten": "Documenten lezen", "categorisatie": "Categoriseren"}
ANDER = "Ander model…"


def aanbieder_label(code: str) -> str:
    klaar, _ = inst.provider_klaar(code)
    return f"{cat[code].label}  {'✓' if klaar else '· niet ingesteld'}"


# --- Keuze per taak ------------------------------------------------------------------------
kolommen = st.columns(2, gap="large")
for taak, kolom in zip(TAKEN, kolommen):
    huidig = inst.ai(taak)
    with kolom, st.container(border=True):
        st.subheader(TITELS[taak])
        provider = st.selectbox(
            "Aanbieder", PROVIDERS, index=PROVIDERS.index(huidig.provider), format_func=aanbieder_label, key=f"prov_{taak}"
        )
        info = cat[provider]
        klaar, uitleg = inst.provider_klaar(provider)
        if not klaar:
            st.warning(f"{uitleg}. Voeg dit toe aan `.env` en herstart de app.", icon=":material/key:")

        opties = [m.id for m in info.modellen]
        standaard = huidig.model if provider == huidig.provider else standaard_model(provider, taak)
        if opties:
            keuzes = [*opties, ANDER]
            index = keuzes.index(standaard) if standaard in opties else len(keuzes) - 1
            gekozen = st.selectbox(
                "Model", keuzes, index=index, key=f"model_{taak}_{provider}",
                format_func=lambda m, info=info: m if m == ANDER else info.model(m).label(),
            )
            model = st.text_input("Modelnaam", value=standaard if standaard not in opties else "", key=f"vrij_{taak}_{provider}") if gekozen == ANDER else gekozen
        else:
            plaats = "deploymentnaam uit Azure AI Foundry" if info.model_is_deployment else "bv. qwen3:8b of mistral-small3.2"
            model = st.text_input(
                "Deploymentnaam" if info.model_is_deployment else "Modelnaam", value=standaard, placeholder=plaats, key=f"vrij_{taak}_{provider}"
            )

        m = info.model(model)
        if m:
            # \$ voorkomt dat Streamlit-markdown de bedragen als formule leest
            prijs = f"\\${m.prijs_in:g} in / \\${m.prijs_uit:g} uit per 1M tokens" if m.prijs_in is not None else ""
            gratis = " · gratis tier" if m.gratis else ""
            st.caption(f"{prijs}{gratis} · {m.status}  \n{m.opmerking}")
        if taak == "documenten" and not info.pdf:
            st.caption("Deze aanbieder leest PDF's niet zelf: de app stuurt de tekst uit de PDF mee. Gescande PDF's lukken dan niet.")
        if provider == "mistral" and taak == "documenten":
            st.caption(f"PDF's en foto's gaan eerst door {inst.mistral_ocr_model} (OCR) en dan als tekst naar dit model.")

        b1, b2 = st.columns(2)
        gewijzigd = (provider, model) != (huidig.provider, huidig.model)
        if b1.button("Bewaar", key=f"bewaar_{taak}", type="primary" if gewijzigd else "secondary", disabled=not gewijzigd or not model):
            db.bewaar_instelling(con, f"ai_{taak}_provider", provider)
            db.bewaar_instelling(con, f"ai_{taak}_model", model.strip())
            st.toast(f"{TITELS[taak]}: {info.label} · {model}", icon=":material/check:")
            st.rerun()
        if b2.button("Test verbinding", key=f"test_{taak}", disabled=gewijzigd or not inst.ai_actief(taak),
                     help="Bewaar eerst je keuze" if gewijzigd else None):
            with st.spinner("Even proberen…"):
                ok, bericht = controleer_verbinding(inst, taak)
            (st.success if ok else st.error)(bericht)

if st.button("Terug naar de instellingen uit .env", icon=":material/restart_alt:", type="tertiary"):
    for taak in TAKEN:
        db.bewaar_instelling(con, f"ai_{taak}_provider", None)
        db.bewaar_instelling(con, f"ai_{taak}_model", None)
    st.rerun()

# --- Kostenraming --------------------------------------------------------------------------
with st.expander("Wat kost dit ongeveer per maand?", icon=":material/euro:"):
    st.caption(
        "Ruwe schatting met de catalogusprijzen. Aannames: een afschrift van 3 pagina's ≈ 3.000 tokens in en 3.000 uit; "
        "een factuur ≈ 1.500 in en 400 uit; per gecategoriseerde transactie ≈ 90 in en 30 uit."
    )
    c1, c2, c3 = st.columns(3)
    n_afschrift = c1.number_input("PDF-afschriften per maand", 0, 500, 4)
    n_factuur = c2.number_input("Facturen per maand", 0, 500, 10)
    n_txn = c3.number_input("Transacties naar AI per maand", 0, 10000, 100)
    rijen = []
    for taak, tokens_in, tokens_uit, paginas in (
        ("documenten", n_afschrift * 3000 + n_factuur * 1500, n_afschrift * 3000 + n_factuur * 400, n_afschrift * 3 + n_factuur),
        ("categorisatie", n_txn * 90, n_txn * 30, 0),
    ):
        keuze = inst.ai(taak)
        m = cat[keuze.provider].model(keuze.model)
        if m is None or m.prijs_in is None:
            rijen.append({"Taak": TITELS[taak], "Model": str(keuze), "Geschat per maand (USD)": None})
            continue
        kost = tokens_in / 1e6 * m.prijs_in + tokens_uit / 1e6 * m.prijs_uit
        if keuze.provider == "mistral" and taak == "documenten":
            kost += paginas * 4 / 1000  # Mistral OCR
        rijen.append({"Taak": TITELS[taak], "Model": str(keuze), "Geschat per maand (USD)": round(kost, 4)})
    st.dataframe(pd.DataFrame(rijen), hide_index=True, column_config={"Geschat per maand (USD)": st.column_config.NumberColumn(format="$ %.4f")})

# --- Vergelijking ----------------------------------------------------------------------------
st.subheader("Modellen vergelijken")
st.caption("Prijzen in USD per 1 miljoen tokens, standaardtarief, stand 26 oktober 2026. Controleer de actuele prijs bij de aanbieder.")
filter_ = st.multiselect("Aanbieders", [p for p in PROVIDERS if cat[p].modellen], default=["gemini"], format_func=lambda p: cat[p].label)
tabel = pd.DataFrame(
    [
        {
            "Aanbieder": cat[p].label, "Model": m.id, "Input": m.prijs_in, "Output": m.prijs_uit,
            "Gratis tier": {True: "ja", False: "nee"}.get(m.gratis, "–"), "Goed voor": m.rol, "Status": m.status, "Opmerking": m.opmerking,
        }
        for p in (filter_ or PROVIDERS) for m in cat[p].modellen
    ]
)
if not tabel.empty:
    st.dataframe(
        tabel, hide_index=True,
        column_config={
            "Input": st.column_config.NumberColumn(format="$ %.2f"),
            "Output": st.column_config.NumberColumn(format="$ %.2f"),
            "Opmerking": st.column_config.TextColumn(width="large"),
        },
    )

# --- Live ophalen ----------------------------------------------------------------------------
with st.expander("Welke modellen kan mijn sleutel gebruiken? (live opvragen)", icon=":material/travel_explore:"):
    klare = [p for p in PROVIDERS if inst.provider_klaar(p)[0]]
    if not klare:
        st.caption("Nog geen aanbieder ingesteld in .env.")
    else:
        p = st.selectbox("Aanbieder", klare, format_func=lambda c: cat[c].label, key="live_aanbieder")
        if st.button("Vraag op", key="live_knop"):
            try:
                with st.spinner("Bezig…"):
                    st.session_state.live = (p, lijst_modellen(inst, p))
            except AIFout as e:
                st.error(str(e))
        if st.session_state.get("live", (None,))[0] == p:
            modellen = st.session_state.live[1]
            st.write(f"{len(modellen)} modellen. Gebruik er een via **Ander model…** hierboven.")
            st.dataframe(pd.DataFrame({"Model": modellen}), hide_index=True, height=300)

# --- Sleutels en privacy ------------------------------------------------------------------------
st.subheader("Aanbieders, sleutels en privacy")
sleutels = pd.DataFrame(
    [
        {
            "Aanbieder": cat[p].label,
            "Status": "✓ klaar" if inst.provider_klaar(p)[0] else "✗ " + inst.provider_klaar(p)[1],
            "Privacy": cat[p].privacy,
            "In .env": " + ".join([SLEUTEL_VARIABELEN[p][0] + (" (optioneel)" if cat[p].sleutel_optioneel else ""), *cat[p].extra_omgeving]),
            "Leest PDF zelf": "ja" if cat[p].pdf else "nee (tekst)",
            "Foto's": "ja" if cat[p].afbeeldingen else "nee",
            "Meer info": cat[p].link,
        }
        for p in PROVIDERS
    ]
)
st.dataframe(
    sleutels, hide_index=True,
    column_config={"Privacy": st.column_config.TextColumn(width="large"), "Meer info": st.column_config.LinkColumn(display_text="docs")},
)
st.caption(
    "Na het aanpassen van `.env` moet je de app herstarten. Met `LLM_PDF_ALS_TEKST=1` stuurt de app bij elke aanbieder "
    "de tekst uit de PDF in plaats van het bestand zelf (goedkoper, maar zonder gescande PDF's)."
)
