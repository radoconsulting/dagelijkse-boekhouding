# Dagelijkse boekhouding

Een lichte boekhoudapp voor het gezin. Je laadt rekeningafschriften en facturen op, de app leest ze in,
haalt dubbels eruit, categoriseert de verrichtingen en toont waar het geld naartoe gaat. Alles draait lokaal
op je pc in één DuckDB-bestand. Excel werkt als tweede interface: je exporteert, past categorieën en regels
aan in Excel en leest de wijzigingen weer in. Voor PDF's, foto's en lastige transacties gebruikt de app een
AI-model naar keuze: Gemini, Claude, OpenAI, Azure OpenAI, Mistral of een lokaal model via Ollama.

## Wat het kan

| Bron | Formaat | Hoe |
|---|---|---|
| KBC / CBC | CSV-export | kolommen op naam herkend, lopend saldo gecontroleerd |
| Belfius | CSV-export (met de inleidende regels) | idem, cp1252-codering |
| Crelan | CSV-export | idem, punt als decimaalteken |
| Elke Belgische bank | CODA (`.cod`) | Febelfin-standaard, saldo-aansluiting per uittreksel |
| Crelan (en AXA Bank vóór de overstap) | PDF-uittreksel | vaste lezer zonder AI, een fractie van een seconde per bestand, gecontroleerd tegen vorig en nieuw saldo |
| Andere banken | PDF-rekeningafschrift | gelezen door het gekozen AI-model, gecontroleerd tegen begin- en eindsaldo |
| Kredietkaarten | PDF-afrekening (uitgavenstaat) | AI leest de aankopen; de afrekening wordt gekoppeld aan het totaal op je zichtrekening |
| Facturen | PDF of foto | AI (of zonder AI: tekstherkenning voor OGM, IBAN, totaal, datums) |

- **Dubbels herkennen tussen formaten.** Elke verrichting krijgt een sleutel op basis van rekening, boekingsdatum,
  bedrag en volgnummer. Dezelfde betaling uit een CSV en uit een CODA-bestand komt er dus maar één keer in.
- **Categoriseren in lagen.** Eerst herkent de app overboekingen tussen je eigen rekeningen. Daarna passen
  regels (regex op tegenpartij, omschrijving of mededeling, met optioneel richting en bedraggrenzen). Wat dan
  nog geen categorie heeft, kan het AI-model voorstellen, met een zekerheidsscore. Wat jij zelf kiest, in de app of
  in Excel, wordt nooit overschreven.
- **Leren uit je keuzes.** Geef je een tegenpartij een paar keer handmatig dezelfde categorie, dan stelt de app
  er een regel voor.
- **Facturen koppelen aan betalingen**: eerst via de gestructureerde mededeling (+++…+++), anders via bedrag en
  IBAN of naam van de leverancier. Een openstaande factuur wordt gekoppeld zodra de betaling binnenkomt.
- **Rapporten**: inkomsten, uitgaven, sparen/beleggen en netto per periode, per maand, per categorie en per tag.
- **Kredietkaarten zonder dubbeltellingen.** De aankopen van een kaartafrekening komen op een aparte
  kaartrekening (bv. `KAART-KBC-1234`) en worden gewoon gecategoriseerd. Het totaal dat later van je
  zichtrekening gaat, wordt herkend (bedrag, datum en woorden als "Mastercard" of "kredietkaart") en telt als
  interne overboeking. Heb je de afrekening (nog) niet ingelezen, dan staat dat totaal onder
  *Diversen / Kredietkaart (zonder detail)*.
- **Budgetten per categorie** met maandbedrag, optioneel meenemen van over- of onderbesteding, een waarschuwing
  vanaf 80%, een voorstel op basis van je gemiddelde uitgaven en een vergelijking met de vorige 3 maanden en
  dezelfde maand vorig jaar.
- **Vaste lasten en abonnementen** worden vanzelf herkend: dezelfde tegenpartij met een regelmatig ritme
  (wekelijks tot jaarlijks) en een stabiel bedrag. Je ziet wat ze per maand kosten, wanneer de volgende betaling
  verwacht wordt, wat duurder werd (vanaf 5%) en wat gestopt lijkt.
