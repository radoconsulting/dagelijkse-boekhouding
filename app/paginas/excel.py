import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from gedeeld import instellingen, verbinding

from boekhouding.excel import exporteer_excel, importeer_excel

con = verbinding()
inst = instellingen()
st.title("Excel")
st.write(
    "Werk je liever in Excel? Exporteer alles naar één werkmap, pas categorieën, notities en regels aan in de "
    "kolommen met een **gele kop**, bewaar en lees het bestand hier terug in. Het tabblad *Overzicht* rekent met "
    "formules, dus het volgt je aanpassingen meteen."
)

links, rechts = st.columns(2, gap="large")
with links:
    st.subheader("1. Exporteren")
    if st.button("Maak Excel-bestand", type="primary", icon=":material/file_export:"):
        pad = inst.export_dir / f"boekhouding_{datetime.now():%Y%m%d_%H%M}.xlsx"
        exporteer_excel(con, pad)
        st.session_state.excel_pad = str(pad)
    if st.session_state.get("excel_pad"):
        pad = Path(st.session_state.excel_pad)
        st.success(f"Bewaard in {pad}")
        st.download_button(
            "Download", pad.read_bytes(), file_name=pad.name, icon=":material/download:",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with rechts:
    st.subheader("2. Aanpassingen inlezen")
    st.caption("Wat je in Excel aanpast, telt als handmatige keuze en wordt nooit door regels of AI overschreven.")
    bestand = st.file_uploader("Bewerkte werkmap", type=["xlsx"])
    if st.button("Lees in", disabled=bestand is None, icon=":material/file_upload:"):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(bestand.getvalue())
        try:
            v = importeer_excel(con, tmp.name)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        st.success(
            f"{v.transacties_gewijzigd} transacties bijgewerkt · {v.categorieen_nieuw} nieuwe en {v.categorieen_gewijzigd} "
            f"gewijzigde categorieën · {v.regels_nieuw} nieuwe en {v.regels_gewijzigd} gewijzigde regels · "
            f"{v.rekeningen_gewijzigd} rekeningen · {v.opnieuw_gecategoriseerd} transacties opnieuw gecategoriseerd"
        )
        for f in v.fouten:
            st.warning(f)
