"""Financial health check from Yahoo Finance data, with a plain-language brief."""
import json
import logging
from datetime import datetime

from .symbols import yahoo_ticker

log = logging.getLogger(__name__)

KEYS = ["longName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "priceToBook",
        "returnOnEquity", "returnOnAssets", "debtToEquity", "profitMargins", "operatingMargins",
        "revenueGrowth", "earningsGrowth", "dividendYield", "currentRatio", "beta",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "heldPercentInsiders", "heldPercentInstitutions",
        "longBusinessSummary", "currentPrice", "bookValue", "totalDebt", "totalCash", "freeCashflow",
        "targetMeanPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions"]


def _pct(x):
    return None if x is None else round(x * 100, 1)


def fetch(symbol_row):
    import yfinance as yf
    t = yf.Ticker(yahoo_ticker(symbol_row))
    info = t.info or {}
    data = {k: info.get(k) for k in KEYS}
    try:
        fin = t.income_stmt
        hist = {}
        for label in ("Total Revenue", "Net Income"):
            if label in fin.index:
                s = fin.loc[label].dropna().sort_index()
                hist[label] = {str(k.date()): float(v) for k, v in s.items()}
        data["history"] = hist
    except Exception as e:
        log.info("income statement %s: %s", symbol_row["symbol"], e)
        data["history"] = {}
    try:                                    # latest quarterly results
        q = t.quarterly_income_stmt
        quarters = {}
        for label, key in (("Total Revenue", "sales"), ("Net Income", "profit")):
            if label in q.index:
                for k, v in q.loc[label].dropna().items():
                    quarters.setdefault(str(k.date()), {})[key] = float(v)
        data["quarters"] = quarters
    except Exception as e:
        log.info("quarterly results %s: %s", symbol_row["symbol"], e)
        data["quarters"] = {}
    data["source_url"] = f"https://finance.yahoo.com/quote/{yahoo_ticker(symbol_row)}/financials"
    return data


def latest_quarter(d):
    """Latest quarter vs the same quarter a year earlier -> dict or None."""
    q = d.get("quarters") or {}
    ks = sorted(q)
    if len(ks) < 2:
        return None
    last = ks[-1]
    y, m = int(last[:4]), last[5:7]
    prev = next((k for k in ks if k.startswith(str(y - 1)) and k[5:7] == m), None)
    out = dict(period=last, sales=q[last].get("sales"), profit=q[last].get("profit"))
    if prev:
        for k in ("sales", "profit"):
            a, b = q[last].get(k), q[prev].get(k)
            out[k + "_yoy"] = None if not a or not b or b <= 0 else round((a / b - 1) * 100, 1)
    return out


def cagr(series):
    vals = [v for _, v in sorted(series.items())]
    if len(vals) < 2 or vals[0] <= 0 or vals[-1] <= 0:
        return None
    return round(((vals[-1] / vals[0]) ** (1 / (len(vals) - 1)) - 1) * 100, 1)


