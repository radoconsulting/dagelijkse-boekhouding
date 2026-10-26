"""CODA (Febelfin 'Coded statement of account', versie 2) — het Belgische standaardformaat.

Records van 128 tekens met vaste posities:
  0  kop                      1  oud saldo (rekening, munt)
  21/22/23  beweging           31/32/33  bijkomende informatie
  4  vrije mededeling         8  nieuw saldo           9  einde

Enkel bewegingen met detailnummer 0000 worden opgenomen; detailregels van een
gegroepeerde (globale) beweging zouden anders dubbel tellen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from ..models import Afschrift, ImportFout, Transactie
from ..utils import formatteer_ogm, normaliseer_iban, schoon, vind_iban, vind_ogm

FAMILIES = {
    "01": "Overschrijving",
    "02": "Instantoverschrijving",
    "03": "Cheque",
    "04": "Kaartbetaling",
    "05": "Domiciliëring",
    "07": "Handelspapier",
    "09": "Loketverrichting",
    "11": "Effecten",
    "13": "Krediet",
    "30": "Diverse verrichting",
    "35": "Afsluiting (interesten/kosten)",
    "41": "Buitenlandse overschrijving",
    "43": "Buitenlandse cheque",
    "47": "Buitenlands handelspapier",
    "80": "Kosten en provisies",
}


def _datum(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d%m%y").date()
    except ValueError:
        return None


def _bedrag(teken: str, cijfers: str) -> Decimal:
    waarde = Decimal(cijfers) / 1000
    return (-waarde if teken == "1" else waarde).quantize(Decimal("0.01"))


@dataclass
class _Beweging:
    volgnr: str
    bank_referentie: str = ""
    bedrag: Decimal = Decimal("0")
    valutadatum: date | None = None
    boekingsdatum: date | None = None
    code: str = ""
    comm_type: str = "0"
    comm: str = ""
    tegenpartij_rekening: str = ""
    tegenpartij_naam: str = ""
    tegenpartij_bic: str = ""
    info: list[str] = field(default_factory=list)


@dataclass
class _Uittreksel:
    rekening: str | None = None
    munt: str = "EUR"
    houder: str | None = None
    oud_saldo: Decimal | None = None
    nieuw_saldo: Decimal | None = None
    nummer: str | None = None
    bewegingen: list[_Beweging] = field(default_factory=list)


def is_coda(inhoud: bytes) -> bool:
    try:
        regels = inhoud.decode("latin-1").splitlines()
    except Exception:
        return False
    if len(regels) < 3:
        return False
    eerste = regels[0]
    return (
        eerste[:1] == "0"
        and eerste[1:5] == "0000"
        and len(eerste.rstrip("\r\n")) >= 100
        and any(r[:1] == "1" for r in regels[:5])
        and any(r[:1] == "9" for r in regels[-3:])
    )


def _rekening_uit_veld(structuur: str, veld: str) -> tuple[str | None, str]:
    if structuur == "0":
        rekening, munt = veld[0:12], veld[13:16]
    elif structuur == "2":
        rekening, munt = veld[0:31], veld[34:37]
    else:
        rekening, munt = veld[0:34], veld[34:37]
    rekening = rekening.strip()
    if not rekening.strip("0 "):
        return None, (munt.strip() or "EUR")
    return (vind_iban(rekening) or normaliseer_iban(rekening)), (munt.strip() or "EUR")


def lees_coda(inhoud: bytes, bestandsnaam: str = "") -> Afschrift:
    if not is_coda(inhoud):
        raise ImportFout(f"'{bestandsnaam}' is geen geldig CODA-bestand.")
    tekst = inhoud.decode("latin-1")
    uittreksels: list[_Uittreksel] = []
    huidig: _Uittreksel | None = None
    bewegingen: dict[str, _Beweging] = {}

    for ruw in tekst.splitlines():
        r = ruw.rstrip("\r\n").ljust(128)
        soort = r[0]
        if soort == "0":
            huidig = _Uittreksel()
            uittreksels.append(huidig)
            bewegingen = {}
        elif soort == "1" and huidig is not None:
            huidig.rekening, huidig.munt = _rekening_uit_veld(r[1], r[5:42])
            huidig.oud_saldo = _bedrag(r[42], r[43:58])
            huidig.houder = schoon(r[64:90])
            huidig.nummer = r[2:5].strip() or None
        elif soort == "2" and huidig is not None:
            volgnr, detail = r[2:6], r[6:10]
            if detail != "0000":
                continue
            artikel = r[1]
            if artikel == "1":
                b = _Beweging(
                    volgnr=volgnr,
                    bank_referentie=r[10:31].strip(),
                    bedrag=_bedrag(r[31], r[32:47]),
                    valutadatum=_datum(r[47:53]),
                    code=r[53:61],
                    comm_type=r[61],
                    comm=r[62:115],
                    boekingsdatum=_datum(r[115:121]),
                )
                bewegingen[volgnr] = b
                huidig.bewegingen.append(b)
            elif volgnr in bewegingen:
                b = bewegingen[volgnr]
                if artikel == "2":
                    b.comm += r[10:63]
                    b.tegenpartij_bic = r[98:109].strip()
                elif artikel == "3":
                    b.tegenpartij_rekening = r[10:47].strip()
                    b.tegenpartij_naam = r[47:82].strip()
                    b.comm += r[82:125]
        elif soort == "3" and huidig is not None:
            b = bewegingen.get(r[2:6])
            if b is None or len(" ".join(b.info)) > 400:
                continue
            artikel = r[1]
            tekstdeel = {"1": r[40:113], "2": r[10:115], "3": r[10:100]}.get(artikel, "")
            if artikel == "1" and r[39] == "1":
                tekstdeel = r[40 + 3 : 113]  # gestructureerde info: code (3) weglaten
            if tekstdeel.strip():
                b.info.append(tekstdeel.strip())
        elif soort == "8" and huidig is not None:
            huidig.nieuw_saldo = _bedrag(r[41], r[42:57])

    transacties: list[Transactie] = []
    controles: list[str] = []
    for u in uittreksels:
        lopend = u.oud_saldo
        for b in u.bewegingen:
            lopend = lopend + b.bedrag if lopend is not None else None
            ogm = None
            mededeling = None
            if b.comm_type == "1" and b.comm[:3] in ("101", "102"):
                ogm = formatteer_ogm(b.comm[3:15])
            elif b.comm_type == "1":
                mededeling = schoon(b.comm[3:])
            else:
                mededeling = schoon(b.comm)
                ogm = vind_ogm(mededeling)
            familie = FAMILIES.get(b.code[1:3], f"Verrichting {b.code[1:5]}")
            info = schoon(" ".join(b.info))
            tegen_iban, _ = _rekening_uit_veld("3", b.tegenpartij_rekening.ljust(37)) if b.tegenpartij_rekening else (None, "")
            transacties.append(
                Transactie(
                    rekening_iban=u.rekening,
                    boekingsdatum=b.boekingsdatum or b.valutadatum,
                    valutadatum=b.valutadatum,
                    bedrag=b.bedrag,
                    munt=u.munt,
                    tegenpartij_naam=schoon(b.tegenpartij_naam),
                    tegenpartij_iban=tegen_iban,
                    tegenpartij_bic=b.tegenpartij_bic or None,
                    omschrijving=schoon(f"{familie} {info or ''}"),
                    mededeling=mededeling,
                    gestructureerde_mededeling=ogm,
                    bank_referentie=b.bank_referentie or None,
                    saldo_na=lopend,
                    ruw={"code": b.code, "uittreksel": u.nummer, "volgnr": b.volgnr, "info": info},
                )
            )
        if u.oud_saldo is not None and u.nieuw_saldo is not None:
            som = sum((b.bedrag for b in u.bewegingen), Decimal("0"))
            verschil = u.oud_saldo + som - u.nieuw_saldo
            controles.append("ok" if verschil == 0 else f"afwijking van {verschil} op uittreksel {u.nummer}")

    if not transacties:
        raise ImportFout(f"'{bestandsnaam}' bevat geen bewegingen.")
    saldo = "n.v.t." if not controles else ("ok" if all(c == "ok" for c in controles) else "; ".join(c for c in controles if c != "ok"))
    return Afschrift(bron="coda", bank=None, transacties=transacties, saldo_controle=saldo)
