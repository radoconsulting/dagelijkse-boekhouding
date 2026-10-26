"""Catalogus van AI-aanbieders en hun modellen (boekhouding/standaard/modellen.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

PAD = Path(__file__).resolve().parent.parent / "standaard" / "modellen.yaml"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    prijs_in: float | None = None
    prijs_uit: float | None = None
    gratis: bool | None = None
    rol: str = "beide"
    status: str = "stabiel"
    context: str | None = None
    opmerking: str = ""

    def label(self) -> str:
        delen = [self.id]
        if self.prijs_in is not None:
            delen.append(f"${self.prijs_in:g} / ${self.prijs_uit:g}")
        if self.status != "stabiel":
            delen.append(self.status)
        return " · ".join(delen)


@dataclass(frozen=True)
class AanbiederInfo:
    code: str
    label: str
    sleutel: str
    pakket: str
    pdf: bool
    afbeeldingen: bool
    standaard_documenten: str
    standaard_categorisatie: str
    privacy: str = ""
    link: str = ""
    sleutel_optioneel: bool = False
    model_is_deployment: bool = False
    extra_omgeving: tuple[str, ...] = ()
    modellen: tuple[ModelInfo, ...] = field(default_factory=tuple)

    def model(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.modellen if m.id == model_id), None)


@lru_cache(maxsize=1)
def catalogus() -> dict[str, AanbiederInfo]:
    ruw = yaml.safe_load(PAD.read_text(encoding="utf-8"))
    uit = {}
    for code, a in ruw.items():
        modellen = tuple(
            ModelInfo(
                id=m["id"],
                prijs_in=(m.get("prijs") or [None, None])[0],
                prijs_uit=(m.get("prijs") or [None, None])[1],
                gratis=m.get("gratis"),
                rol=m.get("rol", "beide"),
                status=m.get("status", "stabiel"),
                context=m.get("context"),
                opmerking=m.get("opmerking", ""),
            )
            for m in a.get("modellen") or []
        )
        uit[code] = AanbiederInfo(
            code=code,
            label=a["label"],
            sleutel=a["sleutel"],
            pakket=a.get("pakket", ""),
            pdf=bool(a.get("pdf")),
            afbeeldingen=bool(a.get("afbeeldingen")),
            standaard_documenten=a.get("standaard_documenten") or "",
            standaard_categorisatie=a.get("standaard_categorisatie") or "",
            privacy=a.get("privacy", ""),
            link=a.get("link", ""),
            sleutel_optioneel=bool(a.get("sleutel_optioneel")),
            model_is_deployment=bool(a.get("model_is_deployment")),
            extra_omgeving=tuple(a.get("extra_omgeving") or ()),
            modellen=modellen,
        )
    return uit


def aanbieder(code: str) -> AanbiederInfo:
    try:
        return catalogus()[code]
    except KeyError:
        raise ValueError(f"Onbekende AI-aanbieder '{code}'. Kies uit: {', '.join(catalogus())}") from None