def assess(d):
    """Return (score 0-100, list of good points, list of concerns, brief text)."""
    good, bad, score = [], [], 50.0
    roe, de, pm = _pct(d.get("returnOnEquity")), d.get("debtToEquity"), _pct(d.get("profitMargins"))
    rg, eg, pe, pb = _pct(d.get("revenueGrowth")), _pct(d.get("earningsGrowth")), d.get("trailingPE"), d.get("priceToBook")
    h = d.get("history") or {}
    rev_cagr, ni_cagr = cagr(h.get("Total Revenue", {})), cagr(h.get("Net Income", {}))

    def add(cond, pts, text, bucket):
        nonlocal score
        if cond:
            score += pts
            bucket.append(text)

    if roe is not None:
        add(roe >= 18, 10, f"High return on equity ({roe}%)", good)
        add(roe < 8, -10, f"Low return on equity ({roe}%)", bad)
    if de is not None:
        add(de < 30, 8, f"Low debt (debt/equity {de/100:.2f})", good)
        add(de > 150, -12, f"High debt (debt/equity {de/100:.2f})", bad)
    if pm is not None:
        add(pm >= 15, 6, f"Healthy profit margin ({pm}%)", good)
        add(pm < 0, -12, f"Loss making (margin {pm}%)", bad)
    if rev_cagr is not None:
        add(rev_cagr >= 15, 8, f"Sales growing fast ({rev_cagr}%/yr over recent years)", good)
        add(rev_cagr < 0, -8, f"Sales shrinking ({rev_cagr}%/yr)", bad)
    elif rg is not None:
        add(rg >= 15, 6, f"Sales up {rg}% vs last year", good)
        add(rg < 0, -6, f"Sales down {rg}% vs last year", bad)
    if ni_cagr is not None:
        add(ni_cagr >= 15, 8, f"Profit growing fast ({ni_cagr}%/yr)", good)
        add(ni_cagr < 0, -8, f"Profit shrinking ({ni_cagr}%/yr)", bad)
    elif eg is not None:
        add(eg >= 15, 6, f"Profit up {eg}% vs last year", good)
        add(eg < -10, -6, f"Profit down {eg}% vs last year", bad)
    if pe is not None and pe > 0:
        add(pe < 15, 6, f"Cheap valuation (P/E {pe:.1f})", good)
        add(pe > 60, -8, f"Expensive valuation (P/E {pe:.1f})", bad)
    elif pe is not None and pe < 0:
        bad.append("Negative earnings (P/E not meaningful)")
    if pb is not None:
        add(pb > 10, -4, f"High price-to-book ({pb:.1f})", bad)
    lq = latest_quarter(d)
    if lq:
        sy, py = lq.get("sales_yoy"), lq.get("profit_yoy")
        if sy is not None:
            add(sy >= 15, 4, f"Latest quarter ({lq['period']}) sales up {sy}% vs a year ago", good)
            add(sy < -5, -4, f"Latest quarter ({lq['period']}) sales down {-sy}% vs a year ago", bad)
        if py is not None:
            add(py >= 20, 5, f"Latest quarter ({lq['period']}) profit up {py}% vs a year ago", good)
            add(py < -15, -5, f"Latest quarter ({lq['period']}) profit down {-py}% vs a year ago", bad)
        if lq.get("profit") is not None and lq["profit"] < 0:
            bad.append(f"Loss in the latest quarter ({lq['period']})")
    cr = d.get("currentRatio")
    if cr is not None:
        add(cr < 1, -4, f"Weak short-term liquidity (current ratio {cr:.2f})", bad)
    score = max(0, min(100, score))

    name = d.get("longName") or ""
    mc = d.get("marketCap")
    size = ("large cap" if mc and mc >= 2e11 else "mid cap" if mc and mc >= 5e10 else "small cap" if mc else "")
    verdict = ("financially strong" if score >= 70 else "financially average" if score >= 45 else "financially weak")
    biz = (d.get("longBusinessSummary") or "").split(". ")[0]
    brief = " ".join(x for x in [
        f"{name} ({d.get('sector') or 'sector n/a'}{', ' + size if size else ''}).",
        (biz + ".") if biz else "",
        f"Overall it looks {verdict} (score {score:.0f}/100).",
        ("Strengths: " + "; ".join(good) + ".") if good else "",
        ("Concerns: " + "; ".join(bad) + ".") if bad else "",
    ] if x)
    return score, good, bad, brief


def update_fundamentals(con, symbols, max_age_days=7):
    errors, n = [], 0
    for s in symbols:
        r = con.execute("SELECT updated_at FROM fundamentals WHERE symbol=?", (s,)).fetchone()
        if r and (datetime.now() - datetime.fromisoformat(r[0])).days < max_age_days:
            continue
        row = con.execute("SELECT * FROM symbols WHERE symbol=?", (s,)).fetchone()
        if not row:
            continue
        try:
            d = fetch(row)
            score, _, _, brief = assess(d)
            con.execute("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?,?)",
                        (s, json.dumps(d), brief, score, datetime.now().isoformat(timespec="seconds")))
            n += 1
        except Exception as e:
            errors.append(f"{s}: {e}")
    if n:
        from . import sources
        sources.note(con, "fund")
    con.commit()
    return n, errors
