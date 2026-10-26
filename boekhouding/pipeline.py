"""Importpijplijn: bestand -> herkennen -> inlezen -> ontdubbelen -> opslaan -> categoriseren -> facturen koppelen."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from . import facturen as fact
from . import kredietkaart as kaart
from .db import een
from .categoriseren import categoriseer_met_ai, pas_regels_toe
from .importers import herken_bestand, lees_coda, lees_csv, lees_pdf_afschrift, raad_pdf_soort
from .llm import AIFout
from .models import Afschrift, ImportFout, RekeningOntbreekt
from .settings import Instellingen, laad_instellingen
from .utils import bestand_hash, normaliseer_iban, transactie_sleutel

MAX_AI_PER_IMPORT = 1000  # de rest kan op de pagina Transacties
ONDERSTEUND = {".csv", ".txt", ".cod", ".coda", ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}


@dataclass
class ImportVerslag:
    bestandsnaam: str
    soort: str = ""  # afschrift | factuur | kredietkaart
    bron: str = ""
    bank: str | None = None
    rekeningen: list[str] = field(default_factory=list)
    gelezen: int = 0
    nieuw: int = 0
    dubbel: int = 0
    saldo_controle: str = "n.v.t."
    gecategoriseerd_regels: int = 0
    gecategoriseerd_ai: int = 0
    facturen_gekoppeld: int = 0
    kaartafrekeningen_gekoppeld: int = 0
    factuur_id: str | None = None
    al_geimporteerd: bool = False
    fout: str | None = None
    waarschuwingen: list[str] = field(default_factory=list)
    nieuwe_ids: list[str] = field(default_factory=list)
    duur: float = 0.0  # seconden

    @property
    def gelukt(self) -> bool:
        return self.fout is None


def _registreer_rekeningen(con, afschrift: Afschrift) -> None:
    for iban in afschrift.rekeningen:
        con.execute(
            "INSERT INTO rekeningen (iban, bank) VALUES (?, ?) ON CONFLICT DO NOTHING",
            [iban, afschrift.bank],
        )
        if afschrift.bank:
            con.execute("UPDATE rekeningen SET bank = ? WHERE iban = ? AND bank IS NULL", [afschrift.bank, iban])
    for iban, (saldo, datum) in afschrift.gekende_saldi.items():
        # Een recenter gekend saldo vervangt een ouder (ook een zelf ingevuld startsaldo).
        con.execute(
            """UPDATE rekeningen SET startsaldo = ?, startsaldo_datum = ?
               WHERE iban = ? AND (startsaldo_datum IS NULL OR startsaldo_datum <= ?)""",
            [saldo, datum, iban, datum],
        )


def _bewaar_afschrift(con, afschrift: Afschrift, import_id: str) -> tuple[list[str], int]:
    """Sla nieuwe transacties op; geef (nieuwe txn_ids, aantal dubbels)."""
    volgnummers: Counter = Counter()
    rijen = []
    for t in afschrift.transacties:
        groep = (t.rekening_iban, t.boekingsdatum, t.bedrag)
        volgnummers[groep] += 1
        txn_id = transactie_sleutel(t.rekening_iban, t.boekingsdatum, t.bedrag, volgnummers[groep])
        rijen.append(
            (
                txn_id, t.rekening_iban, t.boekingsdatum, t.valutadatum, t.bedrag, t.munt, t.tegenpartij_naam,
                t.tegenpartij_iban, t.tegenpartij_bic, t.omschrijving, t.mededeling, t.gestructureerde_mededeling,
                t.bank_referentie, t.saldo_na, afschrift.bron, import_id, json.dumps(t.ruw, ensure_ascii=False, default=str),
            )
        )
    ids = [r[0] for r in rijen]
    bestaand = {
        r[0]
        for r in con.execute("SELECT txn_id FROM transacties WHERE list_contains(?::VARCHAR[], txn_id)", [ids]).fetchall()
    }
    nieuw = [r for r in rijen if r[0] not in bestaand]
    if nieuw:
        con.executemany(
            """INSERT INTO transacties
               (txn_id, rekening_iban, boekingsdatum, valutadatum, bedrag, munt, tegenpartij_naam, tegenpartij_iban,
                tegenpartij_bic, omschrijving, mededeling, gestructureerde_mededeling, bank_referentie, saldo_na,
                bron, import_id, ruwe_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            nieuw,
        )
    return [r[0] for r in nieuw], len(rijen) - len(nieuw)


