"""DuckDB-schema, verbinding en initiële vulling van categorieën en regels."""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from .utils import slug

STANDAARD_DIR = Path(__file__).resolve().parent / "standaard"

# Bronnen van een categorie op een transactie. Handmatige keuzes worden nooit overschreven.
BRON_HANDMATIG = "handmatig"
BRON_EXCEL = "excel"
BRON_FACTUUR = "factuur"
BRON_REGEL = "regel"
BRON_AI = "ai"
BRON_INTERN = "intern"
BRON_KAART = "kaart"  # afrekening van een kredietkaart = overboeking naar de kaartrekening
BRON_GESPLITST = "gesplitst"  # de categorieën staan op de splitsingen
VASTE_BRONNEN = (BRON_HANDMATIG, BRON_EXCEL, BRON_FACTUUR, BRON_KAART, BRON_GESPLITST)

CATEGORIE_INTERN = "overboekingen_eigen_rekeningen"
CATEGORIE_KAART_ZONDER_DETAIL = "diversen_kredietkaart_zonder_detail"
SCHEMA_VERSIE = 4

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS seq_regel START 1000;

CREATE TABLE IF NOT EXISTS categorieen (
    categorie_id    VARCHAR PRIMARY KEY,
    hoofdcategorie  VARCHAR NOT NULL,
    subcategorie    VARCHAR NOT NULL,
    soort           VARCHAR NOT NULL,      -- inkomst | uitgave | sparen | transfer
    actief          BOOLEAN DEFAULT TRUE,
    volgorde        INTEGER
);

