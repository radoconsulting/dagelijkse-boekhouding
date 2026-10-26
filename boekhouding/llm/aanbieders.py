"""Implementaties per AI-aanbieder. Elke functie krijgt een opdracht (+ optionele bijlage) en geeft een
gevalideerd Pydantic-object terug volgens het gevraagde schema.

- gemini            google-genai SDK, PDF en foto's rechtstreeks, response_schema
- anthropic         anthropic SDK, document-/image-blokken, messages.parse(output_format=...)
- openai            openai SDK, Responses API, input_file/input_image, responses.parse(text_format=...)
- azure_openai      idem via het v1-endpoint van je Azure-resource; model = deploymentnaam
- mistral           REST: eerst Mistral OCR voor PDF/foto, dan chat/completions met json_schema
- openai_compatibel REST: chat/completions met json_schema (valt terug op json_object); voor Ollama,
                    LM Studio, OpenRouter, vLLM, ...
"""

from __future__ import annotations

import base64
import importlib
import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ..settings import Instellingen
from .schemas import plat_schema

MAX_TOKENS = {"documenten": 16000, "categorisatie": 8000}  # Claude: onder de grens waarboven streaming verplicht is
# Gemini telt het denkwerk mee in de uitvoertokens; een lang afschrift heeft er veel nodig.
GEMINI_MAX_TOKENS = {"documenten": 32768, "categorisatie": 8192}
# Maximale duur van één AI-oproep. Zonder grens kan één vastgelopen verbinding een hele import blokkeren.
TIMEOUT = 180.0
MISTRAL_URL = "https://api.mistral.ai/v1"

# Testhaak: functie(request) -> response die echte HTTP-oproepen vervangt. None = echte oproepen.
http_handler = None


class AIFout(RuntimeError):
    """Fout bij een AI-oproep; de boodschap is bedoeld voor de gebruiker."""

    def __init__(self, boodschap: str, *, tijdelijk: bool = False, bijlage_probleem: bool = False):
        super().__init__(boodschap)
        self.tijdelijk = tijdelijk
        self.bijlage_probleem = bijlage_probleem


@dataclass
class Bijlage:
    data: bytes
    mime_type: str
    bestandsnaam: str = "document.pdf"

    @property
    def is_pdf(self) -> bool:
        return self.mime_type == "application/pdf"

    @property
    def base64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64}"


# --- Hulpfuncties ---------------------------------------------------------------------


def _http_client(timeout: float = TIMEOUT, bibliotheek: str = "httpx"):
    """httpx-client (of httpx2 voor de Anthropic-SDK), met de testhaak als die gezet is."""
    mod = importlib.import_module(bibliotheek)
    if http_handler is None:
        return mod.Client(timeout=timeout)

    def doorgeven(request):
        r = http_handler(request)
        return r if isinstance(r, mod.Response) else mod.Response(r.status_code, content=r.content, headers=dict(r.headers))

    return mod.Client(transport=mod.MockTransport(doorgeven), timeout=timeout)


