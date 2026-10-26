"""Categoriseren: interne overboekingen, regels (eerst) en AI (voor wat overblijft).

Volgorde en voorrang:
1. Handmatige keuzes (app of Excel) en categorieën uit een gekoppelde factuur worden nooit overschreven.
2. Een overboeking naar of van een eigen rekening wordt 'Overboekingen / Eigen rekeningen'.
3. De eerste actieve regel die past (laagste prioriteit eerst) bepaalt de categorie.
4. Wat dan nog leeg is, kan het gekozen AI-model voorstellen, met een zekerheidsscore.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

import duckdb
import pandas as pd

from .db import (
    BRON_AI,
    BRON_INTERN,
    BRON_REGEL,
    CATEGORIE_INTERN,
    VASTE_BRONNEN,
    categorie_labels,
    een,
    eigen_ibans,
)
from .llm import CategorieVoorstellen, vraag_json
from .settings import Instellingen
from .utils import normaliseer_iban, zonder_accenten

VELDEN = ("alles", "tegenpartij_naam", "tegenpartij_iban", "omschrijving", "mededeling")
TEKENS = ("beide", "in", "uit")


@dataclass
class Regel:
    regel_id: int | None
    patroon: str
    categorie_id: str
    veld: str = "alles"
    teken: str = "beide"
    prioriteit: int = 100
    naam: str | None = None
    min_bedrag: Decimal | None = None
    max_bedrag: Decimal | None = None
    rekening_iban: str | None = None
    _re: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        if self.veld not in VELDEN:
            raise ValueError(f"Onbekend veld '{self.veld}'. Kies uit: {', '.join(VELDEN)}")
        if self.teken not in TEKENS:
            raise ValueError(f"Onbekend teken '{self.teken}'. Kies uit: {', '.join(TEKENS)}")
        patroon = zonder_accenten(self.patroon or "")
        if self.veld == "tegenpartij_iban":
            patroon = re.sub(r"\s", "", patroon)
        try:
            self._re = re.compile(patroon, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"Ongeldig patroon '{self.patroon}': {e}") from e
        self.rekening_iban = normaliseer_iban(self.rekening_iban)

    def past(self, t: dict, cache: dict | None = None) -> bool:
        """cache: per transactie hergebruikte tekst per veld en bedrag (scheelt veel werk bij duizenden transacties)."""
        cache = {} if cache is None else cache
        if "_bedrag" not in cache:
            cache["_bedrag"] = Decimal(str(t["bedrag"]))
        bedrag = cache["_bedrag"]
        if self.teken == "in" and bedrag < 0:
            return False
        if self.teken == "uit" and bedrag >= 0:
            return False
        if self.min_bedrag is not None and abs(bedrag) < Decimal(str(self.min_bedrag)):
            return False
        if self.max_bedrag is not None and abs(bedrag) > Decimal(str(self.max_bedrag)):
            return False
        if self.rekening_iban and normaliseer_iban(t.get("rekening_iban")) != self.rekening_iban:
            return False
        if self.veld not in cache:
            cache[self.veld] = veld_tekst(t, self.veld)
        return bool(self._re.search(cache[self.veld]))


def veld_tekst(t: dict, veld: str) -> str:
    if veld == "tegenpartij_iban":
        return normaliseer_iban(t.get("tegenpartij_iban")) or ""
    delen = ("tegenpartij_naam", "omschrijving", "mededeling") if veld == "alles" else (veld,)
    tekst = " ".join(str(t[d]) for d in delen if t.get(d) and not pd.isna(t.get(d)))
    return zonder_accenten(tekst).lower()


def laad_regels(con: duckdb.DuckDBPyConnection) -> list[Regel]:
    rijen = con.execute(
        """SELECT regel_id, patroon, categorie_id, veld, teken, prioriteit, naam, min_bedrag, max_bedrag, rekening_iban
           FROM regels WHERE actief ORDER BY prioriteit, regel_id"""
    ).fetchall()
    regels = []
    for r in rijen:
        try:
            regels.append(Regel(*r))
        except ValueError:
            continue  # een kapotte regel mag de rest niet tegenhouden
    return regels


def _kandidaten(con, alleen_ongecategoriseerd: bool, txn_ids: list[str] | None) -> pd.DataFrame:
    vaste = ", ".join(f"'{b}'" for b in VASTE_BRONNEN)
    q = f"""SELECT txn_id, rekening_iban, bedrag, tegenpartij_naam, tegenpartij_iban, omschrijving, mededeling,
                   categorie_id, categorie_bron, regel_id
            FROM transacties
            WHERE (categorie_bron IS NULL OR categorie_bron NOT IN ({vaste}))"""
    params: list = []
    if alleen_ongecategoriseerd:
        q += " AND categorie_id IS NULL"
    if txn_ids is not None:
        q += " AND list_contains(?::VARCHAR[], txn_id)"
        params.append(list(txn_ids))
    return con.execute(q, params).df()


def _schrijf_updates(con, updates: list[dict]) -> int:
    if not updates:
        return 0
    updates = [list(u) + [None] * (6 - len(u)) for u in updates]
    df = pd.DataFrame(updates, columns=["txn_id", "categorie_id", "categorie_bron", "regel_id", "zekerheid", "ai_model"])
    df["ai_model"] = df["ai_model"].astype("object")
    df["regel_id"] = df["regel_id"].astype("Int64")
    con.register("_upd", df)
    try:
        con.execute(
            """UPDATE transacties AS t SET
                   categorie_id = u.categorie_id,
                   categorie_bron = u.categorie_bron,
                   regel_id = u.regel_id,
                   zekerheid = u.zekerheid,
                   ai_model = u.ai_model,
                   gewijzigd_op = current_timestamp
               FROM _upd AS u WHERE t.txn_id = u.txn_id"""
        )
    finally:
        con.unregister("_upd")
    return len(df)


def pas_regels_toe(
    con: duckdb.DuckDBPyConnection,
    alleen_ongecategoriseerd: bool = False,
    txn_ids: list[str] | None = None,
) -> int:
    """Pas interne-overboekingsdetectie en regels toe. Geeft het aantal gewijzigde transacties."""
    df = _kandidaten(con, alleen_ongecategoriseerd, txn_ids)
    if df.empty:
        return 0
    regels = laad_regels(con)
    eigen = eigen_ibans(con)
    updates = []
    for t in df.to_dict("records"):
        tegen = normaliseer_iban(t.get("tegenpartij_iban"))
        nieuw = None
        if tegen and tegen in eigen and tegen != normaliseer_iban(t["rekening_iban"]):
            nieuw = (CATEGORIE_INTERN, BRON_INTERN, None, 1.0)
        else:
            cache: dict = {}
            for r in regels:
                if r.past(t, cache):
                    nieuw = (r.categorie_id, BRON_REGEL, r.regel_id, 1.0)
                    break
        huidig = (t["categorie_id"], t["categorie_bron"])
        if nieuw is None:
            if t["categorie_bron"] in (BRON_REGEL, BRON_INTERN):
                updates.append([t["txn_id"], None, None, None, None])  # regel bestaat niet meer
            continue
        huidige_regel = t.get("regel_id")
        huidige_regel = None if huidige_regel is None or pd.isna(huidige_regel) else int(huidige_regel)
        if (nieuw[0], nieuw[1]) != huidig or (nieuw[1] == BRON_REGEL and huidige_regel != nieuw[2]):
            updates.append([t["txn_id"], *nieuw])
    return _schrijf_updates(con, updates)


SYSTEEM_CATEGORISATIE = """Je categoriseert banktransacties van een Belgisch gezin.
Kies voor elke transactie exact één categorie_id uit de gegeven lijst.
- Positief bedrag = geld komt binnen, negatief = geld gaat buiten. Kies een inkomstcategorie enkel bij positieve bedragen,
  tenzij het duidelijk een terugbetaling van een aankoop is (dan de categorie van die aankoop).
