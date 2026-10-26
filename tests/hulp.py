"""Kleine hulpjes om testtransacties rechtstreeks in de database te zetten."""

import uuid
from datetime import date


def rekening(con, iban="BE11000000000001", soort="zicht", bank="Test", naam=None):
    con.execute("INSERT INTO rekeningen (iban, bank, naam, soort) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [iban, bank, naam, soort])
    return iban


def txn(con, datum: date, bedrag: float, naam=None, oms=None, iban_tegen=None, rek="BE11000000000001",
        cat=None, bron=None, saldo_na=None, nagekeken=True) -> str:
    tid = uuid.uuid4().hex[:24]
    con.execute(
        """INSERT INTO transacties (txn_id, rekening_iban, boekingsdatum, bedrag, tegenpartij_naam, omschrijving,
                                    tegenpartij_iban, categorie_id, categorie_bron, saldo_na, nagekeken)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [tid, rek, datum, bedrag, naam, oms, iban_tegen, cat, bron or ("handmatig" if cat else None), saldo_na, nagekeken],
    )
    return tid
