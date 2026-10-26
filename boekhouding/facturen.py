"""Facturen inlezen (AI, met een eenvoudige lokale terugval) en koppelen aan betalingen."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from difflib import SequenceMatcher

import duckdb

from .db import BRON_AI, BRON_FACTUUR, BRON_REGEL, categorie_labels, een
from .llm import AIFout, FactuurGegevens, aanbieder, vraag_json
from .models import ImportFout
from .settings import Instellingen
from .utils import (
    normaliseer_iban,
    ogm_cijfers,
    parse_bedrag,
    parse_datum,
    pdf_tekst,
    schoon,
    vind_iban,
    vind_ogm,
    zonder_accenten,
)

MIME = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic"}

SYSTEEM_FACTUUR = """Je leest facturen, rekeningen en betalingsuitnodigingen voor een Belgisch gezin.
Geef de gegevens in het gevraagde JSON-formaat. Regels:
- is_factuur: false als het document geen factuur, rekening of betalingsuitnodiging is.
- bedrag_totaal: het totaal te betalen bedrag inclusief btw (positief getal). Bij een creditnota negatief.
- datums als YYYY-MM-DD.
- leverancier_iban: het rekeningnummer waarop betaald moet worden, zonder spaties.
- gestructureerde_mededeling: in de vorm +++123/4567/89012+++ als die vermeld is.
- omschrijving: één korte zin over wat er gefactureerd wordt (bv. 'Elektriciteit voorschot oktober').
- categorie_id: kies de best passende uit de lijst, of null.
- Verzin niets: ontbrekende velden zijn null."""


def lees_factuur(inhoud: bytes, bestandsnaam: str, inst: Instellingen, con: duckdb.DuckDBPyConnection) -> tuple[dict, str]:
    """Geef (gegevens, extractiemethode). Gebruikt de AI voor documenten als die ingesteld is, anders tekstherkenning."""
    ext = "." + bestandsnaam.rsplit(".", 1)[-1].lower() if "." in bestandsnaam else ".pdf"
    if inst.ai_actief("documenten"):
        labels = categorie_labels(con)
        opdracht = "Categorieën (categorie_id | label):\n" + "\n".join(f"{k} | {v}" for k, v in labels.items())
        try:
            g = vraag_json(
                inst,
                "documenten",
                FactuurGegevens,
                opdracht + "\n\nLees de bijgevoegde factuur.",
                bijlage=inhoud,
                mime_type=MIME.get(ext, "application/pdf"),
                systeem=SYSTEEM_FACTUUR,
                bestandsnaam=bestandsnaam,
            )
        except AIFout as e:
            raise ImportFout(str(e)) from e
        if not g.is_factuur:
            raise ImportFout(f"'{bestandsnaam}' lijkt geen factuur te zijn (volgens {inst.ai('documenten')}).")
        gegevens = g.model_dump()
        if gegevens.get("categorie_id") not in labels:
            gegevens["categorie_id"] = None
        return gegevens, str(inst.ai("documenten"))

    if ext != ".pdf":
        raise ImportFout(
            "Foto's van facturen kunnen enkel met AI gelezen worden. Stel een aanbieder in op de pagina AI-modellen "
            f"({aanbieder(inst.ai('documenten').provider).label}: {inst.provider_klaar(inst.ai('documenten').provider)[1]})."
        )
    tekst = pdf_tekst(inhoud, max_paginas=3)
    if not tekst.strip():
        raise ImportFout(
            f"'{bestandsnaam}' bevat geen leesbare tekst (gescande PDF?). Met een AI-aanbieder kan dit wel gelezen worden."
        )
    return lokale_extractie(tekst), "lokaal"


_BEDRAG = r"(-?\d{1,3}(?:[.\s]\d{3})*,\d{2}|-?\d+[.,]\d{2})"
_DATUM = r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})"


def _zoek(patronen: list[str], tekst: str, groep: int = 1) -> str | None:
    for p in patronen:
        m = re.search(p, tekst, re.IGNORECASE)
        if m:
            return m.group(groep)
    return None


def lokale_extractie(tekst: str) -> dict:
    """Ruwe extractie zonder AI: OGM, IBAN, totaalbedrag, datums en factuurnummer."""
    totaal = None
    for regel in tekst.splitlines():
        if re.search(r"te betalen|totaal|total|a payer|à payer|montant d[uû]|eindtotaal", regel, re.IGNORECASE):
            bedragen = [parse_bedrag(b) for b in re.findall(_BEDRAG, regel)]
            bedragen = [b for b in bedragen if b is not None]
            if bedragen:
                totaal = bedragen[-1]
    btw = re.search(r"\bBE\s?0?\d{3}[.\s]?\d{3}[.\s]?\d{3}\b", tekst)
    eerste_regel = next((r.strip() for r in tekst.splitlines() if len(r.strip()) > 2), None)
    return {
        "leverancier": schoon(eerste_regel)[:80] if eerste_regel else None,
        "leverancier_btw": re.sub(r"\s", "", btw.group(0)) if btw else None,
        "leverancier_iban": vind_iban(tekst),
        "factuurnummer": _zoek([r"factuur\s*(?:nummer|nr\.?|n°)\s*[:#]?\s*([A-Z0-9\-/]+)", r"invoice\s*(?:number|no\.?)\s*[:#]?\s*([A-Z0-9\-/]+)", r"facture\s*(?:n°|numero|no)\s*[:#]?\s*([A-Z0-9\-/]+)"], tekst),
        "factuurdatum": _zoek([rf"factuurdatum\s*:?\s*{_DATUM}", rf"datum factuur\s*:?\s*{_DATUM}", rf"date de (?:la )?facture\s*:?\s*{_DATUM}", rf"datum\s*:?\s*{_DATUM}"], tekst),
        "vervaldatum": _zoek([rf"vervaldatum\s*:?\s*{_DATUM}", rf"(?:uiterlijk|te betalen) (?:op|voor)\s*:?\s*{_DATUM}", rf"[ée]ch[ée]ance\s*:?\s*{_DATUM}"], tekst),
        "bedrag_totaal": float(totaal) if totaal is not None else None,
        "bedrag_btw": None,
        "munt": "EUR",
        "gestructureerde_mededeling": vind_ogm(tekst),
        "omschrijving": None,
        "categorie_id": None,
    }


def bewaar_factuur(con: duckdb.DuckDBPyConnection, factuur_id: str, bestandsnaam: str, g: dict, extractie: str) -> None:
    con.execute(
        """INSERT OR REPLACE INTO facturen
           (factuur_id, bestandsnaam, leverancier, leverancier_btw, leverancier_iban, factuurnummer,
            factuurdatum, vervaldatum, bedrag_totaal, bedrag_btw, munt, gestructureerde_mededeling,
            omschrijving, categorie_id, status, extractie, ruwe_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        [
            factuur_id,
            bestandsnaam,
            schoon(g.get("leverancier")),
            schoon(g.get("leverancier_btw")),
            normaliseer_iban(g.get("leverancier_iban")),
            schoon(g.get("factuurnummer")),
            parse_datum(g.get("factuurdatum")),
            parse_datum(g.get("vervaldatum")),
            parse_bedrag(g.get("bedrag_totaal")),
            parse_bedrag(g.get("bedrag_btw")),
            (g.get("munt") or "EUR").upper()[:3],
            vind_ogm(g.get("gestructureerde_mededeling")),
            schoon(g.get("omschrijving")),
            g.get("categorie_id"),
            extractie,
            json.dumps(g, ensure_ascii=False, default=str),
        ],
    )