def importeer_bestand(
    con: duckdb.DuckDBPyConnection,
    bestandsnaam: str,
    inhoud: bytes,
    *,
    rekening_iban: str | None = None,
    pdf_soort: str = "auto",
    gebruik_ai: bool = False,
    forceer: bool = False,
    inst: Instellingen | None = None,
) -> ImportVerslag:
    """Importeer één bestand. Fouten komen terug in verslag.fout, behalve RekeningOntbreekt (die wordt opgeworpen
    zodat de app of CLI de gebruiker een rekening kan laten kiezen)."""
    inst = inst or laad_instellingen()
    verslag = ImportVerslag(bestandsnaam=bestandsnaam)
    h = bestand_hash(inhoud)
    vorige = een(con.execute("SELECT bestandsnaam, geimporteerd_op FROM imports WHERE bestand_hash = ?", [h]))
    if vorige and not forceer:
        verslag.al_geimporteerd = True
        verslag.fout = f"Dit bestand werd al geïmporteerd op {vorige[1]:%d/%m/%Y %H:%M} (als '{vorige[0]}')."
        return verslag

    soort = herken_bestand(bestandsnaam, inhoud)
    kaartimport = None
    try:
        if soort == "pdf" and pdf_soort == "auto":
            pdf_soort = raad_pdf_soort(inhoud, bestandsnaam)
        if soort == "afbeelding" or (soort == "pdf" and pdf_soort == "factuur"):
            return _importeer_factuur(con, bestandsnaam, inhoud, h, inst, verslag)
        if soort == "pdf" and pdf_soort == "kredietkaart":
            kaartimport = kaart.lees_kaartafrekening(inhoud, bestandsnaam, inst)
            afschrift = kaartimport.afschrift
        elif soort == "pdf":
            afschrift = lees_pdf_afschrift(inhoud, bestandsnaam, inst)
        elif soort == "coda":
            afschrift = lees_coda(inhoud, bestandsnaam)
        elif soort == "csv":
            afschrift = lees_csv(inhoud, bestandsnaam)
        else:
            raise ImportFout(f"Bestandstype van '{bestandsnaam}' wordt niet ondersteund.")
    except RekeningOntbreekt:
        raise
    except ImportFout as e:
        verslag.fout = str(e)
        return verslag

    if rekening_iban:
        iban = normaliseer_iban(rekening_iban)
        for t in afschrift.transacties:
            t.rekening_iban = t.rekening_iban or iban
    if any(not t.rekening_iban for t in afschrift.transacties):
        raise RekeningOntbreekt(
            f"'{bestandsnaam}' vermeldt niet van welke rekening de verrichtingen zijn. Kies of geef een rekening op."
        )

    verslag.soort = "kredietkaart" if kaartimport else "afschrift"
    verslag.bron, verslag.bank = afschrift.bron, afschrift.bank
    verslag.rekeningen = sorted(afschrift.rekeningen)
    verslag.saldo_controle = afschrift.saldo_controle
    verslag.waarschuwingen = afschrift.waarschuwingen
    verslag.gelezen = len(afschrift.transacties)

    import_id = uuid.uuid4().hex
    gekende_rekeningen = {r[0] for r in con.execute("SELECT iban FROM rekeningen").fetchall()}
    _registreer_rekeningen(con, afschrift)
    nieuwe_ids, verslag.dubbel = _bewaar_afschrift(con, afschrift, import_id)
    verslag.nieuw = len(nieuwe_ids)
    verslag.nieuwe_ids = nieuwe_ids
    if kaartimport:
        kaart.bewaar_kaartafrekening(con, kaartimport)
    con.execute(
        """INSERT INTO imports (import_id, bestandsnaam, bestand_hash, bron, bank, rekening_iban,
                                aantal_gelezen, aantal_nieuw, aantal_dubbel, saldo_controle)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [import_id, bestandsnaam, h, afschrift.bron, afschrift.bank, ", ".join(verslag.rekeningen),
         verslag.gelezen, verslag.nieuw, verslag.dubbel, verslag.saldo_controle],
    )

    if nieuwe_ids:
        # Een nieuwe eigen rekening kan oude transacties tot interne overboeking maken: dan alles opnieuw bekijken,
        # anders enkel de nieuwe transacties (bij honderden afschriften scheelt dat veel tijd).
        nieuwe_rekening = bool(afschrift.rekeningen - gekende_rekeningen)
        verslag.gecategoriseerd_regels = pas_regels_toe(con, txn_ids=None if nieuwe_rekening else nieuwe_ids)
        verslag.facturen_gekoppeld = fact.koppel_facturen(con)
        verslag.kaartafrekeningen_gekoppeld = kaart.koppel_kaartafrekeningen(con)
        if gebruik_ai and inst.ai_actief("categorisatie"):
            try:
                verslag.gecategoriseerd_ai = categoriseer_met_ai(con, inst, txn_ids=nieuwe_ids)
            except AIFout as e:
                verslag.waarschuwingen.append(f"AI-categorisatie mislukt: {e}")
    return verslag


def _importeer_factuur(con, bestandsnaam: str, inhoud: bytes, h: str, inst: Instellingen, verslag: ImportVerslag) -> ImportVerslag:
    verslag.soort = "factuur"
    verslag.bron = "factuur"
    try:
        gegevens, extractie = fact.lees_factuur(inhoud, bestandsnaam, inst, con)
    except ImportFout as e:
        verslag.fout = str(e)
        return verslag
    factuur_id = h[:24]
    fact.bewaar_factuur(con, factuur_id, bestandsnaam, gegevens, extractie)
    con.execute(
        """INSERT INTO imports (import_id, bestandsnaam, bestand_hash, bron, aantal_gelezen, aantal_nieuw, aantal_dubbel, saldo_controle)
           VALUES (?, ?, ?, 'factuur', 1, 1, 0, 'n.v.t.')""",
        [uuid.uuid4().hex, bestandsnaam, h],
    )
    verslag.factuur_id = factuur_id
    verslag.gelezen = verslag.nieuw = 1
    verslag.facturen_gekoppeld = fact.koppel_facturen(con)
    if extractie == "lokaal":
        verslag.waarschuwingen.append("Factuur zonder AI gelezen: controleer leverancier, bedrag en datums.")
    return verslag


def verwerk_inbox(
    con: duckdb.DuckDBPyConnection,
    inst: Instellingen | None = None,
    gebruik_ai: bool = False,
    voortgang=None,
) -> list[ImportVerslag]:
    """Importeer alle bestanden uit de inbox-map en verplaats ze naar data/archief/JJJJ-MM/.
    Bestanden die mislukken blijven in de inbox staan."""
    inst = inst or laad_instellingen()
    inst.maak_mappen()
    verslagen = []
    paden = [p for p in sorted(inst.inbox_dir.iterdir()) if p.is_file() and p.suffix.lower() in ONDERSTEUND]
    for i, pad in enumerate(paden):
        if voortgang:
            voortgang(i, len(paden), pad.name)
        begin = time.monotonic()
        try:
            # AI-categorisatie één keer op het einde, voor alle nieuwe transacties samen (veel minder oproepen).
            verslag = importeer_bestand(con, pad.name, pad.read_bytes(), gebruik_ai=False, inst=inst)
        except RekeningOntbreekt as e:
            verslag = ImportVerslag(bestandsnaam=pad.name, fout=str(e))
        verslag.duur = time.monotonic() - begin
        verslagen.append(verslag)
        if verslag.gelukt or verslag.al_geimporteerd:
            doel = inst.archief_dir / datetime.now().strftime("%Y-%m")
            doel.mkdir(parents=True, exist_ok=True)
            bestemming = doel / pad.name
            if bestemming.exists():
                bestemming = doel / f"{pad.stem}_{datetime.now():%H%M%S}{pad.suffix}"
            shutil.move(str(pad), bestemming)
    if gebruik_ai:
        categoriseer_nieuwe_met_ai(con, inst, verslagen)
    return verslagen


def categoriseer_nieuwe_met_ai(con, inst: Instellingen, verslagen: list[ImportVerslag], voortgang=None) -> int:
    """AI-categorisatie voor alle nieuwe transacties van een reeks imports samen. Het resultaat komt in het
    verslag van het bestand waar de transactie vandaan kwam; een fout wordt een waarschuwing."""
    ids = [t for v in verslagen for t in v.nieuwe_ids]
    if not ids or not inst.ai_actief("categorisatie"):
        return 0
    voor = dict(con.execute(
        "SELECT txn_id, categorie_bron FROM transacties WHERE list_contains(?::VARCHAR[], txn_id)", [ids]
    ).fetchall())
    try:
        totaal = categoriseer_met_ai(con, inst, max_aantal=MAX_AI_PER_IMPORT, txn_ids=ids, voortgang=voortgang)
    except AIFout as e:
        verslagen[-1].waarschuwingen.append(f"AI-categorisatie mislukt: {e}")
        return 0
    na = dict(con.execute(
        "SELECT txn_id, categorie_bron FROM transacties WHERE list_contains(?::VARCHAR[], txn_id)", [ids]
    ).fetchall())
    for v in verslagen:
        v.gecategoriseerd_ai += sum(1 for t in v.nieuwe_ids if na.get(t) == "ai" and voor.get(t) != "ai")
    return totaal


def bewaar_origineel(inst: Instellingen, bestandsnaam: str, inhoud: bytes) -> Path:
    """Bewaar een via de app opgeladen bestand in het archief (handig voor facturen)."""
    doel = inst.archief_dir / datetime.now().strftime("%Y-%m")
    doel.mkdir(parents=True, exist_ok=True)
    pad = doel / Path(bestandsnaam).name
    if pad.exists() and pad.read_bytes() != inhoud:
        pad = doel / f"{pad.stem}_{datetime.now():%H%M%S}{pad.suffix}"
    pad.write_bytes(inhoud)
    return pad
