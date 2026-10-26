"""AI-aanbieders testen zonder echte sleutels: elke HTTP-oproep gaat naar een nagebootste server die het
verzoek bewaart en een antwoord in het formaat van die aanbieder teruggeeft."""

import json
from dataclasses import replace

import httpx
import pytest
from conftest import lees

from boekhouding import db
from boekhouding.llm import AIFout, CategorieVoorstellen, FactuurGegevens, aanbieders, catalogus, controleer_verbinding, vraag_json
from boekhouding.llm.schemas import PdfAfschrift, plat_schema
from boekhouding.settings import PROVIDERS, AIKeuze, laad_instellingen, met_database_keuzes

ANTWOORD = {"voorstellen": [{"id": 0, "categorie_id": "boodschappen_supermarkt", "zekerheid": 0.9}]}


class NepServer:
    def __init__(self, antwoorden):
        self.antwoorden = list(antwoorden)  # functies (request) -> httpx.Response
        self.verzoeken: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.verzoeken.append(request)
        return self.antwoorden.pop(0)(request)

    def body(self, i=-1) -> dict:
        return json.loads(self.verzoeken[i].content)


@pytest.fixture
def server(monkeypatch):
    def maak(*antwoorden):
        s = NepServer(antwoorden)
        monkeypatch.setattr(aanbieders, "http_handler", s)
        return s

    return maak


def met(inst, provider, model="m", **extra):
    return replace(
        inst,
        ai_documenten=AIKeuze(provider, model),
        ai_categorisatie=AIKeuze(provider, model),
        sleutels={provider: "sk-test"},
        **extra,
    )


def json_antwoord(data, status=200):
    return lambda req: httpx.Response(status, json=data)


# --- Catalogus en instellingen ------------------------------------------------------------


def test_catalogus_dekt_alle_aanbieders():
    assert set(catalogus()) == set(PROVIDERS)
    gemini = catalogus()["gemini"]
    assert gemini.model("gemini-3.8-flash").prijs_in == 0.75
    for code, info in catalogus().items():
        for taak_model in (info.standaard_documenten, info.standaard_categorisatie):
            assert not taak_model or info.model(taak_model), f"{code}: standaardmodel {taak_model} niet in catalogus"


