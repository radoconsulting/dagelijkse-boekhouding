"""Startpunt van de app:  streamlit run app/main.py  (of start.bat)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Dagelijkse boekhouding", page_icon=":material/account_balance_wallet:", layout="wide")

from gedeeld import zijbalk  # noqa: E402

HIER = Path(__file__).resolve().parent / "paginas"
paginas = [
    st.Page(HIER / "overzicht.py", title="Overzicht", icon=":material/dashboard:", default=True),
    st.Page(HIER / "importeren.py", title="Importeren", icon=":material/upload_file:"),
    st.Page(HIER / "transacties.py", title="Transacties", icon=":material/receipt_long:"),
    st.Page(HIER / "budgetten.py", title="Budgetten", icon=":material/savings:"),
    st.Page(HIER / "vaste_lasten.py", title="Vaste lasten & vooruitzicht", icon=":material/event_repeat:"),
    st.Page(HIER / "facturen.py", title="Facturen", icon=":material/description:"),
    st.Page(HIER / "regels.py", title="Categorieën & regels", icon=":material/rule:"),
    st.Page(HIER / "excel.py", title="Excel", icon=":material/table_view:"),
    st.Page(HIER / "ai_modellen.py", title="AI-modellen", icon=":material/auto_awesome:"),
]
navigatie = st.navigation(paginas)
zijbalk()
navigatie.run()
