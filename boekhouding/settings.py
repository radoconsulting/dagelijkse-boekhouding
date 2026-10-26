"""Instellingen: paden en AI-configuratie, gelezen uit .env of omgevingsvariabelen.

AI-keuze per taak ('documenten' = PDF's en foto's lezen, 'categorisatie' = transacties categoriseren):
  LLM_PROVIDER                   standaardaanbieder voor beide taken (gemini, anthropic, openai, azure_openai,
                                 mistral, openai_compatibel); standaard gemini
  LLM_PROVIDER_DOCUMENTEN        aanbieder enkel voor documenten (optioneel)
  LLM_PROVIDER_CATEGORISATIE     aanbieder enkel voor categorisatie (optioneel)
  LLM_MODEL_DOCUMENTEN           model (of Azure-deployment) voor documenten (optioneel)
  LLM_MODEL_CATEGORISATIE        model voor categorisatie (optioneel)
Keuzes die je in de app maakt (pagina AI-modellen) worden in de database bewaard en gaan voor op .env.
API-sleutels staan enkel in .env, nooit in de database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

TAKEN = ("documenten", "categorisatie")
PROVIDERS = ("gemini", "anthropic", "openai", "azure_openai", "mistral", "openai_compatibel")

SLEUTEL_VARIABELEN = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "azure_openai": ("AZURE_OPENAI_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "openai_compatibel": ("OPENAI_COMPAT_API_KEY",),
}


def _pad(waarde: str | os.PathLike, basis: Path = PROJECT_ROOT) -> Path:
    p = Path(waarde)
    return p if p.is_absolute() else (basis / p).resolve()


def _env(*namen: str) -> str | None:
    for n in namen:
        w = (os.getenv(n) or "").strip()
        if w:
            return w
    return None


@dataclass(frozen=True)
class AIKeuze:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class Instellingen:
    data_dir: Path
    inbox_dir: Path
    ai_documenten: AIKeuze
    ai_categorisatie: AIKeuze
    sleutels: dict = field(default_factory=dict)  # provider -> API-sleutel
    azure_endpoint: str | None = None
    compat_base_url: str | None = None
    mistral_ocr_model: str = "mistral-ocr-latest"
    pdf_als_tekst: bool = False

    @property
    def db_pad(self) -> Path:
        return self.data_dir / "boekhouding.duckdb"

    @property
    def archief_dir(self) -> Path:
        return self.data_dir / "archief"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    def maak_mappen(self) -> None:
        for p in (self.data_dir, self.inbox_dir, self.archief_dir, self.export_dir):
            p.mkdir(parents=True, exist_ok=True)

    # --- AI -----------------------------------------------------------------------

    def ai(self, taak: str) -> AIKeuze:
        if taak not in TAKEN:
            raise ValueError(f"Onbekende taak '{taak}'")
        return self.ai_documenten if taak == "documenten" else self.ai_categorisatie

    def sleutel(self, provider: str) -> str | None:
        return self.sleutels.get(provider)

    def provider_klaar(self, provider: str) -> tuple[bool, str]:
        """(klaar, uitleg) — is deze aanbieder bruikbaar met de huidige .env?"""
        if provider not in PROVIDERS:
            return False, f"onbekende aanbieder '{provider}'"
        if provider == "openai_compatibel":
            return (True, "ok") if self.compat_base_url else (False, "OPENAI_COMPAT_BASE_URL ontbreekt in .env")
        if not self.sleutel(provider):
            return False, f"{SLEUTEL_VARIABELEN[provider][0]} ontbreekt in .env"
        if provider == "azure_openai" and not self.azure_endpoint:
            return False, "AZURE_OPENAI_ENDPOINT ontbreekt in .env"
        return True, "ok"

    def ai_actief(self, taak: str) -> bool:
        keuze = self.ai(taak)
        return bool(keuze.model) and self.provider_klaar(keuze.provider)[0]

    def met_keuze(self, taak: str, provider: str, model: str) -> "Instellingen":
        keuze = AIKeuze(provider, model)
        return replace(self, **{f"ai_{taak}": keuze})


def standaard_model(provider: str, taak: str) -> str:
    from .llm.catalogus import aanbieder

    info = aanbieder(provider)
    return (info.standaard_documenten if taak == "documenten" else info.standaard_categorisatie) or ""


def _keuze_uit_env(taak: str) -> AIKeuze:
    provider = (_env(f"LLM_PROVIDER_{taak.upper()}", "LLM_PROVIDER") or "gemini").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER '{provider}' is onbekend. Kies uit: {', '.join(PROVIDERS)}")
    model = _env(f"LLM_MODEL_{taak.upper()}")
    if not model and provider == "gemini":
        model = _env(f"GEMINI_MODEL_{taak.upper()}")  # oude naam blijft werken
    return AIKeuze(provider, model or standaard_model(provider, taak))


def laad_instellingen() -> Instellingen:
    return Instellingen(
        data_dir=_pad(os.getenv("BOEKHOUDING_DATA_DIR", "data")),
        inbox_dir=_pad(os.getenv("BOEKHOUDING_INBOX_DIR", "inbox")),
        ai_documenten=_keuze_uit_env("documenten"),
        ai_categorisatie=_keuze_uit_env("categorisatie"),
        sleutels={p: k for p, namen in SLEUTEL_VARIABELEN.items() if (k := _env(*namen))},
        azure_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        compat_base_url=_env("OPENAI_COMPAT_BASE_URL"),
        mistral_ocr_model=_env("MISTRAL_OCR_MODEL") or "mistral-ocr-latest",
        pdf_als_tekst=(_env("LLM_PDF_ALS_TEKST") or "").lower() in ("1", "true", "ja", "yes"),
    )


def met_database_keuzes(inst: Instellingen, keuzes: dict[str, str]) -> Instellingen:
    """Leg de in de app gemaakte keuzes (tabel 'instellingen') over de .env-waarden."""
    for taak in TAKEN:
        provider = keuzes.get(f"ai_{taak}_provider")
        model = keuzes.get(f"ai_{taak}_model")
        if provider in PROVIDERS:
            inst = inst.met_keuze(taak, provider, model if model is not None else standaard_model(provider, taak))
        elif model:
            inst = inst.met_keuze(taak, inst.ai(taak).provider, model)
    return inst
