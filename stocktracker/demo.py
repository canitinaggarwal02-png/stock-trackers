"""Fills a separate demo database with made-up data so you can explore the dashboard
without internet access. Nothing here is real market data."""
import json
import random
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import alerts, analysis, db, fundamentals, news, portfolio
from .symbols import build_aliases, clean_name

COMPANIES = [
    ("RELIANCE", "Reliance Industries Limited", "Refineries", 2900),
    ("TCS", "Tata Consultancy Services Limited", "IT Services", 4100),
    ("INFY", "Infosys Limited", "IT Services", 1850),
    ("HDFCBANK", "HDFC Bank Limited", "Private Bank", 1700),
    ("BEL", "Bharat Electronics Limited", "Defence Electronics", 310),
    ("TATAMOTORS", "Tata Motors Limited", "Automobiles", 980),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Limited", "Pharmaceuticals", 1750),
    ("ASIANPAINT", "Asian Paints Limited", "Paints", 2900),
    ("IRCTC", "Indian Railway Catering And Tourism Corporation Limited", "Tourism", 900),
    ("SUZLON", "Suzlon Energy Limited", "Power Equipment", 62),
    ("DMART", "Avenue Supermarts Limited", "Retail", 4600),
    ("ZYDUSLIFE", "Zydus Lifesciences Limited", "Pharmaceuticals", 1050),
]

NEWS = [
    ("BEL", "Bharat Electronics bags orders worth Rs 1,640 crore from Indian Army", 2, 0.0),
    ("INFY", "Infosys Q2 net profit jumps 11%, beats estimates; raises revenue guidance", 20, 4.5),
    ("TATAMOTORS", "Tata Motors CFO resigns with immediate effect", 5, -1.0),
    ("SUNPHARMA", "Sun Pharma gets USFDA approval for generic version of cancer drug", 30, 0.4),
    ("SUZLON", "Suzlon Energy wins 400 MW wind energy order from NTPC Green", 26, 6.0),
    ("ZYDUSLIFE", "USFDA issues warning letter to Zydus Lifesciences plant in Gujarat", 3, 0.0),
    ("RELIANCE", "Reliance Industries announces acquisition of stake in green hydrogen startup", 50, 0.5),
    ("DMART", "Avenue Supermarts Q2 revenue rises 15%, margins under pressure", 70, -2.5),
    (None, "Crude oil prices surge 6% as Middle East tensions escalate", 8, 0.0),
    ("IRCTC", "IRCTC declares interim dividend, record date fixed", 28, 0.6),
    ("HDFCBANK", "HDFC Bank target price raised by Morgan Stanley, maintains overweight rating", 6, 0.0),
]

# Older made-up news so the tracker has past records to learn from
# (how similar news moved the price in the following days).
OLD_NEWS = [
    ("BEL", "Bharat Electronics wins Rs 2,200 crore defence order from Indian Navy", 24 * 60, 3.5),
    ("BEL", "Bharat Electronics secures order worth Rs 1,100 crore from Air Force", 24 * 150, 2.8),
    ("SUZLON", "Suzlon Energy bags 300 MW order from Jindal Renewables", 24 * 45, 5.0),
    ("SUZLON", "Suzlon Energy wins 200 MW wind order from Torrent Power", 24 * 120, 4.0),
    ("RELIANCE", "Reliance Industries wins contract for offshore gas project", 24 * 90, 1.5),
    ("INFY", "Infosys Q1 net profit rises 9%, beats street estimates", 24 * 110, 3.0),
    ("TCS", "TCS Q1 profit beats estimates as deal wins rise", 24 * 105, 2.5),
    ("HDFCBANK", "HDFC Bank Q1 profit beats estimates on loan growth", 24 * 100, 2.0),
    ("TATAMOTORS", "Tata Motors CEO of UK unit resigns", 24 * 80, -3.0),
    ("IRCTC", "IRCTC chief financial officer resigns", 24 * 200, -2.5),
    ("ZYDUSLIFE", "USFDA issues warning letter to Zydus unit after inspection", 24 * 160, -6.0),
    ("SUNPHARMA", "USFDA warning letter for Sun Pharma Halol plant", 24 * 210, -4.5),
]


