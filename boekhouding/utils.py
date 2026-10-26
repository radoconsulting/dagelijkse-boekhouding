"""Hulpfuncties voor bedragen, datums, IBAN's en gestructureerde mededelingen (OGM)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")

DATUM_FORMATEN = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y", "%Y%m%d")

_OGM_RE = re.compile(r"(?:\+{3}|\*{3})\s*(\d{3})\s*/?\s*(\d{4})\s*/?\s*(\d{5})\s*(?:\+{3}|\*{3})")
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,30})\b")


def parse_bedrag(waarde, decimaal: str | None = None) -> Decimal | None:
    """Zet een bedrag als tekst (Belgische of internationale notatie) om naar Decimal.

    decimaal: ',' of '.' als hint wanneer de notatie dubbelzinnig is (bv. '1.000').
    """
    if waarde is None:
        return None
    if isinstance(waarde, Decimal):
        return waarde.quantize(CENT)
    if isinstance(waarde, (int, float)):
        return Decimal(str(waarde)).quantize(CENT)
    s = str(waarde).strip()
    if not s:
        return None
    s = (
        s.replace(" ", "")
        .replace(" ", "")
        .replace(" ", "")
        .replace("EUR", "")
        .replace("€", "")
        .replace("−", "-")
    )
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-"):
        neg, s = not neg, s[:-1]
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    heeft_komma, heeft_punt = "," in s, "." in s
    if heeft_komma and heeft_punt:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif heeft_komma:
        if decimaal == "." or s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif heeft_punt:
        if s.count(".") > 1 or (decimaal == "," and re.fullmatch(r"\d{1,3}(\.\d{3})+", s)):
            s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return (-d if neg else d).quantize(CENT)


def parse_datum(waarde, formaten: tuple[str, ...] = DATUM_FORMATEN) -> date | None:
    if waarde is None:
        return None
    if isinstance(waarde, datetime):
        return waarde.date()
    if isinstance(waarde, date):
        return waarde
    s = str(waarde).strip()
    if not s:
        return None
    s = s.split(" ")[0] if re.match(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\s", s) else s
    for fmt in formaten:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normaliseer_iban(waarde) -> str | None:
    if waarde is None:
        return None
    s = re.sub(r"[\s\-.]", "", str(waarde)).upper()
    return s or None


def formatteer_iban(iban: str | None) -> str:
    if not iban:
        return ""
    s = normaliseer_iban(iban) or ""
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))


IBAN_LENGTES = {
    "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18,
    "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24,
}


def iban_geldig(iban: str | None) -> bool:
    """Modulo-97-controle (ISO 13616) en, voor gekende landen, de lengte."""
    s = normaliseer_iban(iban) or ""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", s) or IBAN_LENGTES.get(s[:2], len(s)) != len(s):
        return False
    cijfers = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(cijfers) % 97 == 1


def vind_iban(tekst: str | None) -> str | None:
    """Eerste geldige IBAN in een tekst, ook als er meteen een naam in hoofdletters op volgt
    ('NL91ABNA0417164300 SALON VOORBEELD'). Zonder geldige kandidaat: de eerste die op een IBAN lijkt."""
    if not tekst:
        return None
    eerste = None
    for m in _IBAN_RE.finditer(tekst.upper()):
        kandidaat = normaliseer_iban(m.group(1))
        eerste = eerste or kandidaat
        lengte = IBAN_LENGTES.get(kandidaat[:2])
        for n in ([lengte] if lengte else range(len(kandidaat), 14, -1)):
            if len(kandidaat) >= n and iban_geldig(kandidaat[:n]):
                return kandidaat[:n]
    return eerste


def ogm_geldig(cijfers: str) -> bool:
    """Controleer de modulo-97 controle van een Belgische gestructureerde mededeling."""
    cijfers = re.sub(r"\D", "", cijfers or "")
    if len(cijfers) != 12:
        return False
    rest = int(cijfers[:10]) % 97
    return int(cijfers[10:]) == (rest or 97)


def formatteer_ogm(cijfers: str | None) -> str | None:
    cijfers = re.sub(r"\D", "", cijfers or "")
    if len(cijfers) != 12:
        return None
    return f"+++{cijfers[:3]}/{cijfers[3:7]}/{cijfers[7:]}+++"


def vind_ogm(tekst: str | None) -> str | None:
    """Zoek een gestructureerde mededeling (+++123/4567/89012+++) in vrije tekst."""
    if not tekst:
        return None
    m = _OGM_RE.search(tekst)
    if m:
        return formatteer_ogm("".join(m.groups()))
    kaal = re.sub(r"\D", "", tekst)
    if len(kaal) == 12 and ogm_geldig(kaal) and re.fullmatch(r"[\d\s/+*]+", tekst.strip()):
        return formatteer_ogm(kaal)
    return None


def ogm_cijfers(ogm: str | None) -> str | None:
    c = re.sub(r"\D", "", ogm or "")
    return c if len(c) == 12 else None


def zonder_accenten(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normaliseer_kop(s: str) -> str:
    s = zonder_accenten((s or "").strip().strip('"').strip("﻿")).lower()
    return re.sub(r"\s+", " ", s)


def schoon(s) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


def bestand_hash(inhoud: bytes) -> str:
    return hashlib.sha256(inhoud).hexdigest()


def transactie_sleutel(rekening_iban: str, boekingsdatum: date, bedrag: Decimal, volgnummer: int) -> str:
    """Natuurlijke sleutel: dezelfde verrichting levert dezelfde sleutel op, ongeacht het formaat
    (CSV, CODA of PDF). Volgnummer onderscheidt identieke bedragen op dezelfde dag."""
    basis = f"{normaliseer_iban(rekening_iban)}|{boekingsdatum.isoformat()}|{bedrag:.2f}|{volgnummer}"
    return hashlib.sha1(basis.encode()).hexdigest()[:24]


def slug(s: str) -> str:
    s = zonder_accenten(s).lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def pdf_tekst(inhoud: bytes, max_paginas: int = 5) -> str:
    """Ingebedde tekst uit een PDF (leeg bij gescande PDF's)."""
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(inhoud)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:max_paginas])
    except Exception:
        return ""
