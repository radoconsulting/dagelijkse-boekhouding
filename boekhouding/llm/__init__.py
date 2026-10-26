"""AI-laag: één functie (vraag_json) die per taak de gekozen aanbieder en het gekozen model gebruikt.

    vraag_json(inst, "documenten", PdfAfschrift, opdracht, bijlage=pdf_bytes, mime_type="application/pdf")

- De aanbieder en het model per taak komen uit Instellingen (.env of de pagina AI-modellen).
- Leest de aanbieder geen PDF's (of staat LLM_PDF_ALS_TEKST=1), dan gaat de tekst uit de PDF mee.
  Weigert een aanbieder een PDF onverwacht, dan probeert de app het één keer opnieuw met de tekst.
- Tijdelijke fouten (429, 5xx, time-outs) worden tot twee keer opnieuw geprobeerd.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel

from ..settings import Instellingen
from ..utils import pdf_tekst
from . import aanbieders
from .aanbieders import AIFout, Bijlage, lijst_modellen
from .catalogus import aanbieder, catalogus
from .schemas import (
    CategorieVoorstel,
    CategorieVoorstellen,
    FactuurGegevens,
    KaartAfrekening,
    KaartVerrichting,
    PdfAfschrift,
    PdfVerrichting,
    Ping,
)

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)

__all__ = [
    "AIFout",
    "Bijlage",
    "CategorieVoorstel",
    "CategorieVoorstellen",
    "FactuurGegevens",
    "KaartAfrekening",
    "KaartVerrichting",
    "PdfAfschrift",
    "PdfVerrichting",
    "aanbieder",
    "catalogus",
    "lijst_modellen",
    "controleer_verbinding",
    "vraag_json",
]


def _pdf_als_tekst(opdracht: str, bijlage: Bijlage) -> str:
    tekst = pdf_tekst(bijlage.data, max_paginas=60)
    if not tekst.strip():
        raise AIFout(
            "Deze PDF bevat geen tekst (gescand?) en de gekozen aanbieder kan PDF's niet zelf lezen. "
            "Kies voor documenten Gemini, Claude, OpenAI of Mistral."
        )
    return f"{opdracht}\n\nInhoud van het document (tekst uit de PDF):\n{tekst}"


def _met_herhaling(oproep, aanbieder_code: str, pogingen: int = 3):
    for poging in range(pogingen):
        try:
            return oproep()
        except Exception as e:
            fout = aanbieders._vertaal(e, aanbieder_code)
            if fout.tijdelijk and poging < pogingen - 1:
                wacht = 2 * (poging + 1) ** 2
                # Bij een limiet (429) zegt de API vaak hoe lang je moet wachten, bv. "retryDelay": "37s".
                if m := re.search(r"retry(?:Delay['\"]?\s*:\s*['\"]?| in )(\d+(?:\.\d+)?)\s?s", str(e), re.I):
                    wacht = min(max(wacht, float(m.group(1)) + 1), 65)
                log.warning("Tijdelijke AI-fout (%s), nieuwe poging over %ss", fout, wacht)
                time.sleep(wacht)
                continue
            raise fout from e


def vraag_json(
    inst: Instellingen,
    taak: str,
    schema: type[T],
    opdracht: str,
    bijlage: bytes | None = None,
    mime_type: str | None = None,
    systeem: str | None = None,
    bestandsnaam: str = "document.pdf",
) -> T:
    keuze = inst.ai(taak)
    info = aanbieder(keuze.provider)
    klaar, uitleg = inst.provider_klaar(keuze.provider)
    if not klaar:
        raise AIFout(f"{info.label} is niet ingesteld: {uitleg}.")
    if not keuze.model:
        raise AIFout(f"Er is geen model gekozen voor {taak} bij {info.label} (pagina AI-modellen of .env).")

    b = Bijlage(bijlage, mime_type or "application/pdf", bestandsnaam) if bijlage is not None else None
    if b is not None and b.is_pdf and (inst.pdf_als_tekst or not info.pdf):
        opdracht, b = _pdf_als_tekst(opdracht, b), None
    if b is not None and not b.is_pdf and not info.afbeeldingen:
        raise AIFout(f"{info.label} kan geen foto's lezen. Kies een andere aanbieder voor documenten.")

    impl = aanbieders.IMPLEMENTATIES[keuze.provider]
    try:
        return _met_herhaling(lambda: impl(inst, keuze.model, schema, opdracht, systeem, b, taak), keuze.provider)
    except AIFout as e:
        if b is None or not b.is_pdf or not e.bijlage_probleem:
            raise
        log.warning("%s weigerde de PDF (%s); nieuwe poging met de tekst uit de PDF", keuze, e)
        tekst_opdracht = _pdf_als_tekst(opdracht, b)
        return _met_herhaling(lambda: impl(inst, keuze.model, schema, tekst_opdracht, systeem, None, taak), keuze.provider)


def controleer_verbinding(inst: Instellingen, taak: str) -> tuple[bool, str]:
    """Kleine proefoproep met de gekozen aanbieder en het gekozen model voor deze taak."""
    begin = time.perf_counter()
    try:
        antwoord = vraag_json(inst, taak, Ping, "Antwoord met het woord 'ok' in het veld antwoord.")
    except AIFout as e:
        return False, str(e)
    return True, f"{inst.ai(taak)} antwoordde '{antwoord.antwoord}' in {time.perf_counter() - begin:.1f} s"
