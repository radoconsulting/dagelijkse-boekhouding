from decimal import Decimal

import pytest
from conftest import lees

from boekhouding.models import RekeningOntbreekt
from boekhouding.pipeline import importeer_bestand, verwerk_inbox


def _importeer_alles(con, inst):
    return [
        importeer_bestand(con, n, lees(n), inst=inst)
        for n in ("kbc_voorbeeld.csv", "belfius_voorbeeld.csv", "crelan_voorbeeld.csv", "kbc_voorbeeld.cod", "factuur_kinderopvang.pdf")
    ]


def test_volledige_import(con, inst):
    verslagen = _importeer_alles(con, inst)
    assert all(v.gelukt for v in verslagen), [v.fout for v in verslagen]
    (n,) = con.execute("SELECT count(*) FROM transacties").fetchall()[0]
    assert n == 11 + 5 + 3 + 1  # CODA: 2 van de 3 verrichtingen stonden al in de KBC-CSV


def test_ontdubbeling_tussen_formaten(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    v = importeer_bestand(con, "kbc.cod", lees("kbc_voorbeeld.cod"), inst=inst)
    assert (v.nieuw, v.dubbel) == (1, 2)


def test_twee_identieke_verrichtingen_op_een_dag_blijven_twee(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    (n,) = con.execute("SELECT count(*) FROM transacties WHERE bedrag = -8.60").fetchall()[0]
    assert n == 2


def test_hetzelfde_bestand_opnieuw(con, inst):
    importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    v = importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    assert v.al_geimporteerd and not v.gelukt
    v = importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst, forceer=True)
    assert (v.nieuw, v.dubbel) == (0, 11)


def test_interne_overboeking_aan_beide_kanten(con, inst):
    _importeer_alles(con, inst)
    rijen = con.execute("SELECT bedrag, categorie_id, categorie_bron FROM transacties WHERE abs(bedrag) = 500").fetchall()
    assert sorted(float(r[0]) for r in rijen) == [-500.0, 500.0]
    assert all(r[1] == "overboekingen_eigen_rekeningen" and r[2] == "intern" for r in rijen)


def test_factuur_gekoppeld_via_ogm(con, inst):
    _importeer_alles(con, inst)
    f = con.execute("SELECT status, txn_id, bedrag_totaal, factuurnummer FROM facturen").fetchall()[0]
    assert f[0] == "betaald" and f[1] is not None
    assert f[2] == Decimal("312.50") and f[3] == "2026-0917"
    (bedrag,) = con.execute("SELECT bedrag FROM transacties WHERE txn_id = ?", [f[1]]).fetchall()[0]
    assert bedrag == Decimal("-312.50")


def test_factuur_eerst_dan_betaling(con, inst):
    v = importeer_bestand(con, "f.pdf", lees("factuur_kinderopvang.pdf"), inst=inst)
    assert v.facturen_gekoppeld == 0
    v = importeer_bestand(con, "kbc.csv", lees("kbc_voorbeeld.csv"), inst=inst)
    assert v.facturen_gekoppeld == 1


def test_csv_zonder_rekeningnummer(con, inst):
    zonder = lees("crelan_voorbeeld.csv").decode().replace("BE56 1030 0000 0003", "")
    with pytest.raises(RekeningOntbreekt):
        importeer_bestand(con, "c.csv", zonder.encode(), inst=inst)
    v = importeer_bestand(con, "c.csv", zonder.encode(), inst=inst, rekening_iban="BE56 1030 0000 0003")
    assert v.nieuw == 3


def test_inbox(con, inst):
    inst.maak_mappen()
    (inst.inbox_dir / "kbc.csv").write_bytes(lees("kbc_voorbeeld.csv"))
    (inst.inbox_dir / "kapot.csv").write_bytes(b"x;y\n1;2\n")
    verslagen = verwerk_inbox(con, inst)
    assert {v.bestandsnaam: v.gelukt for v in verslagen} == {"kbc.csv": True, "kapot.csv": False}
    assert [p.name for p in inst.inbox_dir.iterdir()] == ["kapot.csv"]  # mislukte blijven staan
    assert len(list(inst.archief_dir.rglob("kbc.csv"))) == 1


def test_pdf_afschrift_zonder_gemini_geeft_duidelijke_fout(con, inst):
    v = importeer_bestand(con, "uittreksel_okt.pdf", lees("factuur_kinderopvang.pdf"), pdf_soort="afschrift", inst=inst)
    assert not v.gelukt and "GEMINI_API_KEY" in v.fout and "AI-modellen" in v.fout
