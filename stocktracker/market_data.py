"""Exchange data published daily by NSE: traded & delivery quantity for every share,
bulk and block deals, FII/DII cash market activity and shareholding patterns."""
import io
import json
import logging
import re
from datetime import date, datetime, timedelta

import pandas as pd

from . import http, sources

log = logging.getLogger(__name__)

BHAV_URLS = ["https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv",
             "https://archives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"]
DEAL_URLS = {"bulk": ["https://nsearchives.nseindia.com/content/equities/bulk.csv",
                      "https://archives.nseindia.com/content/equities/bulk.csv"],
             "block": ["https://nsearchives.nseindia.com/content/equities/block.csv",
                       "https://archives.nseindia.com/content/equities/block.csv"]}


def _first_ok(urls):
    err = None
    for u in urls:
        try:
            return http.get(u, retries=2)
        except Exception as e:
            err = e
    raise err


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


# ---------- daily volume / delivery for every NSE share ----------

def fetch_delivery_day(con, d):
    r = _first_ok([u.format(d=d.strftime("%d%m%Y")) for u in BHAV_URLS])
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SERIES"].str.strip().isin(["EQ", "BE", "BZ", "SM", "ST"])]
    n = 0
    for row in df.itertuples(index=False):
        sym = row.SYMBOL.strip()
        ds = d.isoformat()
        tq, dq = _num(row.TTL_TRD_QNTY), _num(row.DELIV_QTY)
        con.execute("INSERT OR REPLACE INTO delivery VALUES (?,?,?,?,?,?,?,?,?)",
                    (sym, ds, tq, dq, _num(row.DELIV_PER), int(_num(row.NO_OF_TRADES) or 0),
                     _num(row.TURNOVER_LACS), _num(row.CLOSE_PRICE), _num(row.PREV_CLOSE)))
        con.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                    (sym, ds, _num(row.OPEN_PRICE), _num(row.HIGH_PRICE), _num(row.LOW_PRICE),
                     _num(row.CLOSE_PRICE), tq))
        n += 1
    con.commit()
    return n


