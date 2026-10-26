"""Gestructureerde antwoorden die de app van een taalmodel verwacht.

Velden hebben bewust geen standaardwaarden en optionele velden zijn 'X | None' zonder default:
zo zijn ze bruikbaar als strikt JSON-schema bij elke aanbieder (Gemini, Claude, OpenAI, Mistral, ...).
"""

from __future__ import annotations

from pydantic import BaseModel


class PdfVerrichting(BaseModel):
    boekingsdatum: str  # YYYY-MM-DD
    valutadatum: str | None
    bedrag: float  # negatief = uitgave/debet
    tegenpartij_naam: str | None
    tegenpartij_iban: str | None
    omschrijving: str | None
    mededeling: str | None
    gestructureerde_mededeling: str | None


class PdfAfschrift(BaseModel):
    bank: str | None
    rekening_iban: str | None
    afschriftnummer: str | None
    beginsaldo: float | None
    eindsaldo: float | None
    verrichtingen: list[PdfVerrichting]


class KaartVerrichting(BaseModel):
    datum: str  # aankoopdatum YYYY-MM-DD
    boekingsdatum: str | None
    handelaar: str
    bedrag: float  # negatief = aankoop of kosten, positief = terugbetaling of creditering
    origineel_bedrag: float | None
    originele_munt: str | None
    land: str | None


class KaartAfrekening(BaseModel):
    bank: str | None
    kaartnaam: str | None  # bv. 'KBC Mastercard Gold'
    kaart_laatste_cijfers: str | None
    periode_van: str | None
    periode_tot: str | None
    afrekeningsdatum: str | None  # datum waarop het totaal van de zichtrekening gaat
    totaal_te_betalen: float | None  # positief
    debet_rekening_iban: str | None
    verrichtingen: list[KaartVerrichting]


class FactuurGegevens(BaseModel):
    is_factuur: bool
    leverancier: str | None
    leverancier_btw: str | None
    leverancier_iban: str | None
    factuurnummer: str | None
    factuurdatum: str | None  # YYYY-MM-DD
    vervaldatum: str | None
    bedrag_totaal: float | None  # te betalen, incl. btw
    bedrag_btw: float | None
    munt: str | None
    gestructureerde_mededeling: str | None
    omschrijving: str | None
    categorie_id: str | None


class CategorieVoorstel(BaseModel):
    id: int
    categorie_id: str
    zekerheid: float


class CategorieVoorstellen(BaseModel):
    voorstellen: list[CategorieVoorstel]


class Ping(BaseModel):
    antwoord: str


def plat_schema(model: type[BaseModel]) -> dict:
    """JSON-schema zonder $ref/$defs en titels, met additionalProperties=false en alle velden verplicht.
    Dat is de strengste gemene deler die OpenAI-compatibele endpoints (Mistral, Ollama, ...) aanvaarden."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def los(s):
        if isinstance(s, list):
            return [los(x) for x in s]
        if not isinstance(s, dict):
            return s
        if "$ref" in s:
            return los(defs[s["$ref"].rsplit("/", 1)[-1]])
        uit = {}
        for k, v in s.items():
            if k in ("title", "default"):
                continue
            if k == "properties":
                uit[k] = {naam: los(sub) for naam, sub in v.items()}
            else:
                uit[k] = los(v)
        if uit.get("type") == "object":
            uit["additionalProperties"] = False
            uit["required"] = list(uit.get("properties", {}))
        return uit

    return los(schema)
