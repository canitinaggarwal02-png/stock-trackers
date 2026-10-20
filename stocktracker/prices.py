"""Price history from Yahoo Finance (free, no key) for the shares we analyse in depth."""
import logging

import pandas as pd

from .symbols import yahoo_ticker

log = logging.getLogger(__name__)


def update_prices(con, symbols, period=None):
    """period=None: up to 5 years for shares seen for the first time, 1 month for the rest
    (the database keeps every day already downloaded, so history builds up)."""
    if period is None:
        have = {r[0]: r[1] for r in con.execute("SELECT symbol, COUNT(*) FROM prices GROUP BY symbol")}
        new = [s for s in symbols if have.get(s, 0) < 200]
        old = [s for s in symbols if have.get(s, 0) >= 200]
        n1, e1 = update_prices(con, new, "5y") if new else (0, [])
        n2, e2 = update_prices(con, old, "1mo") if old else (0, [])
        return n1 + n2, e1 + e2
    import yfinance as yf

    rows = con.execute(f"SELECT * FROM symbols WHERE symbol IN ({','.join('?' * len(symbols))})",
                       list(symbols)).fetchall() if symbols else []
    tick = {yahoo_ticker(r): r["symbol"] for r in rows}
    if not tick:
        return 0, []
    errors, n = [], 0
    tickers = list(tick)
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i + 50]
        try:
            df = yf.download(batch, period=period, group_by="ticker", auto_adjust=False,
                             threads=True, progress=False)
        except Exception as e:
            errors.append(f"yfinance batch {i}: {e}")
            continue
        for t in batch:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
            except KeyError:
                errors.append(f"no data for {t}")
                continue
            for d, r in sub.iterrows():
                con.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                            (tick[t], d.date().isoformat(), float(r["Open"]), float(r["High"]),
                             float(r["Low"]), float(r["Close"]), float(r["Volume"])))
                n += 1
    if n:
        from . import sources
        sources.note(con, "prices")
    con.commit()
    return n, errors


def history(con, symbol, days=400):
    df = pd.read_sql_query("SELECT date, open, high, low, close, volume FROM prices WHERE symbol=? "
                           "ORDER BY date DESC LIMIT ?", con, params=(symbol, days))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))