def update_delivery(con, days_back=35):
    """Fill in any missing trading days in the last `days_back` calendar days."""
    have = {r[0] for r in con.execute("SELECT DISTINCT date FROM delivery")}
    got, errors, fails = 0, [], 0
    today = date.today()
    for i in range(days_back, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5 or d.isoformat() in have:
            continue
        try:
            got += 1 if fetch_delivery_day(con, d) else 0
            fails = 0
        except Exception as e:
            fails += 1                      # holidays have no file; many failures in a row means blocked
            if i <= 1 or fails >= 4:        # today's file is published in the evening
                errors.append(f"{d}: {e}")
            if fails >= 4 and not got:
                break
    if got:
        sources.note(con, "delivery")
    return got, errors


# ---------- bulk & block deals ----------

def _match_big(client, big):
    c = (client or "").lower()
    for b in big:
        if b.lower() in c:
            return b
    return None


def _store_deal(con, d, sym, name, client, side, qty, price, kind, big, src=None):
    side = "BUY" if str(side).strip().upper().startswith("B") else "SELL"
    con.execute("INSERT OR IGNORE INTO deals (date, symbol, name, client, side, qty, price, kind, exchange, "
                "big_investor, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (d, sym, name, client, side, qty, price, kind, "NSE", _match_big(client, big), src))


def update_deals(con, big_investors, backfill_days=30):
    n, errors = 0, []
    for kind, urls in DEAL_URLS.items():
        try:
            resp = _first_ok(urls)
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = [c.strip() for c in df.columns]
            for r in df.to_dict("records"):
                try:
                    d = datetime.strptime(str(r["Date"]).strip(), "%d-%b-%Y").date().isoformat()
                except ValueError:          # "NO RECORDS" on days without deals
                    continue
                price = _num(r.get("Trade Price / Wght. Avg. Price") or r.get("Trade Price"))
                _store_deal(con, d, r["Symbol"].strip(), r.get("Security Name"), r.get("Client Name"),
                            r.get("Buy/Sell"), _num(r.get("Quantity Traded")), price, kind, big_investors, resp.url)
                n += 1
        except Exception as e:
            errors.append(f"{kind} deals: {e}")
    # Backfill history once a day through NSE's JSON API (best effort; NSE may block it).
    try:
        frm = (date.today() - timedelta(days=backfill_days)).strftime("%d-%m-%Y")
        to = date.today().strftime("%d-%m-%Y")
        for kind in ("bulk", "block"):
            data = http.nse_api(f"historical/{kind}-deals?from={frm}&to={to}").get("data", [])
            for r in data:
                d = datetime.strptime(r["BD_DT_DATE"], "%d-%b-%Y").date().isoformat()
                _store_deal(con, d, r["BD_SYMBOL"], r.get("BD_SCRIP_NAME"), r.get("BD_CLIENT_NAME"),
                            r.get("BD_BUY_SELL"), _num(r.get("BD_QTY_TRD")), _num(r.get("BD_TP_WATP")),
                            kind, big_investors, sources.SOURCES["deals"][1])
                n += 1
    except Exception as e:
        errors.append(f"deal history API: {e}")
    if n:
        sources.note(con, "deals")
    con.commit()
    return n, errors


# ---------- FII / DII ----------

def update_fii_dii(con):
    data = http.nse_api("fiidiiTradeReact")
    for r in data:
        d = datetime.strptime(r["date"], "%d-%b-%Y").date().isoformat()
        cat = "FII/FPI" if "FII" in r["category"].upper() or "FPI" in r["category"].upper() else "DII"
        con.execute("INSERT OR REPLACE INTO fii_dii VALUES (?,?,?,?,?)",
                    (d, cat, _num(r["buyValue"]), _num(r["sellValue"]), _num(r["netValue"])))
    sources.note(con, "fii")
    con.commit()
    return len(data)


# ---------- shareholding pattern ----------

_FACT = re.compile(r"<[\w-]+:ShareholdingAsAPercentageOfTotalNumberOfShares[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]+)<", re.I)


def parse_shp_xbrl(text, big_investors):
    vals = {}
    for ctx, v in _FACT.findall(text):
        x = _num(v)
        if x is not None:
            vals[ctx.lower()] = x
    if vals and max(vals.values()) <= 1.0:
        vals = {k: v * 100 for k, v in vals.items()}

    def pick(*keys):
        hits = [v for k, v in vals.items() if any(s in k for s in keys)]
        return max(hits) if hits else None

    mf = pick("mutualfund")
    fii = pick("institutionsforeign") or (
        sum(v for k, v in vals.items() if "foreignportfolioinvestor" in k or "foreigninstitutionalinvestor" in k) or None)
    dii = pick("institutionsdomestic")
    if dii is None:
        parts = [pick(k) for k in ("mutualfund", "insurancecompan", "bank", "providentfund", "pensionfund",
                                   "alternateinvestmentfund", "nbfc")]
        dii = sum(p for p in parts if p) or None
    low = text.lower()
    big = sorted({b for b in big_investors if b.lower() in low})
    return mf, fii, dii, big


def update_shareholding(con, symbols, big_investors, limit=40):
    """Rotate through `symbols`, refreshing the ones checked longest ago."""
    last = {r[0]: r[1] for r in con.execute("SELECT symbol, MAX(period) FROM shareholding GROUP BY symbol")}
    todo = sorted(symbols, key=lambda s: last.get(s) or "")[:limit]
    n, errors = 0, []
    for sym in todo:
        try:
            recs = http.nse_api(f"corporate-share-holdings-master?index=equities&symbol={quote(sym)}")
            for rec in sorted(recs, key=lambda r: r.get("date", ""), reverse=True)[:2]:
                period = datetime.strptime(rec["date"], "%d-%b-%Y").date().isoformat()
                mf = fii = dii = None
                big = []
                if rec.get("xbrl"):
                    try:
                        mf, fii, dii, big = parse_shp_xbrl(http.get(rec["xbrl"], retries=1).text, big_investors)
                    except Exception as e:
                        log.info("XBRL %s: %s", sym, e)
                con.execute("INSERT OR REPLACE INTO shareholding VALUES (?,?,?,?,?,?,?,?,?)",
                            (sym, period, _num(rec.get("pr_and_prgrp")), fii, dii, mf,
                             _num(rec.get("public_val")), json.dumps(big), "NSE"))
                n += 1
        except Exception as e:
            errors.append(f"{sym}: {e}")
    if n:
        sources.note(con, "shp")
    con.commit()
    return n, errors


def quote(s):
    from urllib.parse import quote as q
    return q(s, safe="")
