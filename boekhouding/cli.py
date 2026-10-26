"""Opdrachtregel: python -m boekhouding <opdracht>

  app                          start de Streamlit-app
  importeer BESTAND...         importeer CSV/CODA/PDF/foto's  [--rekening IBAN] [--ai] [--pdf afschrift|kredietkaart|factuur]
  inbox                        verwerk alle bestanden in de inbox-map  [--ai]
  categoriseer                 pas regels opnieuw toe  [--ai]
  excel-export [PAD]           schrijf een Excel-bestand (standaard in data/exports)
  excel-import PAD             lees aanpassingen uit Excel terug in
  status                       korte samenvatting, inclusief gekozen AI-modellen
  budget [JJJJ-MM]             stand van de budgetten (standaard deze maand)
  vaste-lasten                 herkende vaste lasten, abonnementen en terugkerende inkomsten
  ai-test [TAAK]               proefoproep naar het AI-model voor documenten en/of categorisatie
  ai-modellen AANBIEDER        toon de modellen die je sleutel bij die aanbieder kan gebruiken (live)
  ai-kies TAAK AANBIEDER [MODEL]   bewaar een AI-keuze in de database (zoals op de pagina AI-modellen)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import db
from .categoriseren import categoriseer_met_ai, pas_regels_toe
from .excel import exporteer_excel, importeer_excel
from .llm import AIFout, aanbieder, catalogus, controleer_verbinding, lijst_modellen
from .models import RekeningOntbreekt
from .pipeline import ImportVerslag, categoriseer_nieuwe_met_ai, importeer_bestand, verwerk_inbox
from .settings import PROJECT_ROOT, PROVIDERS, TAKEN, laad_instellingen, met_database_keuzes, standaard_model


def _toon(v: ImportVerslag) -> None:
    if v.fout:
        print(f"✗ {v.bestandsnaam}: {v.fout}")
        return
    if v.soort == "factuur":
        print(f"✓ {v.bestandsnaam}: factuur ingelezen" + (" en gekoppeld aan een betaling" if v.facturen_gekoppeld else ""))
    else:
        print(
            f"✓ {v.bestandsnaam} [{v.bron}] {v.gelezen} gelezen, {v.nieuw} nieuw, {v.dubbel} dubbel, "
            f"saldo: {v.saldo_controle}, {v.gecategoriseerd_regels} via regels, {v.gecategoriseerd_ai} via AI"
        )
    for w in v.waarschuwingen:
        print(f"  ! {w}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boekhouding", description="Dagelijkse boekhouding")
    sub = parser.add_subparsers(dest="opdracht", required=True)
    sub.add_parser("app")
    p_imp = sub.add_parser("importeer")
    p_imp.add_argument("bestanden", nargs="+", type=Path)
    p_imp.add_argument("--rekening")
    p_imp.add_argument("--ai", "--gemini", dest="ai", action="store_true", help="categoriseer wat geen regel heeft met AI")
    p_imp.add_argument("--pdf", choices=["auto", "afschrift", "kredietkaart", "factuur"], default="auto")
    p_imp.add_argument("--forceer", action="store_true", help="ook bestanden die al eens geïmporteerd werden")
    sub.add_parser("inbox").add_argument("--ai", "--gemini", dest="ai", action="store_true")
    sub.add_parser("categoriseer").add_argument("--ai", "--gemini", dest="ai", action="store_true")
    sub.add_parser("excel-export").add_argument("pad", nargs="?", type=Path)
    sub.add_parser("excel-import").add_argument("pad", type=Path)
    sub.add_parser("status")
    sub.add_parser("budget").add_argument("maand", nargs="?", help="JJJJ-MM")
    sub.add_parser("vaste-lasten")
    sub.add_parser("ai-test").add_argument("taak", nargs="?", choices=TAKEN)
    sub.add_parser("ai-modellen").add_argument("aanbieder", choices=PROVIDERS)
    p_kies = sub.add_parser("ai-kies")
    p_kies.add_argument("taak", choices=TAKEN)
    p_kies.add_argument("aanbieder", choices=PROVIDERS)
    p_kies.add_argument("model", nargs="?")
    args = parser.parse_args(argv)

    inst = laad_instellingen()
    if args.opdracht == "app":
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app" / "main.py")], cwd=PROJECT_ROOT)

    inst.maak_mappen()
    con = db.verbind(inst.db_pad)
    inst = met_database_keuzes(inst, db.lees_instellingen(con))
    try:
        if args.opdracht == "importeer":
            verslagen = []
            for i, pad in enumerate(args.bestanden, start=1):
                print(f"[{i}/{len(args.bestanden)}] {pad.name} …", flush=True)
                try:
                    v = importeer_bestand(
                        con, pad.name, pad.read_bytes(), rekening_iban=args.rekening, pdf_soort=args.pdf,
                        gebruik_ai=False, forceer=args.forceer, inst=inst,
                    )
                except RekeningOntbreekt as e:
                    v = ImportVerslag(bestandsnaam=pad.name, fout=f"{e} Gebruik --rekening BE..")
                verslagen.append(v)
                _toon(v)
            if args.ai:
                n = categoriseer_nieuwe_met_ai(con, inst, verslagen)
                print(f"{n} nieuwe transacties via AI ({inst.ai('categorisatie')}) gecategoriseerd")
                for w in verslagen[-1].waarschuwingen if verslagen else []:
                    if w.startswith("AI-"):
                        print(f"  ⚠ {w}")
        elif args.opdracht == "inbox":
            verslagen = verwerk_inbox(con, inst, gebruik_ai=args.ai)
            if not verslagen:
                print(f"Geen bestanden in {inst.inbox_dir}")
            for v in verslagen:
                _toon(v)
        elif args.opdracht == "categoriseer":
            print(f"{pas_regels_toe(con)} transacties via regels (her)gecategoriseerd")
            if args.ai:
                try:
                    print(f"{categoriseer_met_ai(con, inst)} transacties via AI ({inst.ai('categorisatie')}) gecategoriseerd")
                except AIFout as e:
                    print(f"✗ {e}")
        elif args.opdracht == "excel-export":
            pad = args.pad or inst.export_dir / f"boekhouding_{datetime.now():%Y%m%d_%H%M}.xlsx"
            print(f"Geschreven: {exporteer_excel(con, pad)}")
        elif args.opdracht == "excel-import":
            v = importeer_excel(con, args.pad)
            print(
                f"{v.transacties_gewijzigd} transacties, {v.categorieen_nieuw} nieuwe en {v.categorieen_gewijzigd} gewijzigde "
                f"categorieën, {v.regels_nieuw} nieuwe en {v.regels_gewijzigd} gewijzigde regels, "
                f"{v.rekeningen_gewijzigd} rekeningen bijgewerkt; {v.opnieuw_gecategoriseerd} opnieuw gecategoriseerd."
            )
            for f in v.fouten:
                print(f"  ! {f}")
        elif args.opdracht == "status":
            n, leeg, van, tot = con.execute(
                "SELECT count(*), count(*) FILTER (WHERE categorie_id IS NULL), min(boekingsdatum), max(boekingsdatum) FROM transacties"
            ).fetchall()[0]
            print(f"Database: {inst.db_pad}")
            print(f"{n} transacties van {van} tot {tot}, waarvan {leeg} zonder categorie")
            for taak in TAKEN:
                keuze = inst.ai(taak)
                klaar, uitleg = inst.provider_klaar(keuze.provider)
                print(f"AI {taak}: {aanbieder(keuze.provider).label} · {keuze.model or '(geen model)'} · {'klaar' if klaar else uitleg}")
        elif args.opdracht == "budget":
            from datetime import date

            from .budget import budget_status

            d = date.fromisoformat(args.maand + "-01") if args.maand else date.today()
            df = budget_status(con, d.year, d.month)
            if df.empty:
                print("Nog geen budgetten ingesteld.")
            for r in df.itertuples():
                teken = {"ok": "✓", "let op": "!", "over": "✗"}[r.status]
                print(f"{teken} {r.hoofdcategorie} / {r.subcategorie}: {r.besteed:.2f} van {r.beschikbaar:.2f} ({r.aandeel:.0%})")
        elif args.opdracht == "vaste-lasten":
            from .terugkerend import detecteer

            df = detecteer(con)
            if df.empty:
                print("Nog geen terugkerende posten gevonden.")
            for r in df.itertuples():
                stijging = f"  ▲ {r.prijswijziging:+.0%}" if r.prijswijziging and r.prijswijziging > 0.05 else ""
                status = "" if r.actief else "  (gestopt?)"
                print(f"{r.naam[:30]:30} {r.frequentie:14} {r.bedrag:>10.2f}  volgende {r.volgende_datum}{stijging}{status}")
        elif args.opdracht == "ai-test":
            for taak in [args.taak] if args.taak else TAKEN:
                ok, bericht = controleer_verbinding(inst, taak)
                print(f"{'✓' if ok else '✗'} {taak}: {bericht}")
        elif args.opdracht == "ai-modellen":
            info = aanbieder(args.aanbieder)
            try:
                live = lijst_modellen(inst, args.aanbieder)
            except AIFout as e:
                print(f"✗ {e}")
                live = []
            gekend = {m.id: m for m in info.modellen}
            for m in live:
                extra = f"  ({gekend[m].label()})" if m in gekend else ""
                print(f"  {m}{extra}")
            if not live:
                print(f"Modellen in de catalogus van {info.label}:")
                for m in info.modellen:
                    print(f"  {m.label()}  — {m.opmerking}")
        elif args.opdracht == "ai-kies":
            model = args.model or standaard_model(args.aanbieder, args.taak)
            db.bewaar_instelling(con, f"ai_{args.taak}_provider", args.aanbieder)
            db.bewaar_instelling(con, f"ai_{args.taak}_model", model)
            print(f"{args.taak}: {catalogus()[args.aanbieder].label} · {model or '(geen model: geef er een op)'}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