- **Saldovooruitzicht** voor 30, 60 of 90 dagen: huidig saldo + verwachte vaste posten + je gemiddelde
  dagelijkse uitgaven, met het laagste punt en een waarschuwing als je onder nul dreigt te gaan.
- **Nakijklijst**: nieuw geïmporteerde verrichtingen staan op "na te kijken" tot je ze afvinkt (of zelf een
  categorie kiest). Zo weet je wat je al gecontroleerd hebt.
- **Splitsen en tags.** Verdeel één betaling over meerdere categorieën (bv. een supermarktticket met
  boodschappen en huishoudartikelen); rapporten en budgetten tellen dan per deel. Tags zoals `vakantie 2026` of
  `verbouwing` gaan dwars door de categorieën heen.

## Installatie (Windows)

1. Python 3.11 of nieuwer moet geïnstalleerd zijn.
2. Dubbelklik `setup.bat`. Dat maakt een virtuele omgeving `.venv`, installeert de pakketten en maakt `.env` aan.
3. Optioneel: zet een AI-sleutel in `.env`, bijvoorbeeld `GEMINI_API_KEY=...` (aan te maken op https://aistudio.google.com/apikey).
   Zie [AI-aanbieders en modellen](#ai-aanbieders-en-modellen) voor de andere aanbieders.
4. Dubbelklik `start.bat`. De app opent in je browser.

Met uv kan het ook: `uv venv && uv pip install -e ".[dev,ai]"` en daarna `uv run streamlit run app/main.py`.
Het extra `ai` installeert de SDK's van Anthropic en OpenAI; Gemini, Mistral en OpenAI-compatibele endpoints
werken ook zonder.

## Veel bestanden tegelijk

Je kan honderden afschriften in één keer opladen. De pagina toont per bestand de voortgang en het resultaat, en
op het einde een tabel. AI-categorisatie gebeurt één keer voor alle nieuwe transacties samen (maximaal 1.000 per
import; de rest via **Transacties**). Met **Stop** rechtsboven onderbreek je de reeks: wat al ingelezen is, blijft
bewaard, en opnieuw opladen is veilig.

Een AI-oproep duurt nooit langer dan 3 minuten. Lukt het niet, dan krijgt dat ene bestand een foutmelding en gaat
de import verder. Bij een limiet van de aanbieder (429) wacht de app de tijd die de aanbieder opgeeft.

## Dagelijks gebruik

1. Exporteer je verrichtingen uit je online bankieren als CSV (of vraag CODA aan bij je bank).
2. Sleep de bestanden in de app (pagina **Importeren**) of zet ze in de map `inbox/` en klik **Verwerk inbox**.
3. Kredietkaart: laad de maandelijkse afrekening als PDF op (keuze *kredietkaartafrekeningen*). Daarvoor is een
   AI-model voor documenten nodig.
4. Ga naar **Transacties** met *Nog na te kijken* aan. Pas categorieën, tags of notities aan, splits wat nodig is
   en vink af wat klopt.
5. Bekijk het resultaat op **Overzicht**, **Budgetten** en **Vaste lasten & vooruitzicht**.

Eenmalig instellen: geef je rekeningen een naam en, als je bankbestand geen lopend saldo bevat, een gekend saldo
op een datum (**Categorieën & regels → Rekeningen**). Dat saldo is de basis van het vooruitzicht. Een
Belfius-export met de inleidende regels geeft het saldo zelf mee.

Opdrachtregel (handig voor scripts of een geplande taak):

```bat
.venv\Scripts\python -m boekhouding importeer pad\naar\export.csv
.venv\Scripts\python -m boekhouding importeer --pdf kredietkaart pad\naar\uitgavenstaat.pdf
.venv\Scripts\python -m boekhouding inbox --ai
.venv\Scripts\python -m boekhouding categoriseer --ai
.venv\Scripts\python -m boekhouding excel-export
.venv\Scripts\python -m boekhouding excel-import data\exports\boekhouding_20261026_1000.xlsx
.venv\Scripts\python -m boekhouding status
.venv\Scripts\python -m boekhouding budget 2026-10
.venv\Scripts\python -m boekhouding vaste-lasten
.venv\Scripts\python -m boekhouding ai-test
.venv\Scripts\python -m boekhouding ai-modellen mistral
.venv\Scripts\python -m boekhouding ai-kies categorisatie mistral mistral-small-latest
```

## AI-aanbieders en modellen

De app gebruikt AI voor twee taken, en per taak kies je een aanbieder en een model (pagina **AI-modellen**, of
`LLM_PROVIDER` / `LLM_MODEL_DOCUMENTEN` / `LLM_MODEL_CATEGORISATIE` in `.env`):

- **Documenten lezen**: PDF-afschriften, facturen en foto's. Hier telt nauwkeurigheid.
- **Categoriseren**: transacties waarvoor geen regel past. Hier volstaat een klein, goedkoop model.

| Aanbieder | `.env` | Documenten (standaard) | Categoriseren (standaard) | PDF |
|---|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | gemini-3.8-flash | gemini-3.5-flash-lite | zelf |
| Anthropic Claude | `ANTHROPIC_API_KEY` | claude-sonnet-5-5 | claude-haiku-5-5 | zelf |
| OpenAI | `OPENAI_API_KEY` | gpt-6.1-sol | gpt-6-luna | zelf |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` | jouw deployment | jouw deployment | zelf |
| Mistral (EU) | `MISTRAL_API_KEY` | mistral-medium-latest | mistral-small-latest | via Mistral OCR |
| OpenAI-compatibel (Ollama, LM Studio, OpenRouter) | `OPENAI_COMPAT_BASE_URL` (+ sleutel) | zelf kiezen | zelf kiezen | tekst uit de PDF |

Je kan aanbieders mengen, bijvoorbeeld Gemini voor documenten en Mistral of een lokaal Ollama-model voor
categorisatie. De catalogus met prijzen, gratis tiers en uitfaseringsdata staat in
`boekhouding/standaard/modellen.yaml` (stand 26/10/2026). Op de pagina AI-modellen kan je ook live opvragen
welke modellen je sleutel kan gebruiken, en elk ander model invullen.

Hoe het werkt: elke aanbieder krijgt hetzelfde strikte JSON-schema mee (structured output), dus het antwoord
past altijd in de tabellen. Leest een aanbieder geen PDF's, dan stuurt de app de tekst uit de PDF mee. Weigert
een model onverwacht een PDF, dan probeert de app het één keer opnieuw met de tekst. Tijdelijke fouten (429,
5xx) worden automatisch herhaald.

## Excel

Op de pagina **Excel** (of met `excel-export`) maak je één werkmap met deze tabbladen: Overzicht (formules
per maand en categorie), Transacties, Budgetten, Categorieën, Regels, Rekeningen en Facturen. Kolommen met een
gele kop mag je aanpassen. Daarna lees je het bestand weer in. Het tabblad Overzicht rekent met `SUMIFS` op de
transacties, dus het volgt je aanpassingen meteen, nog voor je het bestand inleest.

- **Transacties**: categorie, notitie en tags (komma-gescheiden). Een gesplitste transactie staat er als één rij
  per deel, met een id als `abc123#2`; per deel pas je categorie, notitie en tags aan. Splitsen zelf doe je in de app.
  Een categorie die je in Excel kiest, telt ook als nagekeken.
- **Budgetten**: maandbudget en meenemen per uitgavencategorie, met het gemiddelde van de laatste 6 maanden en een
  voorstel ernaast. Een leeg of nul budget verwijdert het.
- **Rekeningen**: naam, eigenaar, soort (zicht, spaar, kredietkaart) en een gekend saldo op een datum.

## De data zelf bevragen

Alles staat in `data/boekhouding.duckdb`. De view `v_transacties` geeft één rij per verrichting met categorie,
soort, maand en rekeningnaam. Voor analyses gebruik je beter `v_boekingen`: daarin is een gesplitste transactie
opgesplitst in haar delen (`boeking_id` = `txn_id#volgnr`), zodat sommen per categorie kloppen. Tags zijn een
lijst (`VARCHAR[]`).

```sql
SELECT maand, hoofdcategorie, -sum(bedrag) AS uitgegeven
FROM v_boekingen
WHERE soort = 'uitgave'
GROUP BY ALL ORDER BY maand, uitgegeven DESC;

-- Alles met de tag 'vakantie 2026'
SELECT boekingsdatum, bedrag, tegenpartij_naam, categorie_label
FROM v_boekingen WHERE list_contains(tags, 'vakantie 2026');
```

Sluit de app eerst, of open het bestand alleen-lezen (`duckdb -readonly data/boekhouding.duckdb`):
DuckDB laat maar één proces tegelijk schrijven.

| Tabel | Inhoud |
|---|---|
| `transacties` | één rij per verrichting, met `categorie_bron` = regel / ai / handmatig / excel / factuur / kaart / gesplitst / intern, `ai_model`, `nagekeken`, `tags`, `notitie` |
| `splitsingen` | de delen van gesplitste transacties (bedrag, categorie, notitie, tags) |
| `kaartafrekeningen` | ingelezen kredietkaartafrekeningen en de betaling op de zichtrekening waaraan ze gekoppeld zijn |
| `budgetten` | maandbudget per categorie, meenemen ja/nee, vanaf welke maand |
| `terugkerend_keuzes` | vaste lasten die je verborgen hebt |
| `categorieen` | hoofd- en subcategorie, soort (inkomst, uitgave, sparen, transfer) |
| `regels` | patroon, veld, teken, bedraggrenzen, prioriteit |
| `facturen` | geëxtraheerde factuurgegevens en de gekoppelde betaling |
| `rekeningen`, `imports` | eigen rekeningen (soort, gekend saldo) en importhistoriek |
| `instellingen` | AI-keuzes uit de app (sleutels staan nooit in de database) |

## Privacy

- Je gegevens blijven lokaal in `data/`. Die map en `.env` staan in `.gitignore`.
- Naar de AI-aanbieder gaan alleen PDF's en foto's die je zelf oplaadt, en voor categorisatie per transactie:
  datum, bedrag, tegenpartij, omschrijving en mededeling. Rekeningnummers gaan niet mee.
- Gemini: voor gebruikers in de EER, dus ook in België, gelden volgens de
  [Gemini API-voorwaarden](https://ai.google.dev/terms) de datavoorwaarden van de betaalde dienst ook voor gratis
  gebruik: Google gebruikt je prompts en antwoorden niet om zijn producten te verbeteren. Ze worden wel een
  beperkte tijd gelogd om misbruik op te sporen.
- Wil je dat er niets je pc verlaat, gebruik dan Ollama of LM Studio via `openai_compatibel`. Wil je een
  Europese aanbieder, kies Mistral of Azure OpenAI in een EU-regio.
- Zonder AI-sleutel werkt de app volledig offline, maar zonder PDF-afschriften en slimme categorisatie.

## Ontwikkelen

```bat
.venv\Scripts\python -m pytest
```

De testbestanden in `tests/fixtures/` zijn fictief en worden gemaakt door `tests/fixtures/maak_voorbeelden.py`.
Een nieuwe bank toevoegen: voeg een `CsvProfiel` toe in `boekhouding/importers/csv_banken.py` met de kolomnamen
en de velden waaraan het profiel herkend wordt. Een nieuw AI-model toevoegen: zet het in
`boekhouding/standaard/modellen.yaml`. De AI-tests (`tests/test_llm.py`) bootsen elke API na en controleren de
echte request-vorm van de SDK's, zonder sleutels.

```
boekhouding/
  importers/      csv_banken.py, coda.py, pdf.py, crelan_pdf.py (vaste lezer voor Crelan- en AXA Bank-PDF's)
  pipeline.py     inlezen -> ontdubbelen -> opslaan -> categoriseren -> facturen en kaartafrekeningen koppelen
  llm/            vraag_json() + aanbieders.py (Gemini, Claude, OpenAI, Azure, Mistral, compat), catalogus, schema's
  categoriseren.py, facturen.py, kredietkaart.py, excel.py, rapporten.py, db.py, cli.py
  budget.py       budgetstatus, meenemen, voorstel, maandvergelijking
  terugkerend.py  vaste lasten herkennen, prijsstijgingen, saldovooruitzicht
  saldi.py        huidig saldo per rekening
  beheer.py       nakijken, categorie, tags, notities, splitsen
  standaard/      standaardcategorieën en -regels, modellen.yaml (AI-catalogus)
app/              Streamlit: main.py + paginas/
```
