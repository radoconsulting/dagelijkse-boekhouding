"""Kredietkaartafrekeningen.

Probleem: op de zichtrekening staat per maand één bedrag ("afrekening kredietkaart"), terwijl de echte
aankopen enkel op de afrekening van de kaart staan. Telt de app beide, dan tel je dubbel; telt ze enkel het
totaal, dan zie je niet waaraan het geld opging.

Aanpak:
- De afrekening (PDF) wordt door de AI voor documenten gelezen.
- Elke aankoop komt op een virtuele rekening per kaart (rekeningen.soort = 'kredietkaart') en wordt gewoon
  gecategoriseerd (regels, AI, handmatig).
- Op die kaartrekening komt ook een positieve boeking "Afrekening via zichtrekening" ter grootte van het totaal.
- De afrekening op de zichtrekening wordt gekoppeld (bedrag + datumvenster, bij voorkeur met een woord als
  'kredietkaart', 'mastercard' of 'visa') en telt dan als interne overboeking (bron 'kaart').
Zolang de afrekening niet ingelezen is, valt het totaalbedrag onder 'Diversen / Kredietkaart (zonder detail)'.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import duckdb

from .db import BRON_KAART, CATEGORIE_INTERN, een
from .llm import AIFout, KaartAfrekening, aanbieder, vraag_json
from .models import Afschrift, ImportFout, Transactie
from .settings import Instellingen
from .utils import normaliseer_iban, parse_bedrag, parse_datum, schoon, slug

OMSCHRIJVING_AFREKENING = "Afrekening via zichtrekening"
KAARTWOORDEN = re.compile(r"kredietkaart|credit ?card|mastercard|visa|amex|american express|carte de cr[eé]dit", re.I)

SYSTEEM_KAART = """Je leest de maandelijkse afrekening (uitgavenstaat) van een kredietkaart van een Belgische bank.
Geef de gegevens in het gevraagde JSON-formaat. Regels:
- verrichtingen: ALLE aankopen, terugbetalingen, kosten en interesten van deze afrekening, in volgorde.
  Neem het vorige saldo en betalingen/afrekeningen van de vorige periode NIET op als verrichting.