def _tijdelijk(e: Exception) -> bool:
    naam = type(e).__name__
    tekst = str(e)
    return (
        any(n in naam for n in ("RateLimit", "Timeout", "Connection", "InternalServer", "Overloaded", "ServiceUnavailable"))
        or any(c in tekst for c in ("429", "500", "502", "503", "504", "529", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded"))
    )


def _bijlage_probleem(e: Exception) -> bool:
    tekst = str(e).lower()
    return ("400" in tekst or "invalid" in tekst or "unsupported" in tekst) and any(
        w in tekst for w in ("pdf", "file", "document", "image", "mime", "media")
    )


def _vertaal(e: Exception, aanbieder: str) -> AIFout:
    if isinstance(e, AIFout):
        return e
    if isinstance(e, ImportError):
        pakket = {"gemini": "google-genai", "anthropic": "anthropic", "openai": "openai", "azure_openai": "openai"}.get(aanbieder, aanbieder)
        return AIFout(f"Het pakket '{pakket}' is niet geïnstalleerd. Voer setup.bat opnieuw uit of: pip install {pakket}")
    tekst = str(e)
    if "timeout" in type(e).__name__.lower() or "timed out" in tekst.lower():
        # Niet opnieuw proberen: dezelfde zware vraag loopt meestal opnieuw vast, en de import moet verder kunnen.
        return AIFout(f"{aanbieder}: geen antwoord binnen {TIMEOUT:.0f} seconden. Probeer later opnieuw of kies een sneller model.")
    if any(s in tekst for s in ("API key not valid", "API_KEY_INVALID", "invalid x-api-key", "Incorrect API key", "authentication_error",
                                "Unauthorized", "401", "PERMISSION_DENIED", "invalid_api_key")):
        from ..settings import SLEUTEL_VARIABELEN

        return AIFout(f"De API-sleutel voor {aanbieder} werd geweigerd. Controleer {SLEUTEL_VARIABELEN[aanbieder][0]} in .env en herstart de app.")
    if any(s in tekst for s in ("404", "NOT_FOUND", "not_found_error", "model_not_found", "DeploymentNotFound")):
        return AIFout(f"{aanbieder}: het model bestaat niet of je sleutel heeft er geen toegang toe. Kies een ander model. ({tekst[:200]})")
    return AIFout(f"{aanbieder}: {e}", tijdelijk=_tijdelijk(e), bijlage_probleem=_bijlage_probleem(e))


def _json_uit_tekst(tekst: str) -> str:
    """Haal JSON uit een antwoord dat eventueel in een ```json-codeblok staat."""
    tekst = (tekst or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", tekst, re.S)
    if m:
        return m.group(1).strip()
    start = min((i for i in (tekst.find("{"), tekst.find("[")) if i >= 0), default=0)
    return tekst[start:]


def _valideer(schema: type[BaseModel], tekst: str, aanbieder: str) -> BaseModel:
    try:
        return schema.model_validate_json(_json_uit_tekst(tekst))
    except ValidationError as e:
        raise AIFout(f"{aanbieder}: het antwoord past niet in het verwachte formaat ({e.error_count()} fouten).", tijdelijk=True) from e


# --- Gemini -------------------------------------------------------------------------------


def gemini(inst: Instellingen, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None, taak: str):
    from google import genai
    from google.genai import types

    opties = types.HttpOptions(timeout=int(TIMEOUT * 1000), httpx_client=_http_client() if http_handler else None)
    client = genai.Client(api_key=inst.sleutel("gemini"), http_options=opties)
    inhoud: list = []
    if bijlage is not None:
        inhoud.append(types.Part.from_bytes(data=bijlage.data, mime_type=bijlage.mime_type))
    inhoud.append(opdracht)
    # Geen temperature: Google raadt voor Gemini 3+ de standaardwaarde aan; het schema legt de vorm vast.
    # Overnemen en indelen vraagt weinig denkwerk: een laag denkniveau maakt de oproep vele malen sneller.
    basis = dict(system_instruction=systeem, response_mime_type="application/json", response_schema=schema,
                 max_output_tokens=GEMINI_MAX_TOKENS[taak])
    denken = {"thinking_config": types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)} if model.startswith("gemini-3") else {}
    try:
        antwoord = client.models.generate_content(model=model, contents=inhoud, config=types.GenerateContentConfig(**basis, **denken))
    except Exception as e:
        if not denken or "thinking" not in str(e).lower():
            raise
        antwoord = client.models.generate_content(model=model, contents=inhoud, config=types.GenerateContentConfig(**basis))
    if isinstance(getattr(antwoord, "parsed", None), schema):
        return antwoord.parsed
    reden = str(getattr((antwoord.candidates or [None])[0], "finish_reason", "") or "")
    if "MAX_TOKENS" in reden:
        raise AIFout("gemini: het antwoord werd te lang (document te groot of het model bleef herhalen). "
                     "Probeer een ander model of splits het document.")
    return _valideer(schema, antwoord.text, "gemini")


# --- Anthropic Claude ---------------------------------------------------------------------


def anthropic(inst: Instellingen, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None, taak: str):
    import anthropic as sdk

    client = sdk.Anthropic(
        api_key=inst.sleutel("anthropic"), max_retries=1, timeout=TIMEOUT,
        http_client=_http_client(bibliotheek="httpx2") if http_handler else None
    )
    inhoud: list[dict] = []
    if bijlage is not None:
        if bijlage.is_pdf:
            inhoud.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": bijlage.base64}})
        else:
            inhoud.append({"type": "image", "source": {"type": "base64", "media_type": bijlage.mime_type, "data": bijlage.base64}})
    inhoud.append({"type": "text", "text": opdracht})
    kwargs = dict(model=model, max_tokens=MAX_TOKENS[taak], messages=[{"role": "user", "content": inhoud}], output_format=schema)
    if systeem:
        kwargs["system"] = systeem
    antwoord = client.messages.parse(**kwargs)
    if antwoord.parsed_output is None:
        raise AIFout(f"Claude gaf geen bruikbaar antwoord (stop_reason: {antwoord.stop_reason}).", tijdelijk=antwoord.stop_reason == "max_tokens")
    return antwoord.parsed_output


# --- OpenAI en Azure OpenAI ------------------------------------------------------------------


def _azure_base_url(endpoint: str) -> str:
    e = endpoint.strip().rstrip("/")
    return e + "/" if e.endswith("/openai/v1") else e + "/openai/v1/"


def _openai_responses(client, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None):
    inhoud: list[dict] = []
    if bijlage is not None:
        if bijlage.is_pdf:
            inhoud.append({"type": "input_file", "filename": bijlage.bestandsnaam, "file_data": bijlage.data_url})
        else:
            inhoud.append({"type": "input_image", "image_url": bijlage.data_url})
    inhoud.append({"type": "input_text", "text": opdracht})
    kwargs = dict(model=model, input=[{"role": "user", "content": inhoud}], text_format=schema)
    if systeem:
        kwargs["instructions"] = systeem
    antwoord = client.responses.parse(**kwargs)
    if antwoord.output_parsed is None:
        raise AIFout("OpenAI gaf geen bruikbaar antwoord (geweigerd of afgebroken).")
    return antwoord.output_parsed


def openai(inst: Instellingen, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None, taak: str):
    from openai import OpenAI

    client = OpenAI(api_key=inst.sleutel("openai"), max_retries=1, timeout=TIMEOUT, http_client=_http_client() if http_handler else None)
    return _openai_responses(client, model, schema, opdracht, systeem, bijlage)


def azure_openai(inst: Instellingen, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None, taak: str):
    from openai import OpenAI

    client = OpenAI(
        base_url=_azure_base_url(inst.azure_endpoint or ""),
        api_key=inst.sleutel("azure_openai"),
        max_retries=1,
        timeout=TIMEOUT,
        http_client=_http_client() if http_handler else None,
    )
    return _openai_responses(client, model, schema, opdracht, systeem, bijlage)


# --- OpenAI-compatibele REST (Mistral, Ollama, LM Studio, OpenRouter, ...) --------------------


def _post(url: str, payload: dict, sleutel: str | None) -> dict:
    import httpx

    headers = {"Content-Type": "application/json"}
    if sleutel:
        headers["Authorization"] = f"Bearer {sleutel}"
    try:
        with _http_client() as client:
            r = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as e:
        raise AIFout(f"Geen antwoord van {url} binnen {TIMEOUT:.0f} seconden.") from e
    except httpx.HTTPError as e:
        raise AIFout(f"Geen verbinding met {url}: {e}", tijdelijk=True) from e
    if r.status_code >= 400:
        raise AIFout(
            f"HTTP {r.status_code} van {url}: {r.text[:400]}",
            tijdelijk=r.status_code in (408, 409, 429) or r.status_code >= 500,
            bijlage_probleem=r.status_code == 400 and any(w in r.text.lower() for w in ("image", "file", "document", "pdf")),
        )
    return r.json()


def _get(url: str, sleutel: str | None) -> dict:
    headers = {"Authorization": f"Bearer {sleutel}"} if sleutel else {}
    with _http_client(30) as client:
        r = client.get(url, headers=headers)
    if r.status_code >= 400:
        raise AIFout(f"HTTP {r.status_code} van {url}: {r.text[:300]}")
    return r.json()


def chat_compat(base_url: str, sleutel: str | None, model: str, schema, opdracht: str, systeem: str | None,
                bijlage: Bijlage | None, aanbieder: str = "openai_compatibel"):
    url = base_url.rstrip("/") + "/chat/completions"
    berichten: list[dict] = [{"role": "system", "content": systeem}] if systeem else []
    if bijlage is not None:  # enkel foto's; PDF's worden vooraf tekst
        berichten.append({"role": "user", "content": [
            {"type": "text", "text": opdracht},
            {"type": "image_url", "image_url": {"url": bijlage.data_url}},
        ]})
    else:
        berichten.append({"role": "user", "content": opdracht})
    payload = {
        "model": model,
        "messages": berichten,
        "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": plat_schema(schema), "strict": True}},
    }
    try:
        antwoord = _post(url, payload, sleutel)
    except AIFout as e:
        if "400" not in str(e) or not any(w in str(e).lower() for w in ("response_format", "json_schema", "schema")):
            raise
        # Endpoint kent geen json_schema: vraag gewoon JSON en geef het schema mee in de opdracht.
        payload["response_format"] = {"type": "json_object"}
        extra = "\n\nAntwoord uitsluitend met JSON volgens dit schema:\n" + json.dumps(plat_schema(schema), ensure_ascii=False)
        laatste = payload["messages"][-1]
        if isinstance(laatste["content"], str):
            laatste["content"] += extra
        else:
            laatste["content"][0]["text"] += extra
        antwoord = _post(url, payload, sleutel)
    try:
        tekst = antwoord["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIFout(f"{aanbieder}: onverwacht antwoord: {str(antwoord)[:300]}") from e
    if isinstance(tekst, list):  # sommige endpoints geven content-delen terug
        tekst = "".join(d.get("text", "") for d in tekst if isinstance(d, dict))
    return _valideer(schema, tekst, aanbieder)


def openai_compatibel(inst: Instellingen, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None, taak: str):
    return chat_compat(inst.compat_base_url or "", inst.sleutel("openai_compatibel"), model, schema, opdracht, systeem, bijlage)


def mistral_ocr(inst: Instellingen, bijlage: Bijlage) -> str:
    if bijlage.is_pdf:
        document = {"type": "document_url", "document_url": bijlage.data_url}
    else:
        document = {"type": "image_url", "image_url": bijlage.data_url}
    antwoord = _post(f"{MISTRAL_URL}/ocr", {"model": inst.mistral_ocr_model, "document": document}, inst.sleutel("mistral"))
    paginas = antwoord.get("pages") or []
    if not paginas:
        raise AIFout("Mistral OCR vond geen tekst in het document.")
    return "\n\n".join(f"--- pagina {p.get('index', i) + 1} ---\n{p.get('markdown', '')}" for i, p in enumerate(paginas))


def mistral(inst: Instellingen, model: str, schema, opdracht: str, systeem: str | None, bijlage: Bijlage | None, taak: str):
    if bijlage is not None:
        opdracht = f"{opdracht}\n\nInhoud van het document (OCR, markdown):\n{mistral_ocr(inst, bijlage)}"
    return chat_compat(MISTRAL_URL, inst.sleutel("mistral"), model, schema, opdracht, systeem, None, aanbieder="mistral")


IMPLEMENTATIES = {
    "gemini": gemini,
    "anthropic": anthropic,
    "openai": openai,
    "azure_openai": azure_openai,
    "mistral": mistral,
    "openai_compatibel": openai_compatibel,
}


# --- Modellen live ophalen ------------------------------------------------------------------


def lijst_modellen(inst: Instellingen, aanbieder: str) -> list[str]:
    try:
        if aanbieder == "gemini":
            from google import genai

            client = genai.Client(api_key=inst.sleutel("gemini"))
            uit = []
            for m in client.models.list():
                acties = getattr(m, "supported_actions", None) or []
                if "generateContent" in acties:
                    uit.append(m.name.removeprefix("models/"))
            return sorted(uit)
        if aanbieder == "anthropic":
            import anthropic as sdk

            return [m.id for m in sdk.Anthropic(api_key=inst.sleutel("anthropic")).models.list(limit=100)]
        if aanbieder in ("openai", "azure_openai"):
            from openai import OpenAI

            if aanbieder == "openai":
                client = OpenAI(api_key=inst.sleutel("openai"))
            else:
                client = OpenAI(base_url=_azure_base_url(inst.azure_endpoint or ""), api_key=inst.sleutel("azure_openai"))
            return sorted(m.id for m in client.models.list())
        if aanbieder == "mistral":
            return sorted(m["id"] for m in _get(f"{MISTRAL_URL}/models", inst.sleutel("mistral")).get("data", []))
        if aanbieder == "openai_compatibel":
            basis = (inst.compat_base_url or "").rstrip("/")
            return sorted(m["id"] for m in _get(f"{basis}/models", inst.sleutel("openai_compatibel")).get("data", []))
    except Exception as e:
        raise _vertaal(e, aanbieder) from e
    raise AIFout(f"Onbekende aanbieder '{aanbieder}'")