- Gebruik de tegenpartij, de omschrijving (bij kaartbetalingen staat de handelaar daar vaak in) en de mededeling.
- zekerheid: 0.0 tot 1.0. Bij twijfel kies 'diversen_overige_uitgaven' met lage zekerheid."""


def categoriseer_met_ai(
    con: duckdb.DuckDBPyConnection,
    inst: Instellingen,
    max_aantal: int = 300,
    batch: int = 50,
    txn_ids: list[str] | None = None,
    voortgang=None,
) -> int:
    """Laat het AI-model voor categorisatie een categorie voorstellen voor transacties zonder categorie.
    voortgang(gedaan, totaal) wordt na elke groep opgeroepen (voor een voortgangsbalk)."""
    q = """SELECT txn_id, boekingsdatum, bedrag, tegenpartij_naam, omschrijving, mededeling
           FROM transacties WHERE categorie_id IS NULL AND coalesce(categorie_bron, '') <> 'gesplitst'"""
    params: list = []
    if txn_ids is not None:
        q += " AND list_contains(?::VARCHAR[], txn_id)"
        params.append(list(txn_ids))
    q += " ORDER BY boekingsdatum DESC LIMIT ?"
    params.append(max_aantal)
    df = con.execute(q, params).df()
    if df.empty:
        return 0

    labels = categorie_labels(con)
    soorten = dict(con.execute("SELECT categorie_id, soort FROM categorieen").fetchall())
    cat_lijst = "\n".join(f"{cid} | {label} | {soorten.get(cid)}" for cid, label in labels.items())

    totaal = 0
    for start in range(0, len(df), batch):
        deel = df.iloc[start : start + batch]
        regels = []
        for i, t in enumerate(deel.to_dict("records")):
            regels.append(
                json.dumps(
                    {
                        "id": i,
                        "datum": str(t["boekingsdatum"]),
                        "bedrag": float(t["bedrag"]),
                        "tegenpartij": (t["tegenpartij_naam"] or "")[:80] if isinstance(t["tegenpartij_naam"], str) else "",
                        "omschrijving": (t["omschrijving"] or "")[:200] if isinstance(t["omschrijving"], str) else "",
                        "mededeling": (t["mededeling"] or "")[:140] if isinstance(t["mededeling"], str) else "",
                    },
                    ensure_ascii=False,
                )
            )
        opdracht = (
            "Categorieën (categorie_id | label | soort):\n"
            f"{cat_lijst}\n\nTransacties (één JSON-object per regel):\n" + "\n".join(regels)
        )
        antwoord = vraag_json(inst, "categorisatie", CategorieVoorstellen, opdracht, systeem=SYSTEEM_CATEGORISATIE)
        updates = []
        for v in antwoord.voorstellen:
            if 0 <= v.id < len(deel) and v.categorie_id in labels:
                zekerheid = max(0.0, min(1.0, float(v.zekerheid)))
                updates.append([deel.iloc[v.id]["txn_id"], v.categorie_id, BRON_AI, None, zekerheid, str(inst.ai("categorisatie"))])
        totaal += _schrijf_updates(con, updates)
        if voortgang:
            voortgang(min(start + batch, len(df)), len(df))
    return totaal


# --- Regels beheren ---------------------------------------------------------------


def maak_regel(
    con: duckdb.DuckDBPyConnection,
    patroon: str,
    categorie_id: str,
    veld: str = "alles",
    teken: str = "beide",
    prioriteit: int = 50,
    naam: str | None = None,
    bron: str = "handmatig",
) -> int:
    Regel(None, patroon, categorie_id, veld=veld, teken=teken)  # valideert
    if not een(con.execute("SELECT 1 FROM categorieen WHERE categorie_id = ?", [categorie_id])):
        raise ValueError(f"Onbekende categorie '{categorie_id}'")
    (regel_id,) = een(con.execute(
        """INSERT INTO regels (prioriteit, naam, veld, patroon, teken, categorie_id, bron)
           VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING regel_id""",
        [prioriteit, naam, veld, patroon, teken, categorie_id, bron],
    ))
    return regel_id


def regel_treffers(con: duckdb.DuckDBPyConnection, regel: Regel, limiet: int = 200) -> pd.DataFrame:
    df = con.execute(
        """SELECT txn_id, boekingsdatum, bedrag, rekening_iban, tegenpartij_naam, tegenpartij_iban,
                  omschrijving, mededeling, categorie_label
           FROM v_transacties ORDER BY boekingsdatum DESC"""
    ).df()
    if df.empty:
        return df
    masker = [regel.past(t) for t in df.to_dict("records")]
    return df[masker].head(limiet)


def stel_regels_voor(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Tegenpartijen die je telkens handmatig in dezelfde categorie stopt, maar waarvoor nog geen regel bestaat."""
    df = con.execute(
        """SELECT txn_id, rekening_iban, bedrag, tegenpartij_naam, tegenpartij_iban, omschrijving, mededeling,
                  categorie_id
           FROM transacties
           WHERE categorie_bron IN ('handmatig', 'excel')
             AND (tegenpartij_iban IS NOT NULL OR tegenpartij_naam IS NOT NULL)"""
    ).df()
    kolommen = ["veld", "patroon", "categorie_id", "categorie", "aantal", "voorbeeld"]
    if df.empty:
        return pd.DataFrame(columns=kolommen)
    df["sleutel"] = df.apply(
        lambda r: ("tegenpartij_iban", normaliseer_iban(r["tegenpartij_iban"]))
        if isinstance(r["tegenpartij_iban"], str) and r["tegenpartij_iban"]
        else ("tegenpartij_naam", zonder_accenten(str(r["tegenpartij_naam"])).lower().strip()),
        axis=1,
    )
    regels = laad_regels(con)
    labels = categorie_labels(con, alleen_actief=False)
    voorstellen = []
    for (veld, waarde), groep in df.groupby("sleutel"):
        if groep["categorie_id"].nunique() != 1 or not waarde:
            continue
        cid = groep["categorie_id"].iloc[0]
        voorbeeld = groep.iloc[0].to_dict()
        bestaande = next((r for r in regels if r.past(voorbeeld)), None)
        if bestaande is not None and bestaande.categorie_id == cid:
            continue
        patroon = waarde if veld == "tegenpartij_iban" else "^" + re.escape(waarde) + "$"
        voorstellen.append(
            [veld, patroon, cid, labels.get(cid, cid), len(groep), voorbeeld.get("tegenpartij_naam") or waarde]
        )
    return pd.DataFrame(voorstellen, columns=kolommen).sort_values("aantal", ascending=False, ignore_index=True)