def test_instellingen_uit_omgeving(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_PROVIDER_CATEGORISATIE", "mistral")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.delenv("LLM_MODEL_DOCUMENTEN", raising=False)
    monkeypatch.delenv("LLM_MODEL_CATEGORISATIE", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    inst = laad_instellingen()
    assert inst.ai("documenten") == AIKeuze("anthropic", "claude-sonnet-5-5")
    assert inst.ai("categorisatie") == AIKeuze("mistral", "mistral-small-latest")
    assert inst.ai_actief("documenten") and not inst.ai_actief("categorisatie")
    assert "MISTRAL_API_KEY" in inst.provider_klaar("mistral")[1]


def test_keuzes_uit_database_gaan_voor(con, inst):
    db.bewaar_instelling(con, "ai_documenten_provider", "openai")
    db.bewaar_instelling(con, "ai_documenten_model", "")
    db.bewaar_instelling(con, "ai_categorisatie_model", "gemini-3.1-flash-lite")
    nieuw = met_database_keuzes(inst, db.lees_instellingen(con))
    assert nieuw.ai("documenten") == AIKeuze("openai", "")  # leeg model blijft leeg: de gebruiker moet kiezen
    assert nieuw.ai("categorisatie") == AIKeuze("gemini", "gemini-3.1-flash-lite")
    db.bewaar_instelling(con, "ai_documenten_model", None)
    assert met_database_keuzes(inst, db.lees_instellingen(con)).ai("documenten") == AIKeuze("openai", "gpt-6.1-sol")


def test_plat_schema_zonder_refs():
    s = plat_schema(PdfAfschrift)
    tekst = json.dumps(s)
    assert "$ref" not in tekst and "$defs" not in tekst and '"title"' not in tekst
    item = s["properties"]["verrichtingen"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


# --- Per aanbieder ---------------------------------------------------------------------------


def test_gemini(server, inst):
    s = server(json_antwoord({
        "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(ANTWOORD)}]}, "finishReason": "STOP"}],
    }))
    r = vraag_json(met(inst, "gemini", "gemini-3.5-flash-lite"), "categorisatie", CategorieVoorstellen, "test", systeem="sys")
    assert r.voorstellen[0].categorie_id == "boodschappen_supermarkt"
    assert s.verzoeken[0].url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")
    body = s.body()
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "temperature" not in body["generationConfig"]


def test_anthropic_met_pdf(server, inst):
    s = server(json_antwoord({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": json.dumps({
            "is_factuur": True, "leverancier": "Kinderopvang Het Bijenhuis", "leverancier_btw": None, "leverancier_iban": None,
            "factuurnummer": "2026-0917", "factuurdatum": "2026-10-05", "vervaldatum": None, "bedrag_totaal": 312.5,
            "bedrag_btw": None, "munt": "EUR", "gestructureerde_mededeling": None, "omschrijving": None, "categorie_id": None,
        })}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10},
    }))
    r = vraag_json(met(inst, "anthropic", "claude-sonnet-5-5"), "documenten", FactuurGegevens, "lees",
                   bijlage=lees("factuur_kinderopvang.pdf"), mime_type="application/pdf", systeem="sys")
    assert r.bedrag_totaal == 312.5
    req = s.verzoeken[0]
    assert req.url.path == "/v1/messages"
    assert "sk-test" in (req.headers.get("x-api-key", "") + req.headers.get("authorization", ""))
    body = s.body()
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["max_tokens"] <= 21000  # zonder streaming
    blok = body["messages"][0]["content"][0]
    assert blok["type"] == "document" and blok["source"]["media_type"] == "application/pdf"
    assert body["system"] == "sys"


def _openai_response(data: dict) -> dict:
    return {
        "id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-6-luna", "status": "completed",
        "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(data), "annotations": []}]}],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    }


def test_openai_met_pdf(server, inst):
    s = server(json_antwoord(_openai_response(ANTWOORD)))
    r = vraag_json(met(inst, "openai", "gpt-6-luna"), "documenten", CategorieVoorstellen, "test",
                   bijlage=lees("factuur_kinderopvang.pdf"), mime_type="application/pdf", bestandsnaam="f.pdf")
    assert r.voorstellen[0].zekerheid == 0.9
    assert s.verzoeken[0].url.path == "/v1/responses"
    body = s.body()
    assert body["text"]["format"]["type"] == "json_schema"
    deel = body["input"][0]["content"][0]
    assert deel["type"] == "input_file" and deel["file_data"].startswith("data:application/pdf;base64,")


def test_azure_gebruikt_v1_endpoint_en_deployment(server, inst):
    s = server(json_antwoord(_openai_response(ANTWOORD)))
    a = met(inst, "azure_openai", "mijn-gpt-deployment", azure_endpoint="https://mijnres.openai.azure.com")
    vraag_json(a, "categorisatie", CategorieVoorstellen, "test")
    assert str(s.verzoeken[0].url) == "https://mijnres.openai.azure.com/openai/v1/responses"
    assert s.body()["model"] == "mijn-gpt-deployment"


def test_openai_weigert_pdf_dan_tekst(server, inst):
    s = server(
        json_antwoord({"error": {"message": "Invalid input: file type pdf is not supported for this model", "type": "invalid_request_error"}}, 400),
        json_antwoord(_openai_response(ANTWOORD)),
    )
    vraag_json(met(inst, "openai", "gpt-6-luna"), "documenten", CategorieVoorstellen, "test",
               bijlage=lees("factuur_kinderopvang.pdf"), mime_type="application/pdf")
    tweede = s.body(1)
    assert tweede["input"][0]["content"][0]["type"] == "input_text"
    assert "Kinderopvang Het Bijenhuis" in tweede["input"][0]["content"][0]["text"]


def _chat_antwoord(data: dict) -> dict:
    return {"id": "c1", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(data)}}]}