def build(cfg):
    path = cfg["db_path"].replace(".db", "_demo.db")
    import os
    if os.path.exists(path):
        os.remove(path)
    con = db.connect(path)
    rng = np.random.default_rng(7)
    random.seed(7)
    today = date.today()
    days = [today - timedelta(days=i) for i in range(380, -1, -1)]
    tdays = [d for d in days if d.weekday() < 5]
    if tdays[-1] != today:                  # treat today as a trading day for the demo
        tdays.append(today)
    counts = {}
    for s, n, ind, _ in COMPANIES:
        w = clean_name(n).split()[0].lower()
        counts[w] = counts.get(w, 0) + 1
    for s, n, ind, p0 in COMPANIES:
        con.execute("INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?)",
                    (s, n, "NSE+BSE", "INE000DEMO", None, "EQ", ind, build_aliases(s, n, counts),
                     datetime.now().isoformat()))
    # prices: random walk, with jumps placed after some news
    shocks = {}
    for s, _, h, move in NEWS + OLD_NEWS:
        if s:
            shocks.setdefault(s, []).append((h, move))
    for s, n, ind, p0 in COMPANIES:
        drift = rng.normal(0.0004, 0.0004)
        rets = rng.normal(drift, 0.014, len(tdays))
        for h, move in shocks.get(s, []):
            pub = (datetime.now(timezone.utc) - timedelta(hours=h)).astimezone(analysis.IST)
            first = pub.date() if pub.hour < 15 else pub.date() + timedelta(days=1)
            k = next((i for i, d in enumerate(tdays) if d >= first), None)
            if k is not None:
                rets[k] += move / 100
        closes = p0 * np.exp(np.cumsum(rets - rets.sum() / len(rets)))
        base_vol = rng.integers(200_000, 8_000_000)
        for i, d in enumerate(tdays):
            c = float(closes[i])
            vol = float(base_vol * rng.lognormal(0, 0.3))
            if i == len(tdays) - 1 and s in ("BEL", "SUZLON", "ZYDUSLIFE"):
                vol *= 3.2
            if s == "ZYDUSLIFE" and i == len(tdays) - 1:
                c = float(closes[i - 1]) * 0.94
            o = c * (1 + rng.normal(0, 0.005))
            con.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
                        (s, d.isoformat(), o, max(o, c) * 1.01, min(o, c) * 0.99, c, vol))
            if i >= len(tdays) - 30:
                dp = float(np.clip(rng.normal(48, 10), 15, 90)) + (25 if s in ("BEL", "SUZLON") and i == len(tdays) - 1 else 0)
                prev = float(closes[i - 1])
                con.execute("INSERT INTO delivery VALUES (?,?,?,?,?,?,?,?,?)",
                            (s, d.isoformat(), vol, vol * dp / 100, dp, int(vol / 150), vol * c / 1e5, c, prev))
    # deals
    deal_rows = [
        (1, "SUZLON", "Ashish Kacholia", "BUY", 4_500_000, "bulk", "Ashish Kacholia"),
        (2, "BEL", "Goldman Sachs (Singapore) Pte", "BUY", 6_200_000, "block", "Goldman Sachs"),
        (1, "TATAMOTORS", "Societe Generale", "SELL", 3_100_000, "bulk", "Societe Generale"),
        (3, "IRCTC", "XYZ Capital Fund", "BUY", 900_000, "bulk", None),
        (0, "ZYDUSLIFE", "ABC Securities LLP", "SELL", 1_400_000, "bulk", None),
        (12, "DMART", "Radhakishan Damani", "BUY", 250_000, "block", "Radhakishan Damani"),
    ]
    for ago, s, client, side, qty, kind, big in deal_rows:
        px = con.execute("SELECT close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 1", (s,)).fetchone()[0]
        con.execute("INSERT INTO deals (date, symbol, name, client, side, qty, price, kind, exchange, big_investor, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ((today - timedelta(days=ago)).isoformat(), s, s, client, side, qty, px, kind, "NSE", big,
                     "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"))
    for i, d in enumerate(tdays[-15:]):
        f, dii = float(rng.normal(-800, 1800)), float(rng.normal(1200, 1500))
        con.execute("INSERT INTO fii_dii VALUES (?,?,?,?,?)", (d.isoformat(), "FII/FPI", 12000 + f, 12000, f))
        con.execute("INSERT INTO fii_dii VALUES (?,?,?,?,?)", (d.isoformat(), "DII", 11000 + dii, 11000, dii))
    for s, n, ind, p0 in COMPANIES:
        pr, fii, mf = rng.uniform(30, 72), rng.uniform(8, 30), rng.uniform(3, 15)
        bigh = {"DMART": ["Radhakishan Damani"], "SUZLON": ["Ashish Kacholia"]}.get(s, [])
        for q, (period, adj) in enumerate([("2025-12-31", 0), ("2026-03-31", 0), ("2026-06-30", 0), ("2026-09-30", 1)]):
            con.execute("INSERT INTO shareholding VALUES (?,?,?,?,?,?,?,?,?)",
                        (s, period, pr - (0.8 * adj if s == "TATAMOTORS" else 0), fii + adj * rng.normal(0.3, 0.4),
                         mf + 4 + adj * 0.5, mf + adj * rng.normal(0.4, 0.3), 100 - pr - fii - mf - 4,
                         json.dumps(bigh), "DEMO"))
    # fundamentals
    for s, n, ind, p0 in COMPANIES:
        roe = rng.uniform(0.04, 0.32)
        d = dict(longName=n, sector=ind, industry=ind, marketCap=float(rng.uniform(2e10, 1.5e13)),
                 trailingPE=float(rng.uniform(9, 85)), priceToBook=float(rng.uniform(1, 14)), returnOnEquity=roe,
                 debtToEquity=float(rng.uniform(0, 180)), profitMargins=float(rng.uniform(-0.05, 0.28)),
                 revenueGrowth=float(rng.uniform(-0.05, 0.3)), earningsGrowth=float(rng.uniform(-0.2, 0.4)),
                 currentRatio=float(rng.uniform(0.7, 3)),
                 longBusinessSummary=f"{n} is a demo company in the {ind.lower()} business. This text is made up",
                 history={"Total Revenue": {f"{y}-03-31": float(1e10 * (1 + rng.uniform(-0.05, 0.25)) ** (y - 2022)) for y in range(2022, 2027)},
                          "Net Income": {f"{y}-03-31": float(1e9 * (1 + rng.uniform(-0.1, 0.3)) ** (y - 2022)) for y in range(2022, 2027)}},
                 quarters={q: dict(sales=float(2.6e9 * (1 + 0.03 * i + rng.normal(0, 0.03))),
                                   profit=float(2.8e8 * (1 + 0.05 * i + rng.normal(0, 0.08))))
                           for i, q in enumerate(["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31",
                                                  "2026-06-30", "2026-09-30"])},
                 source_url="https://example.com/demo/financials", source_name="Yahoo Finance financials (DEMO)")
        score, _, _, brief = fundamentals.assess(d)
        con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?)", (s, json.dumps(d), brief, score, datetime.now().isoformat()))
    con.commit()
    # news through the real matching + classification code
    m = news.Matcher(con)
    now = datetime.now(timezone.utc)
    for s, title, h, _ in NEWS + OLD_NEWS:
        item = dict(title=title, summary="", link=f"https://example.com/demo/{abs(hash(title))}",
                    source="DEMO (made-up)", published=now - timedelta(hours=h))
        matches = m.match(title)
        macro = news._macro_targets(con, title, [c[0] for c in COMPANIES]) if not matches else []
        news._store(con, item, matches, macro)
    con.commit()
    portfolio.add(con, "TATAMOTORS", "bought", qty=100, price=1080, note="demo holding")
    portfolio.add(con, "INFY", "bought", qty=50, price=1700, note="demo holding")
    portfolio.add(con, "BEL", "want_buy", target=330, note="demo watch")
    portfolio.add(con, "ZYDUSLIFE", "want_buy", target=1100, note="demo watch")
    portfolio.add(con, "ASIANPAINT", "want_sell", target=3200, note="demo")
    syms = [c[0] for c in COMPANIES]
    analysis.update_news_reactions(con)
    analysis.update_moves_5d(con)
    # made-up score history for the last ~3 months, so the track record has something to check
    for s in syms:
        rows = con.execute("SELECT date, close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 70", (s,)).fetchall()[::-1]
        for i, (d, close) in enumerate(rows[:-1]):
            if i % 3:
                continue
            fwd = rows[min(i + 5, len(rows) - 1)][1] / close - 1
            ss = float(np.clip(fwd * 500 + rng.normal(0, 22), -80, 80))
            ls = float(np.clip(55 + rng.normal(0, 10), 0, 100))
            con.execute("INSERT OR REPLACE INTO signals (symbol, date, short_score, long_score, short_reasons, "
                        "long_reasons, price) VALUES (?,?,?,?,?,?,?)", (s, d, round(ss, 1), round(ls, 1), "[]", "[]", close))
    analysis.update_signals(con, syms, cfg)
    new = alerts.run_alerts(con, cfg, send=False)
    from . import sources
    for k in ("delivery", "deals", "fii", "shp", "prices", "fund", "symbols"):
        sources.note(con, k, "demo")
    db.set_meta(con, "demo_mode", "1")
    con.close()
    return path, new