- bedrag in EUR: negatief voor aankopen, kosten en interesten; positief voor terugbetalingen of crediteringen.
- origineel_bedrag en originele_munt enkel bij betalingen in een andere munt.
- handelaar: de naam van de winkel of dienst zoals vermeld (zonder kaartnummer).
- totaal_te_betalen: het bedrag dat van de zichtrekening wordt afgehouden (positief getal).
- afrekeningsdatum: de datum waarop dat bedrag wordt afgehouden; anders de datum van de afrekening.
- kaart_laatste_cijfers: enkel de laatste 4 cijfers van het kaartnummer.
- datums als YYYY-MM-DD. Verzin niets: ontbrekende velden zijn null."""


@dataclass
class KaartImport:
    afschrift: Afschrift
    afrekening: dict = field(default_factory=dict)


def kaart_rekening_id(bank: str | None, laatste_cijfers: str | None, kaartnaam: str | None) -> str:
    b = (slug(bank or "kaart").replace("_", "")[:12] or "KAART").upper()
    cijfers = re.sub(r"\D", "", laatste_cijfers or "")[-4:]
    if not cijfers:
        cijfers = hashlib.sha1((kaartnaam or bank or "kaart").encode()).hexdigest()[:4].upper()
    return f"KAART-{b}-{cijfers}"


def naar_kaartimport(r: KaartAfrekening, bestandsnaam: str, extractie: str) -> KaartImport:
    kaart_id = kaart_rekening_id(r.bank, r.kaart_laatste_cijfers, r.kaartnaam)
    transacties: list[Transactie] = []
    waarschuwingen: list[str] = []
    for i, v in enumerate(r.verrichtingen, start=1):
        datum = parse_datum(v.boekingsdatum) or parse_datum(v.datum)
        bedrag = parse_bedrag(v.bedrag)
        if datum is None or bedrag is None:
            waarschuwingen.append(f"Kaartverrichting {i} overgeslagen: geen geldige datum of bedrag.")
            continue
        extra = []
        if v.land:
            extra.append(v.land)
        if v.originele_munt and v.originele_munt.upper() != "EUR" and v.origineel_bedrag is not None:
            extra.append(f"{abs(v.origineel_bedrag):.2f} {v.originele_munt.upper()}")
        transacties.append(
            Transactie(
                rekening_iban=kaart_id,
                boekingsdatum=datum,
                valutadatum=parse_datum(v.datum),
                bedrag=bedrag,
                tegenpartij_naam=schoon(v.handelaar),
                omschrijving=schoon(" · ".join(["Kredietkaart", *extra])),
                ruw=v.model_dump(),
            )
        )
    if not transacties:
        raise ImportFout(f"In '{bestandsnaam}' werden geen kaartverrichtingen gevonden.")

    som = sum((t.bedrag for t in transacties), Decimal("0"))
    totaal = parse_bedrag(r.totaal_te_betalen)
    if totaal is not None:
        totaal = abs(totaal)
    if totaal is None:
        controle = "n.v.t."
    elif som + totaal == 0:
        controle = "ok"
    else:
        controle = (
            f"afwijking: verrichtingen samen {-som:.2f}, af te rekenen {totaal:.2f} "
            "(vorig openstaand saldo, kosten of rente?)"
        )

    afrekeningsdatum = parse_datum(r.afrekeningsdatum) or parse_datum(r.periode_tot) or max(t.boekingsdatum for t in transacties)
    if totaal:
        transacties.append(
            Transactie(
                rekening_iban=kaart_id,
                boekingsdatum=afrekeningsdatum,
                bedrag=totaal,
                tegenpartij_naam=OMSCHRIJVING_AFREKENING,
                omschrijving="Afrekening kredietkaart",
                ruw={"afrekening": True},
            )
        )
    afrekening_id = hashlib.sha1(f"{kaart_id}|{afrekeningsdatum}|{totaal}".encode()).hexdigest()[:24]
    laatste = re.sub(r"\D", "", r.kaart_laatste_cijfers or "")[-4:]
    kaartnaam = schoon(r.kaartnaam) or "Kredietkaart"
    return KaartImport(
        afschrift=Afschrift(bron="kaart_pdf", bank=schoon(r.bank), transacties=transacties, saldo_controle=controle,
                            waarschuwingen=waarschuwingen),
        afrekening={
            "afrekening_id": afrekening_id,
            "bestandsnaam": bestandsnaam,
            "bank": schoon(r.bank),
            "kaart_rekening": kaart_id,
            "kaartnaam": f"{kaartnaam} ****{laatste}" if laatste else kaartnaam,
            "periode_van": parse_datum(r.periode_van),
            "periode_tot": parse_datum(r.periode_tot),
            "afrekeningsdatum": afrekeningsdatum,
            "totaal": totaal,
            "som_verrichtingen": -som,
            "controle": controle,
            "debet_rekening": normaliseer_iban(r.debet_rekening_iban),
            "extractie": extractie,
        },
    )


def lees_kaartafrekening(inhoud: bytes, bestandsnaam: str, inst: Instellingen) -> KaartImport:
    if not inst.ai_actief("documenten"):
        keuze = inst.ai("documenten")
        raise ImportFout(
            "Kredietkaartafrekeningen worden met AI gelezen, maar "
            f"{aanbieder(keuze.provider).label} is niet ingesteld ({inst.provider_klaar(keuze.provider)[1]}). "
            "Stel een aanbieder in op de pagina AI-modellen."
        )
    try:
        r = vraag_json(
            inst, "documenten", KaartAfrekening, "Extraheer deze kredietkaartafrekening in het gevraagde JSON-formaat.",
            bijlage=inhoud, mime_type="application/pdf", systeem=SYSTEEM_KAART, bestandsnaam=bestandsnaam,
        )
    except AIFout as e:
        raise ImportFout(str(e)) from e
    return naar_kaartimport(r, bestandsnaam, str(inst.ai("documenten")))


def bewaar_kaartafrekening(con: duckdb.DuckDBPyConnection, ki: KaartImport) -> None:
    a = ki.afrekening
    con.execute("INSERT INTO rekeningen (iban, bank) VALUES (?, ?) ON CONFLICT DO NOTHING", [a["kaart_rekening"], a["bank"]])
    con.execute(
        "UPDATE rekeningen SET soort = 'kredietkaart', naam = coalesce(naam, ?), bank = coalesce(bank, ?) WHERE iban = ?",
        [a["kaartnaam"], a["bank"], a["kaart_rekening"]],
    )
    con.execute(
        """INSERT INTO kaartafrekeningen (afrekening_id, bestandsnaam, bank, kaart_rekening, kaartnaam, periode_van,
               periode_tot, afrekeningsdatum, totaal, som_verrichtingen, controle, debet_rekening, extractie)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (afrekening_id) DO UPDATE SET bestandsnaam = excluded.bestandsnaam, controle = excluded.controle,
               som_verrichtingen = excluded.som_verrichtingen, extractie = excluded.extractie""",
        [a["afrekening_id"], a["bestandsnaam"], a["bank"], a["kaart_rekening"], a["kaartnaam"], a["periode_van"],
         a["periode_tot"], a["afrekeningsdatum"], a["totaal"], a["som_verrichtingen"], a["controle"],
         a["debet_rekening"], a["extractie"]],
    )
    if a["totaal"]:
        con.execute(
            """UPDATE transacties SET categorie_id = ?, categorie_bron = ?, kaart_afrekening_id = ?, regel_id = NULL,
                      zekerheid = 1.0, nagekeken = TRUE
               WHERE rekening_iban = ? AND boekingsdatum = ? AND bedrag = ? AND tegenpartij_naam = ?""",
            [CATEGORIE_INTERN, BRON_KAART, a["afrekening_id"], a["kaart_rekening"], a["afrekeningsdatum"], a["totaal"],
             OMSCHRIJVING_AFREKENING],
        )


def _koppel(con, afrekening_id: str, txn_id: str) -> None:
    con.execute(
        """UPDATE transacties SET categorie_id = ?, categorie_bron = ?, kaart_afrekening_id = ?, regel_id = NULL,
                  zekerheid = 1.0, gewijzigd_op = current_timestamp WHERE txn_id = ?""",
        [CATEGORIE_INTERN, BRON_KAART, afrekening_id, txn_id],
    )
    con.execute("UPDATE kaartafrekeningen SET betaald_txn_id = ? WHERE afrekening_id = ?", [txn_id, afrekening_id])


def koppel_kaartafrekeningen(con: duckdb.DuckDBPyConnection) -> int:
    """Zoek voor elke nog niet gekoppelde afrekening de betaling op een zichtrekening."""
    open_ = con.execute(
        """SELECT afrekening_id, afrekeningsdatum, totaal, debet_rekening FROM kaartafrekeningen
           WHERE betaald_txn_id IS NULL AND totaal IS NOT NULL AND totaal > 0"""
    ).fetchall()
    gekoppeld = 0
    for afr_id, datum, totaal, debet in open_:
        kandidaten = con.execute(
            """SELECT t.txn_id, t.rekening_iban,
                      concat_ws(' ', t.tegenpartij_naam, t.omschrijving, t.mededeling) AS tekst
               FROM transacties t LEFT JOIN rekeningen r ON r.iban = t.rekening_iban
               WHERE coalesce(r.soort, 'zicht') <> 'kredietkaart'
                 AND t.kaart_afrekening_id IS NULL
                 AND t.bedrag = -?
                 AND t.boekingsdatum BETWEEN ? AND ?
                 AND coalesce(t.categorie_bron, '') NOT IN ('handmatig', 'excel', 'gesplitst')
               ORDER BY abs(date_diff('day', t.boekingsdatum, ?))""",
            [totaal, datum - timedelta(days=7), datum + timedelta(days=25), datum],
        ).fetchall()
        if debet:
            op_rekening = [k for k in kandidaten if k[1] == debet]
            kandidaten = op_rekening or kandidaten
        met_woord = [k for k in kandidaten if KAARTWOORDEN.search(k[2] or "")]
        keuze = met_woord[0] if met_woord else (kandidaten[0] if len(kandidaten) == 1 else None)
        if keuze:
            _koppel(con, afr_id, keuze[0])
            gekoppeld += 1
    return gekoppeld


def koppel_handmatig(con: duckdb.DuckDBPyConnection, afrekening_id: str, txn_id: str) -> None:
    """Koppel zelf een afrekening aan een betaling (overschrijft ook een handmatige categorie)."""
    if not een(con.execute("SELECT 1 FROM kaartafrekeningen WHERE afrekening_id = ?", [afrekening_id])):
        raise ValueError("Onbekende afrekening")
    con.execute("UPDATE transacties SET kaart_afrekening_id = NULL, categorie_bron = NULL WHERE kaart_afrekening_id = ? "
                "AND rekening_iban NOT IN (SELECT kaart_rekening FROM kaartafrekeningen WHERE afrekening_id = ?)",
                [afrekening_id, afrekening_id])
    _koppel(con, afrekening_id, txn_id)


def kandidaten_voor(con: duckdb.DuckDBPyConnection, afrekening_id: str, limiet: int = 30) -> list[tuple]:
    """Mogelijke betalingen voor een afrekening, het best passende bedrag eerst."""
    rij = een(con.execute("SELECT afrekeningsdatum, totaal FROM kaartafrekeningen WHERE afrekening_id = ?", [afrekening_id]))
    if not rij:
        return []
    datum, totaal = rij
    return con.execute(
        """SELECT t.txn_id, t.boekingsdatum, t.bedrag, coalesce(t.tegenpartij_naam, left(t.omschrijving, 60))
           FROM transacties t LEFT JOIN rekeningen r ON r.iban = t.rekening_iban
           WHERE coalesce(r.soort, 'zicht') <> 'kredietkaart' AND t.bedrag < 0 AND t.kaart_afrekening_id IS NULL
             AND t.boekingsdatum BETWEEN ? AND ?
           ORDER BY abs(-t.bedrag - ?), t.boekingsdatum LIMIT ?""",
        [datum - timedelta(days=15), datum + timedelta(days=45), totaal or 0, limiet],
    ).fetchall()