def test_mistral_ocr_dan_chat(server, inst):
    s = server(
        json_antwoord({"pages": [{"index": 0, "markdown": "# Factuur\nTotaal te betalen: EUR 312,50"}], "model": "mistral-ocr-latest"}),
        json_antwoord(_chat_antwoord(ANTWOORD)),
    )
    vraag_json(met(inst, "mistral", "mistral-medium-latest"), "documenten", CategorieVoorstellen, "lees",
               bijlage=lees("factuur_kinderopvang.pdf"), mime_type="application/pdf")
    ocr, chat = s.body(0), s.body(1)
    assert s.verzoeken[0].url.path == "/v1/ocr" and ocr["model"] == "mistral-ocr-latest"
    assert ocr["document"]["document_url"].startswith("data:application/pdf;base64,")
    assert s.verzoeken[1].url.path == "/v1/chat/completions"
    assert chat["response_format"]["type"] == "json_schema" and chat["response_format"]["json_schema"]["strict"] is True
    assert "Totaal te betalen" in chat["messages"][-1]["content"]


def test_compat_valt_terug_op_json_object_en_pdf_tekst(server, inst):
    s = server(
        json_antwoord({"error": "response_format json_schema is not supported"}, 400),
        lambda req: httpx.Response(200, json=_chat_antwoord({}) | {"choices": [{"message": {"content": "```json\n" + json.dumps(ANTWOORD) + "\n```"}}]}),
    )
    compat = replace(met(inst, "openai_compatibel", "qwen3:8b"), compat_base_url="http://localhost:11434/v1", sleutels={})
    r = vraag_json(compat, "documenten", CategorieVoorstellen, "lees", bijlage=lees("factuur_kinderopvang.pdf"), mime_type="application/pdf")
    assert r.voorstellen[0].categorie_id == "boodschappen_supermarkt"
    assert str(s.verzoeken[0].url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in s.verzoeken[0].headers
    tweede = s.body(1)
    assert tweede["response_format"] == {"type": "json_object"}
    assert "Kinderopvang Het Bijenhuis" in tweede["messages"][-1]["content"]  # tekst uit de PDF
    assert "Antwoord uitsluitend met JSON" in tweede["messages"][-1]["content"]


def test_tijdelijke_fout_wordt_herhaald(server, inst, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = server(json_antwoord({"error": "overloaded"}, 503), json_antwoord(_chat_antwoord(ANTWOORD)))
    vraag_json(met(inst, "mistral", "mistral-small-latest"), "categorisatie", CategorieVoorstellen, "x")
    assert len(s.verzoeken) == 2


def test_niet_ingesteld(inst):
    with pytest.raises(AIFout, match="ANTHROPIC_API_KEY"):
        vraag_json(replace(inst, ai_categorisatie=AIKeuze("anthropic", "claude-haiku-5-5")), "categorisatie", CategorieVoorstellen, "x")
    ok, bericht = controleer_verbinding(inst, "documenten")
    assert not ok and "GEMINI_API_KEY" in bericht


def test_verbindingstest(server, inst):
    server(json_antwoord(_chat_antwoord({"antwoord": "ok"})))
    ok, bericht = controleer_verbinding(met(inst, "mistral", "mistral-small-latest"), "categorisatie")
    assert ok and "mistral:mistral-small-latest" in bericht


def test_geweigerde_sleutel_geeft_duidelijke_melding(server, inst):
    server(json_antwoord({"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}, 400))
    with pytest.raises(AIFout, match="GEMINI_API_KEY in .env"):
        vraag_json(met(inst, "gemini", "gemini-3.8-flash"), "categorisatie", CategorieVoorstellen, "x")
