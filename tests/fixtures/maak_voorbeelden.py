"""Genereert de fictieve voorbeeldbestanden in deze map (KBC/Belfius/Crelan CSV, CODA en een factuur-PDF).

Alle namen, rekeningnummers en bedragen zijn verzonnen. Opnieuw genereren:
    python tests/fixtures/maak_voorbeelden.py
(de factuur-PDF vraagt het pakket reportlab; de rest niet)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

HIER = Path(__file__).resolve().parent

KBC_IBAN = "BE12734000000001"
BELFIUS_IBAN = "BE34068000000002"
CRELAN_IBAN = "BE56103000000003"


def fmt_be(b: Decimal) -> str:
    return f"{b:.2f}".replace(".", ",")


def kbc_csv() -> None:
    kop = (
        "Rekeningnummer;Rubrieknaam;Naam;Munt;Afschriftnummer;Datum;Omschrijving;Valuta;Bedrag;Saldo;credit;debet;"
        "rekeningnummer tegenpartij;BIC tegenpartij;Naam tegenpartij;Adres tegenpartij;gestructureerde mededeling;"
        "Vrije mededeling"
    )
    # (datum, omschrijving, bedrag, iban tegenpartij, bic, naam, ogm, vrije mededeling) — oudste eerst
    rijen = [
        (date(2026, 10, 15), "STORTING GROEIPAKKET", Decimal("174.52"), "BE41096123456760", "GKCCBEBB", "FONS", "", "Groeipakket oktober 2026"),
        (date(2026, 10, 17), "BETALING VIA BANCONTACT 17-10 NETFLIX.COM AMSTERDAM", Decimal("-13.99"), "", "", "", "", ""),
        (date(2026, 10, 18), "OVERSCHRIJVING", Decimal("-25.00"), "BE77001234567842", "GEBABEBB", "VZW De Speelpleintjes", "", "Lidgeld najaar"),
        (date(2026, 10, 19), "EUROPESE OVERSCHRIJVING", Decimal("-312.50"), "BE90363001234532", "BBRUBEBB", "Kinderopvang Het Bijenhuis", "090/9337/55493", ""),
        (date(2026, 10, 20), "BETALING VIA BANCONTACT 20-10 SHELL MECHELEN", Decimal("-65.30"), "", "", "", "", ""),
        (date(2026, 10, 21), "OVERSCHRIJVING NAAR EIGEN REKENING", Decimal("-500.00"), BELFIUS_IBAN, "GKCCBEBB", "Gezin Voorbeeld", "", "Huishoudpot"),
        (date(2026, 10, 22), "EUROPESE DOMICILIERING", Decimal("-142.00"), "BE46000325448336", "BPOTBEB1", "ENGIE Electrabel", "", "Voorschot elektriciteit en gas"),
        (date(2026, 10, 23), "EUROPESE OVERSCHRIJVING", Decimal("4250.00"), "BE68539007547034", "KREDBEBB", "Voorbeeld Werkgever NV", "", "Loon oktober 2026"),
        (date(2026, 10, 24), "BETALING VIA BANCONTACT 24-10 COLRUYT MECHELEN", Decimal("-87.45"), "", "", "", "", ""),
        (date(2026, 10, 24), "BETALING VIA BANCONTACT 24-10 BAKKERIJ DE KORENAAR ITEGEM", Decimal("-8.60"), "", "", "", "", ""),
        (date(2026, 10, 24), "BETALING VIA BANCONTACT 24-10 BAKKERIJ DE KORENAAR ITEGEM", Decimal("-8.60"), "", "", "", "", ""),
    ]
    saldo = Decimal("2350.00")
    regels = []
    for d, oms, bedrag, iban, bic, naam, ogm, vrij in rijen:
        saldo += bedrag
        credit = fmt_be(bedrag) if bedrag > 0 else ""
        debet = fmt_be(bedrag) if bedrag < 0 else ""
        regels.append(
            ";".join([
                "BE12 7340 0000 0001", "Gezinsrekening", "GEZIN VOORBEELD", "EUR", "2026043", d.strftime("%d/%m/%Y"),
                oms, d.strftime("%d/%m/%Y"), fmt_be(bedrag), fmt_be(saldo), credit, debet, iban, bic, naam, "",
                ogm, vrij,
            ])
        )
    regels.reverse()  # KBC zet de recentste verrichting bovenaan
    (HIER / "kbc_voorbeeld.csv").write_bytes(("\r\n".join([kop, *regels]) + "\r\n").encode("cp1252"))


def belfius_csv() -> None:
    inleiding = [
        "Boekingsdatum vanaf;01/10/2026",
        "Boekingsdatum tot en met;31/10/2026",
        "Bedrag vanaf;",
        "Bedrag tot en met;",
        "Rekeninguittrekselnummer vanaf;",
        "Rekeninguittrekselnummer tot en met;",
        "Mededeling;",
        "Naam tegenpartij bevat;",
        "Rekening tegenpartij;",
        "Laatste saldo;1.234,56 EUR",
        "Datum/uur van het laatste saldo;26/10/2026 08:00:00",
        ";",
    ]
    kop = (
        "Rekening;Boekingsdatum;Rekeninguittrekselnummer;Transactienummer;Rekening tegenpartij;Naam tegenpartij bevat;"
        "Straat en nummer;Postcode en plaats;Transactie;Valutadatum;Bedrag;Devies;BIC;Landcode;Mededelingen"
    )
    rijen = [
        ("21/10/2026", "00010", "BE12 7340 0000 0001", "Gezin Voorbeeld", "OVERSCHRIJVING VAN EIGEN REKENING", "500,00", "Huishoudpot"),
        ("22/10/2026", "00011", "", "", "BETALING MET DEBETKAART DELHAIZE MECHELEN", "-54,20", ""),
        ("25/10/2026", "00012", "BE15000000001234", "Proximus NV", "EUROPESE DOMICILIERING", "-79,99", "Factuur 2026/10 internet en mobiel"),
        ("26/10/2026", "00013", "", "", "BETALING MET DEBETKAART APOTHEEK DE LINDE ITEGEM", "-18,40", ""),
        ("26/10/2026", "00014", "BE88310123456741", "Café Den Hoek", "BETALING VIA PAYCONIQ", "-23,50", "Verjaardagsdrankje"),
    ]
    regels = [
        ";".join([BELFIUS_IBAN[:4] + " " + BELFIUS_IBAN[4:8] + " " + BELFIUS_IBAN[8:12] + " " + BELFIUS_IBAN[12:],
                  d, "2026/10", nr, iban, naam, "", "", trans, d, bedrag, "EUR", "", "BE", med])
        for d, nr, iban, naam, trans, bedrag, med in rijen
    ]
    (HIER / "belfius_voorbeeld.csv").write_bytes(("\r\n".join([*inleiding, kop, *regels]) + "\r\n").encode("cp1252"))


def crelan_csv() -> None:
    kop = "Datum;Bedrag;Saldo na verrichting;Munt;Tegenpartij;Rekening tegenpartij;Type verrichting;Mededeling;Rekening opdrachtgever"
    rijen = [
        ("01/10/2026", "-21.85", "1000.00", "Ethias", "BE00 0000 0000 0000", "Overschrijving", "Verzekeringstaks"),
        ("02/10/2026", "-.99", "999.01", "", "", "Beheerskosten", ""),
        ("05/10/2026", "-45.00", "954.01", "Gemeente Mechelen", "BE27091000000177", "Overschrijving", "Afvalbelasting 2026 +++120/0000/04345+++"),
    ]
    regels = [";".join([d, b, s, "EUR", t, r, ty, m, "BE56 1030 0000 0003"]) for d, b, s, t, r, ty, m in rijen]
    (HIER / "crelan_voorbeeld.csv").write_text("\n".join([kop, *regels]) + "\n", encoding="utf-8")


# --- CODA -------------------------------------------------------------------------


def _rec(*velden: tuple[int, str]) -> str:
    r = [" "] * 128
    for start, waarde in velden:
        r[start : start + len(waarde)] = list(waarde)
    return "".join(r[:128])


def _bedrag15(b: Decimal) -> tuple[str, str]:
    return ("1" if b < 0 else "0"), f"{int(abs(b) * 1000):015d}"


def coda() -> None:
    """CODA-uittreksel van de KBC-rekening met twee verrichtingen die ook in de KBC-CSV staan
    (om ontdubbeling over formaten heen te testen) en één nieuwe."""
    rek = KBC_IBAN.ljust(31) + "   " + "EUR"
    oud = Decimal("6091.18")
    bewegingen = [
        # (datum, bedrag, code, gestructureerd?, mededeling, iban tegenpartij, naam tegenpartij)
        (date(2026, 10, 22), Decimal("-142.00"), "00501000", False, "Voorschot elektriciteit en gas", "BE46000325448336", "ENGIE Electrabel"),
        (date(2026, 10, 24), Decimal("-87.45"), "00443000", False, "COLRUYT MECHELEN", "", ""),
        (date(2026, 10, 26), Decimal("-36.20"), "00101000", True, "101100000123407", "BE15000000001234", "Telenet BV"),
    ]
    regels = [
        _rec((0, "00000"), (5, "261026"), (11, "730"), (14, "05"), (24, "0000000001"), (34, "GEZIN VOORBEELD"), (60, "KREDBEBB"), (127, "2")),
        _rec((0, "12001"), (5, rek), (42, "0"), (43, f"{int(oud * 1000):015d}"), (58, "211026"), (64, "GEZIN VOORBEELD"), (90, "KBC-Gezinsrekening"), (125, "001")),
    ]
    totaal_debet = totaal_credit = Decimal("0")
    for i, (d, b, code, gestructureerd, med, iban, naam) in enumerate(bewegingen, start=1):
        teken, cijfers = _bedrag15(b)
        datum = d.strftime("%d%m%y")
        if gestructureerd:
            comm_type, comm = "1", "101" + med[3:15]
        else:
            comm_type, comm = "0", med[:53]
        regels.append(_rec((0, f"21{i:04d}0000"), (10, f"REF{i:018d}"), (31, teken), (32, cijfers), (47, datum), (53, code),
                           (61, comm_type), (62, comm), (115, datum), (121, "001"), (124, "0"), (125, "1"), (127, "0")))
        regels.append(_rec((0, f"22{i:04d}0000"), (98, "GKCCBEBB"), (125, "1"), (127, "0")))
        regels.append(_rec((0, f"23{i:04d}0000"), (10, iban.ljust(34) + ("EUR" if iban else "   ")), (47, naam), (125, "0"), (127, "0")))
        if b < 0:
            totaal_debet += -b
        else:
            totaal_credit += b
    nieuw = oud + sum(b for _, b, *_ in bewegingen)
    teken, cijfers = _bedrag15(nieuw)
    regels.append(_rec((0, "8001"), (4, rek), (41, teken), (42, cijfers), (57, "261026"), (127, "0")))
    regels.append(_rec((0, "9"), (16, f"{len(regels) - 1:06d}"), (22, f"{int(totaal_debet * 1000):015d}"), (37, f"{int(totaal_credit * 1000):015d}"), (127, "2")))
    (HIER / "kbc_voorbeeld.cod").write_text("\r\n".join(regels) + "\r\n", encoding="latin-1")


def factuur_pdf() -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        print("reportlab niet geïnstalleerd: factuur-PDF overgeslagen")
        return
    c = canvas.Canvas(str(HIER / "factuur_kinderopvang.pdf"), pagesize=A4)
    regels = [
        "Kinderopvang Het Bijenhuis vzw",
        "Bijenstraat 12, 2800 Mechelen",
        "BTW BE 0456.789.123",
        "",
        "FACTUUR",
        "Factuurnummer: 2026-0917",
        "Factuurdatum: 05/10/2026",
        "Vervaldatum: 20/10/2026",
        "",
        "Opvang september 2026 - 18 dagen          312,50",
        "",
        "Totaal te betalen: EUR 312,50",
        "Te betalen op rekening BE90 3630 0123 4532",
        "met gestructureerde mededeling +++090/9337/55493+++",
    ]
    y = 800
    for r in regels:
        c.drawString(60, y, r)
        y -= 20
    c.save()


# Fictieve Crelan-uittreksels in de twee lay-outs die de vaste PDF-lezer kent. Het eerste loopt over de
# jaarwissel en over twee pagina's; na 'Nieuw saldo' staat een bericht dat niet als verrichting mag tellen.
CRELAN_NIEUW = [
    [
        "Zichtrekening Uittreksels: 1/2025 - Pagina 1/2",
        "BE68 5390 0754 7034",
        "Janssens - Maes BIC: NICABEBB",
        "Vorig saldo op 27/12/2024 1.000,00 EUR",
        "Nr Datum Beweging Waarde Bedrag",
        "101 30/12 Betaling Bancontact contactless 30/12 - 42,54",
        "COLRUYT MECHELEN-NOO, MECHELEN",
        "op 28/12/2024 om 09:34",
        "kaart nr 670375*******1234",
        "102 02/01 Overschrijving in uw voordeel 31/12 + 2.500,00",
        "Loon december",
        "Van BE71096123456769 Werkgever BV",
        "Stationsstraat 1 - 2800 Mechelen BELGIE",
        "Tussensaldo 3.457,46 EUR",
    ],
    [
        "Zichtrekening Uittreksels: 1/2025 - Pagina 2/2",
        "BE68 5390 0754 7034",
        "Janssens - Maes BIC: NICABEBB",
        "Tussensaldo 3.457,46 EUR",
        "Nr Datum Beweging Waarde Bedrag",
        "103 03/01 Overschrijving in euro Payconiq 03/01 - 27,00",
        "Payconiq 0a1b2c3d4e5f SALON VOORBEELD BV",
        "Naar NL91ABNA0417164300 SALON VOORBEELD BV via Payconiq",
        "- -",
        "104 03/01 Domiciliëring 03/01 - 79,99",
        "Mededeling : +++090/9337/55493+++",
        "ID Schuldeiser : BE12 ZZZ 0123456789",
        "Schuldeiser : PROXIMUS",
        "De op deze rekening gestorte sommen komen in aanmerking voor het systeem van depositobescherming.",
        "Nieuw saldo op 03/01/2025 3.350,47 EUR",
        "Berichten",
        "999 12/01 Dit is geen verrichting 12/01 - 9,99",
    ],
]
CRELAN_AXA = [
    [
        "comfort2bank zichtrekening (in Datum : 07-07-2024",
        "euro)",
        "IBAN BE68 5390 0754 7034",
        "BIC AXABBE22",
        "Rekeninguittreksel 2024 / 1 - p 1/2",
        "De heer en mevrouw",
        "Jan Janssens",
        "Vorig saldo +500,00",
        "datum datum",
        "verrichting valuta",
        "02-01 Overschrijving in euro van rekening BE71 0961 2345 6769 (BIC: GKCCBEBB) van 02-01 +173,20",
        "FONS (VUTG) - GROEIPAKKET",
        "TRIERSTRAAT 9 BE 1000 BRUSSEL",
        "Mededeling: /C/ Groeipakket 01/2024",
        "$$123456789-1$$ 31-12 Aankoop met AXA debetkaart - Maestro 31-12 -26,00",
        "van 26,00 EUR op 31-12-2023 om 17:07",
        "te HEMA MECHELEN met kaartnummer",
        "7500000000000016",
        "$A$12X34A567 89B-1$$ank Belgium nv maakt deel uit van de Crelan Groep - Sylvain Dupuislaan 251, 1070 Anderlecht",
        "TEL 03 286 66 00 - www.axabank.be/contact - BIC: AXABBE22",
    ],
    [
        "comfort2bank zichtrekening (in Datum : 07-07-2024",
        "euro)",
        "IBAN BE68 5390 0754 7034",
        "BIC AXABBE22",
        "Rekeninguittreksel 2024 / 1 - p 2/2",
        "datum datum",
        "verrichting valuta",
        "03-01 Overschrijving in euro naar rekening NL91 ABNA 0417 03-01 -100,00",
        "1643 00 (BIC: ABNANL2A) van",
        "Jan de Vries",
        "Mededeling: Huur januari",
        "03-01 Aankoop met AXA debetkaart - Bancontact - op 03-01-2024 om 16:38 03-01 -24,60",
        "bij MC DONALDS LIER - LIER met kaart nummer 7500000000000016",
        "Nieuw saldo +522,60",
        "$A$12X34A567 89B-1$$ank Belgium nv maakt deel uit van de Crelan Groep - Sylvain Dupuislaan 251, 1070 Anderlecht",
    ],
]


def crelan_pdf() -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        print("reportlab niet geïnstalleerd: Crelan-PDF's overgeslagen")
        return
    for naam, paginas in (("crelan_uittreksel.pdf", CRELAN_NIEUW), ("axa_uittreksel.pdf", CRELAN_AXA)):
        c = canvas.Canvas(str(HIER / naam), pagesize=A4)
        for regels in paginas:
            c.setFont("Helvetica", 8)
            y = 810
            for r in regels:
                c.drawString(30, y, r)
                y -= 14
            c.showPage()
        c.save()


if __name__ == "__main__":
    kbc_csv()
    belfius_csv()
    crelan_csv()
    coda()
    factuur_pdf()
    crelan_pdf()
    print("Voorbeeldbestanden aangemaakt in", HIER)
