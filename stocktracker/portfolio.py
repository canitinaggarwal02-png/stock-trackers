"""Your shares: what you bought, sold, want to buy and want to sell."""
from datetime import datetime

import pandas as pd

KINDS = {
    "bought": "I bought this (holding)",
    "sold": "I sold this (watch for re-entry)",
    "short": "I short-sold this",
    "want_buy": "I want to buy this",
    "want_sell": "I want to sell this",
}


def add(con, symbol, kind, qty=None, price=None, target=None, stop_loss=None, note=""):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    sym = resolve(con, symbol)
    cur = con.execute("INSERT INTO positions (symbol, kind, qty, price, target, stop_loss, note, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (sym, kind, qty, price, target, stop_loss, note, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return cur.lastrowid, sym


def remove(con, pos_id):
    con.execute("UPDATE positions SET active=0 WHERE id=?", (pos_id,))
    con.commit()


def resolve(con, text):
    """Accept an NSE symbol, BSE code or part of the company name."""
    t = text.strip()
    r = con.execute("SELECT symbol FROM symbols WHERE symbol=? OR bse_code=?", (t.upper(), t)).fetchone()
    if r:
        return r[0]
    rows = con.execute("SELECT symbol, name FROM symbols WHERE name LIKE ? ORDER BY LENGTH(name) LIMIT 5",
                       (f"%{t}%",)).fetchall()
    if len(rows) == 1 or (rows and rows[0]["name"].lower().startswith(t.lower())):
        return rows[0][0]
    if rows:
        raise ValueError(f"'{text}' matches several shares: " + ", ".join(f"{r[0]} ({r[1]})" for r in rows))
    raise ValueError(f"No listed share found for '{text}'. Run an update first so the share list is loaded.")


def active(con):
    return pd.read_sql_query("""SELECT p.*, s.name FROM positions p LEFT JOIN symbols s ON s.symbol=p.symbol
        WHERE p.active=1 ORDER BY p.created_at""", con)


def tracked_symbols(con):
    return [r[0] for r in con.execute("SELECT DISTINCT symbol FROM positions WHERE active=1")]
