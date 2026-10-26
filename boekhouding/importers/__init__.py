"""Herkennen en inlezen van bankbestanden: CSV (KBC/CBC, Belfius, Crelan), CODA en PDF."""

from __future__ import annotations

from pathlib import Path

from .coda import is_coda, lees_coda
from .csv_banken import is_bank_csv, lees_csv
from .pdf import lees_pdf_afschrift, raad_pdf_soort

AFBEELDING_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def herken_bestand(bestandsnaam: str, inhoud: bytes) -> str:
    """Geef 'csv', 'coda', 'pdf', 'afbeelding' of 'onbekend'."""
    ext = Path(bestandsnaam).suffix.lower()
    if inhoud[:5] == b"%PDF-" or ext == ".pdf":
        return "pdf"
    if ext in AFBEELDING_EXT:
        return "afbeelding"
    if is_coda(inhoud):
        return "coda"
    if ext in {".csv", ".txt", ".tsv"} or is_bank_csv(inhoud):
        return "csv"
    return "onbekend"


__all__ = [
    "herken_bestand",
    "lees_csv",
    "lees_coda",
    "lees_pdf_afschrift",
    "raad_pdf_soort",
]