CREATE TABLE IF NOT EXISTS rekeningen (
    iban            VARCHAR PRIMARY KEY,
    bank            VARCHAR,
    naam            VARCHAR,
    eigenaar        VARCHAR,
    aangemaakt_op   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS imports (
    import_id       VARCHAR PRIMARY KEY,
    bestandsnaam    VARCHAR,
    bestand_hash    VARCHAR,
    bron            VARCHAR,               -- kbc_csv | belfius_csv | crelan_csv | coda | pdf_afschrift | factuur
    bank            VARCHAR,
    rekening_iban   VARCHAR,
    aantal_gelezen  INTEGER,
    aantal_nieuw    INTEGER,
    aantal_dubbel   INTEGER,
    saldo_controle  VARCHAR,
    geimporteerd_op TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS transacties (
    txn_id                      VARCHAR PRIMARY KEY,
    rekening_iban               VARCHAR NOT NULL,
    boekingsdatum               DATE NOT NULL,
    valutadatum                 DATE,
    bedrag                      DECIMAL(14, 2) NOT NULL,
    munt                        VARCHAR DEFAULT 'EUR',
    tegenpartij_naam            VARCHAR,
    tegenpartij_iban            VARCHAR,
    tegenpartij_bic             VARCHAR,
    omschrijving                VARCHAR,
    mededeling                  VARCHAR,
    gestructureerde_mededeling  VARCHAR,
    bank_referentie             VARCHAR,
    saldo_na                    DECIMAL(14, 2),
    bron                        VARCHAR,
    import_id                   VARCHAR,
    categorie_id                VARCHAR,
    categorie_bron              VARCHAR,   -- regel | ai | handmatig | excel | factuur | intern
    regel_id                    INTEGER,
    zekerheid                   DOUBLE,
    notitie                     VARCHAR,
    factuur_id                  VARCHAR,
    ruwe_data                   VARCHAR,
    aangemaakt_op               TIMESTAMP DEFAULT current_timestamp,
    gewijzigd_op                TIMESTAMP
);

CREATE TABLE IF NOT EXISTS regels (
    regel_id        INTEGER PRIMARY KEY DEFAULT nextval('seq_regel'),
    prioriteit      INTEGER DEFAULT 100,
    naam            VARCHAR,
    veld            VARCHAR DEFAULT 'alles',   -- alles | tegenpartij_naam | tegenpartij_iban | omschrijving | mededeling
    patroon         VARCHAR NOT NULL,
    teken           VARCHAR DEFAULT 'beide',   -- beide | in | uit
    min_bedrag      DECIMAL(14, 2),
    max_bedrag      DECIMAL(14, 2),
    rekening_iban   VARCHAR,
    categorie_id    VARCHAR NOT NULL,
    actief          BOOLEAN DEFAULT TRUE,
    bron            VARCHAR DEFAULT 'standaard',
    aangemaakt_op   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS facturen (
    factuur_id                  VARCHAR PRIMARY KEY,
    bestandsnaam                VARCHAR,
    leverancier                 VARCHAR,
    leverancier_btw             VARCHAR,
    leverancier_iban            VARCHAR,
    factuurnummer               VARCHAR,
    factuurdatum                DATE,
    vervaldatum                 DATE,
    bedrag_totaal               DECIMAL(14, 2),
    bedrag_btw                  DECIMAL(14, 2),
    munt                        VARCHAR DEFAULT 'EUR',
    gestructureerde_mededeling  VARCHAR,
    omschrijving                VARCHAR,
    categorie_id                VARCHAR,
    txn_id                      VARCHAR,
    status                      VARCHAR DEFAULT 'open',  -- open | betaald
    extractie                   VARCHAR,                 -- aanbieder:model | lokaal
    ruwe_data                   VARCHAR,
    geimporteerd_op             TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS instellingen (
    sleutel         VARCHAR PRIMARY KEY,
    waarde          VARCHAR,
    gewijzigd_op    TIMESTAMP DEFAULT current_timestamp
);

ALTER TABLE transacties ADD COLUMN IF NOT EXISTS ai_model VARCHAR;
UPDATE transacties SET categorie_bron = 'ai' WHERE categorie_bron = 'gemini';

-- v0.3: kredietkaarten, budgetten, vaste lasten, nakijken, splitsen en tags
ALTER TABLE rekeningen ADD COLUMN IF NOT EXISTS soort VARCHAR DEFAULT 'zicht';   -- zicht | spaar | kredietkaart
ALTER TABLE rekeningen ADD COLUMN IF NOT EXISTS startsaldo DECIMAL(14, 2);
ALTER TABLE rekeningen ADD COLUMN IF NOT EXISTS startsaldo_datum DATE;
ALTER TABLE transacties ADD COLUMN IF NOT EXISTS nagekeken BOOLEAN DEFAULT FALSE;
ALTER TABLE transacties ADD COLUMN IF NOT EXISTS tags VARCHAR[] DEFAULT [];
ALTER TABLE transacties ADD COLUMN IF NOT EXISTS kaart_afrekening_id VARCHAR;

CREATE TABLE IF NOT EXISTS kaartafrekeningen (
    afrekening_id       VARCHAR PRIMARY KEY,
    bestandsnaam        VARCHAR,
    bank                VARCHAR,
    kaart_rekening      VARCHAR,       -- virtuele rekening (rekeningen.iban) van de kaart
    kaartnaam           VARCHAR,
    periode_van         DATE,
    periode_tot         DATE,
    afrekeningsdatum    DATE,
    totaal              DECIMAL(14, 2),  -- af te rekenen bedrag (positief)
    som_verrichtingen   DECIMAL(14, 2),
    controle            VARCHAR,
    debet_rekening      VARCHAR,       -- zichtrekening volgens de afrekening
    betaald_txn_id      VARCHAR,       -- de afrekening op de zichtrekening
    extractie           VARCHAR,
    geimporteerd_op     TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS budgetten (
    categorie_id    VARCHAR PRIMARY KEY,
    maandbedrag     DECIMAL(14, 2) NOT NULL,   -- positief bedrag per maand
    meenemen        BOOLEAN DEFAULT FALSE,     -- overschot/tekort schuift door naar de volgende maand
    vanaf           DATE,                      -- vanaf deze maand telt 'meenemen'
    aangemaakt_op   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS splitsingen (
    txn_id          VARCHAR NOT NULL,
    volgnr          INTEGER NOT NULL,
    bedrag          DECIMAL(14, 2) NOT NULL,
    categorie_id    VARCHAR,
    notitie         VARCHAR,
    tags            VARCHAR[] DEFAULT [],
    PRIMARY KEY (txn_id, volgnr)
);

CREATE TABLE IF NOT EXISTS terugkerend_keuzes (
    sleutel         VARCHAR PRIMARY KEY,
    status          VARCHAR,                   -- genegeerd | bevestigd
    gewijzigd_op    TIMESTAMP DEFAULT current_timestamp
);

CREATE OR REPLACE VIEW v_transacties AS
SELECT
    t.*,
    c.hoofdcategorie,
    c.subcategorie,
    coalesce(c.soort, CASE WHEN t.bedrag >= 0 THEN 'inkomst' ELSE 'uitgave' END) AS soort,
    CASE
        WHEN t.categorie_bron = 'gesplitst' THEN 'Gesplitst'
        ELSE coalesce(c.hoofdcategorie || ' / ' || c.subcategorie, 'Niet gecategoriseerd')
    END AS categorie_label,
    CAST(date_trunc('month', t.boekingsdatum) AS DATE) AS maand,
    year(t.boekingsdatum) AS jaar,
    coalesce(r.naam, r.bank || ' ' || right(t.rekening_iban, 4)) AS rekening_naam,
    coalesce(r.soort, 'zicht') AS rekening_soort
FROM transacties t
LEFT JOIN categorieen c ON c.categorie_id = t.categorie_id
LEFT JOIN rekeningen r ON r.iban = t.rekening_iban;

-- Eén rij per 'boeking' voor rapporten: een gesplitste transactie telt via haar delen.
CREATE OR REPLACE VIEW v_boekingen AS
WITH basis AS (
    SELECT t.txn_id, CAST(NULL AS INTEGER) AS volgnr, t.rekening_iban, t.boekingsdatum, t.bedrag,
           t.tegenpartij_naam, t.tegenpartij_iban, t.omschrijving, t.mededeling,
           t.categorie_id, t.categorie_bron, t.zekerheid, t.notitie, coalesce(t.tags, []) AS tags, t.nagekeken
    FROM transacties t
    WHERE NOT EXISTS (SELECT 1 FROM splitsingen s WHERE s.txn_id = t.txn_id)
    UNION ALL
    SELECT t.txn_id, s.volgnr, t.rekening_iban, t.boekingsdatum, s.bedrag,
           t.tegenpartij_naam, t.tegenpartij_iban, t.omschrijving, t.mededeling,
           s.categorie_id, 'gesplitst', NULL, coalesce(s.notitie, t.notitie),
           list_distinct(list_concat(coalesce(t.tags, []), coalesce(s.tags, []))), t.nagekeken
    FROM splitsingen s JOIN transacties t ON t.txn_id = s.txn_id
)
SELECT
    b.*,
    CASE WHEN b.volgnr IS NULL THEN b.txn_id ELSE b.txn_id || '#' || b.volgnr END AS boeking_id,
    c.hoofdcategorie,
    c.subcategorie,
    coalesce(c.soort, CASE WHEN b.bedrag >= 0 THEN 'inkomst' ELSE 'uitgave' END) AS soort,
    coalesce(c.hoofdcategorie || ' / ' || c.subcategorie, 'Niet gecategoriseerd') AS categorie_label,
    CAST(date_trunc('month', b.boekingsdatum) AS DATE) AS maand,
    year(b.boekingsdatum) AS jaar,
    coalesce(r.naam, r.bank || ' ' || right(b.rekening_iban, 4)) AS rekening_naam,
    coalesce(r.soort, 'zicht') AS rekening_soort
FROM basis b
LEFT JOIN categorieen c ON c.categorie_id = b.categorie_id
LEFT JOIN rekeningen r ON r.iban = b.rekening_iban;
"""


def een(resultaat):
    """Eerste rij van een resultaat (of None). Haalt alles op, zodat er geen half gelezen resultaat
    openblijft (DuckDB 2 weigert dan verdere relationele oproepen op dezelfde verbinding)."""
    rijen = resultaat.fetchall()
    return rijen[0] if rijen else None


def categorie_id(hoofdcategorie: str, subcategorie: str) -> str:
    return slug(f"{hoofdcategorie} {subcategorie}")


def verbind(pad: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open (en initialiseer zo nodig) de database. pad=None of ':memory:' geeft een tijdelijke DB."""
    if pad is None or str(pad) == ":memory:":
        con = duckdb.connect(":memory:")
    else:
        pad = Path(pad)
        pad.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(pad))
    initialiseer(con)
    return con


def initialiseer(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)
    (aantal,) = een(con.execute("SELECT count(*) FROM categorieen"))
    if aantal == 0:
        vul_standaard(con)
        bewaar_instelling(con, "schema_versie", str(SCHEMA_VERSIE))
    else:
        migreer(con)


def migreer(con: duckdb.DuckDBPyConnection) -> None:
    """Gegevensmigraties die maar één keer mogen lopen (het schema zelf past SCHEMA al aan)."""
    rij = een(con.execute("SELECT waarde FROM instellingen WHERE sleutel = 'schema_versie'"))
    versie = int(rij[0]) if rij else 2
    if versie < 3:
        # Nieuwe standaardcategorie en -regel voor kredietkaartafrekeningen zonder ingelezen detail.
        (volgorde,) = een(con.execute(
            "SELECT coalesce(max(volgorde), 0) + 1 FROM categorieen WHERE hoofdcategorie = 'Diversen'"
        ))
        con.execute(
            "INSERT OR IGNORE INTO categorieen VALUES (?, 'Diversen', 'Kredietkaart (zonder detail)', 'uitgave', TRUE, ?)",
            [CATEGORIE_KAART_ZONDER_DETAIL, volgorde],
        )
        if not een(con.execute("SELECT 1 FROM regels WHERE categorie_id = ?", [CATEGORIE_KAART_ZONDER_DETAIL])):
            con.execute(
                """INSERT INTO regels (prioriteit, naam, veld, patroon, teken, categorie_id, bron)
                   VALUES (60, 'Kredietkaartafrekening', 'alles', ?, 'uit', ?, 'standaard')""",
                [REGEL_KAARTAFREKENING, CATEGORIE_KAART_ZONDER_DETAIL],
            )
        # Wat al in de database zat vóór de nakijklijst bestond, geldt als nagekeken.
        con.execute("UPDATE transacties SET nagekeken = TRUE")
    if versie < 4:
        # Standaardregels uitgebreid met de omschrijvingen van Crelan en AXA Bank (enkel als je ze niet aanpaste).
        for oud_patroon, nieuw_patroon in REGELS_V4:
            con.execute("UPDATE regels SET patroon = ? WHERE bron = 'standaard' AND patroon = ?", [nieuw_patroon, oud_patroon])
        from .categoriseren import pas_regels_toe

        pas_regels_toe(con)
    bewaar_instelling(con, "schema_versie", str(SCHEMA_VERSIE))


_KAART_V3 = (
    r"afrekening.{0,20}(kredietkaart|credit ?card|mastercard|visa)|(kredietkaart|mastercard|visa).{0,20}(afrekening|uitgavenstaat)"
)
REGEL_KAARTAFREKENING = _KAART_V3 + r"|uitgaven visa kredietkaart|\bbcc - crelan\b|\bvisa ref \d"
_KOSTEN_V3 = (
    r"beheerskosten|bankkosten|kosten rekening|kaartbijdrage|aanrekening kosten|frais de gestion|maandelijkse bijdrage|"
    r"jaarlijkse bijdrage|kosten verrichtingen|pluspakket|plus-rekening|kosten zichtrekening"
)
_VERZEKERING_V3 = (
    r"\b(ethias|ag insurance|axa|baloise|p ?& ?v|allianz|federale verzekering|dvv|kbc verzekeringen|belfius insurance|"
    r"vivium|yuzzu|corona direct)\b"
)
REGELS_V4 = (
    (_KAART_V3, REGEL_KAARTAFREKENING),
    (_KOSTEN_V3, _KOSTEN_V3 + r"|bijdrage zichtrekening|beheren vd rek|performance pack|essential pack"),
    # 'Aankoop met AXA debetkaart' (AXA Bank, nu Crelan) is geen verzekering.
    (_VERZEKERING_V3, _VERZEKERING_V3.replace("|axa|", "|axa(?! debetkaart| bank)|")),
)


def vul_standaard(con: duckdb.DuckDBPyConnection) -> None:
    cats = yaml.safe_load((STANDAARD_DIR / "categorieen.yaml").read_text(encoding="utf-8"))
    label_naar_id: dict[str, str] = {}
    volgorde = 0
    for groep in cats:
        for sub in groep["subcategorieen"]:
            volgorde += 10
            cid = categorie_id(groep["hoofdcategorie"], sub)
            label_naar_id[f"{groep['hoofdcategorie']} / {sub}"] = cid
            con.execute(
                "INSERT OR IGNORE INTO categorieen VALUES (?, ?, ?, ?, TRUE, ?)",
                [cid, groep["hoofdcategorie"], sub, groep["soort"], volgorde],
            )

    regels = yaml.safe_load((STANDAARD_DIR / "regels.yaml").read_text(encoding="utf-8"))
    for i, r in enumerate(regels, start=1):
        cid = label_naar_id.get(r["categorie"])
        if cid is None:
            raise ValueError(f"Regel '{r.get('naam')}' verwijst naar onbekende categorie '{r['categorie']}'")
        con.execute(
            """INSERT INTO regels (regel_id, prioriteit, naam, veld, patroon, teken, categorie_id, bron)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'standaard')""",
            [i, r.get("prioriteit", 100), r.get("naam"), r.get("veld", "alles"), r["patroon"],
             r.get("teken", "beide"), cid],
        )


def categorie_labels(con: duckdb.DuckDBPyConnection, alleen_actief: bool = True) -> dict[str, str]:
    """{categorie_id: 'Hoofd / Sub'} in vaste volgorde."""
    rijen = con.execute(
        f"""SELECT categorie_id, hoofdcategorie || ' / ' || subcategorie
            FROM categorieen {'WHERE actief' if alleen_actief else ''}
            ORDER BY volgorde NULLS LAST, hoofdcategorie, subcategorie"""
    ).fetchall()
    return dict(rijen)


def nog_na_te_kijken(con: duckdb.DuckDBPyConnection) -> int:
    return een(con.execute("SELECT count(*) FROM transacties WHERE NOT coalesce(nagekeken, FALSE)"))[0]


def eigen_ibans(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in con.execute("SELECT iban FROM rekeningen").fetchall()}


def lees_instellingen(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute("SELECT sleutel, waarde FROM instellingen").fetchall())


def bewaar_instelling(con: duckdb.DuckDBPyConnection, sleutel: str, waarde: str | None) -> None:
    if waarde is None:
        con.execute("DELETE FROM instellingen WHERE sleutel = ?", [sleutel])
    else:
        con.execute(
            """INSERT INTO instellingen (sleutel, waarde) VALUES (?, ?)
               ON CONFLICT (sleutel) DO UPDATE SET waarde = excluded.waarde, gewijzigd_op = now()""",
            [sleutel, waarde],
        )
