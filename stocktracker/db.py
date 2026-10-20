import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY,          -- NSE symbol, or BSE code if only on BSE
    name TEXT, exchange TEXT, isin TEXT, bse_code TEXT, series TEXT,
    industry TEXT, aliases TEXT,      -- '|' separated search names
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY, title TEXT, summary TEXT, link TEXT, source TEXT,
    published TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS news_symbols (
    news_id TEXT, symbol TEXT, event_type TEXT, sentiment REAL, horizon TEXT,
    impact_score REAL, price_at_news REAL, reaction_pct REAL, reaction_status TEXT,
    explanation TEXT,
    PRIMARY KEY (news_id, symbol)
);
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS delivery (
    symbol TEXT, date TEXT, traded_qty REAL, delivery_qty REAL, delivery_pct REAL,
    trades INTEGER, turnover_lacs REAL, close REAL, prev_close REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS deals (
    date TEXT, symbol TEXT, name TEXT, client TEXT, side TEXT, qty REAL, price REAL,
    kind TEXT, exchange TEXT, big_investor TEXT,
    PRIMARY KEY (date, symbol, client, side, qty, price, kind)
);
CREATE TABLE IF NOT EXISTS fii_dii (
    date TEXT, category TEXT, buy REAL, sell REAL, net REAL,
    PRIMARY KEY (date, category)
);
CREATE TABLE IF NOT EXISTS shareholding (
    symbol TEXT, period TEXT, promoter REAL, fii REAL, dii REAL, mutual_funds REAL,
    public REAL, big_holders TEXT, source TEXT, PRIMARY KEY (symbol, period)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT PRIMARY KEY, data TEXT, brief TEXT, score REAL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    symbol TEXT, date TEXT, short_score REAL, long_score REAL,
    short_reasons TEXT, long_reasons TEXT, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, kind TEXT,           -- holding | short | want_buy | want_sell
    qty REAL, price REAL, target REAL, stop_loss REAL, note TEXT,
    created_at TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, key TEXT, action TEXT, title TEXT, body TEXT, created_at TEXT,
    sent INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Columns added after the first release; added to older databases on open.
MIGRATIONS = [
    ("news_symbols", "move_5d REAL"),     # price move 5 trading days after the news (for learning)
    ("signals", "price REAL"),            # price when the scores were saved (for the track record)
    ("deals", "source TEXT"),             # address the deal was read from
]


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for table, col in MIGRATIONS:
        if col.split()[0] not in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    con.commit()
    return con


def set_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))
    con.commit()


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default