def _woorden(s: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", zonder_accenten(s or "").lower())} - {"bvba", "sprl", "comm", "belgium", "belgie"}


def _naam_lijkt(leverancier: str | None, *teksten: str | None) -> bool:
    if not leverancier:
        return False
    lev = zonder_accenten(leverancier).lower()
    for t in teksten:
        if not t:
            continue
        t2 = zonder_accenten(t).lower()
        if SequenceMatcher(None, lev, t2).ratio() >= 0.6 or (_woorden(lev) & _woorden(t2)):
            return True
    return False


def koppel_facturen(con: duckdb.DuckDBPyConnection) -> int:
    """Koppel openstaande facturen aan betalingen: eerst via OGM, anders via bedrag + IBAN of naam."""
    facturen = con.execute(
        """SELECT factuur_id, leverancier, leverancier_iban, factuurdatum, vervaldatum, bedrag_totaal,
                  gestructureerde_mededeling, categorie_id
           FROM facturen WHERE txn_id IS NULL AND bedrag_totaal IS NOT NULL"""
    ).fetchall()
    gekoppeld = 0
    for fid, lev, lev_iban, fdatum, vdatum, totaal, ogm, cat in facturen:
        txn = None
        cijfers = ogm_cijfers(ogm)
        if cijfers:
            rij = een(con.execute(
                """SELECT txn_id FROM transacties
                   WHERE factuur_id IS NULL AND regexp_replace(coalesce(gestructureerde_mededeling, ''), '\\D', '', 'g') = ?
                   ORDER BY boekingsdatum LIMIT 1""",
                [cijfers],
            ))
            txn = rij[0] if rij else None
        if txn is None:
            begin = (fdatum or vdatum) - timedelta(days=7) if (fdatum or vdatum) else None
            einde = (vdatum or fdatum) + timedelta(days=60) if (fdatum or vdatum) else None
            kandidaten = con.execute(
                """SELECT txn_id, tegenpartij_iban, tegenpartij_naam, omschrijving, mededeling
                   FROM transacties
                   WHERE factuur_id IS NULL AND bedrag = -?
                     AND (? IS NULL OR boekingsdatum BETWEEN ? AND ?)
                   ORDER BY boekingsdatum""",
                [totaal, begin, begin, einde],
            ).fetchall()
            for tid, t_iban, t_naam, t_oms, t_med in kandidaten:
                if (lev_iban and normaliseer_iban(t_iban) == lev_iban) or _naam_lijkt(lev, t_naam, t_oms, t_med):
                    txn = tid
                    break
        if txn is None:
            continue
        con.execute("UPDATE facturen SET txn_id = ?, status = 'betaald' WHERE factuur_id = ?", [txn, fid])
        con.execute("UPDATE transacties SET factuur_id = ?, gewijzigd_op = current_timestamp WHERE txn_id = ?", [fid, txn])
        if cat:
            con.execute(
                """UPDATE transacties SET categorie_id = ?, categorie_bron = ?, zekerheid = 1.0, regel_id = NULL
                   WHERE txn_id = ? AND (categorie_bron IS NULL OR categorie_bron IN (?, ?))""",
                [cat, BRON_FACTUUR, txn, BRON_REGEL, BRON_AI],
            )
        gekoppeld += 1
    return gekoppeld
