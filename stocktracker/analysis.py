"""Turns raw data into answers: has the news already moved the price or is the move
still to come, how is buying/selling activity changing, and which shares look
interesting for the short term and the long term."""
import json
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .prices import history, rsi
from .sources import with_src

IST = timezone(timedelta(hours=5, minutes=30))
STATUS_TEXT = {
    "not_traded": "Market has not traded since the news, so the effect is still to come.",
    "pending": "Price has barely moved yet; the effect may still come.",
    "partly": "Price has moved part of the way; some effect may still be left.",
    "priced_in": "Price has already reacted; most of the short-term effect looks done.",
    "opposite": "Price moved against what the news suggests; the market may read it differently.",
    "neutral": "News looks neutral; no clear price effect expected.",
    "no_price": "No price data yet.",
}


# ---------- did the news already move the price? ----------

def news_reaction(px, published_iso, sentiment):
    """px: price history DataFrame. Returns (base_price, move_pct, status)."""
    if px is None or px.empty:
        return None, None, "no_price"
    pub = datetime.fromisoformat(published_iso).astimezone(IST)
    first_day = pub.date() if pub.hour < 15 or (pub.hour == 15 and pub.minute < 30) else pub.date() + timedelta(days=1)
    before = px[px.index.date < first_day]
    after = px[px.index.date >= first_day]
    if before.empty:
        return None, None, "no_price"
    base = float(before["close"].iloc[-1])
    if after.empty:
        return base, 0.0, "not_traded" if abs(sentiment) >= 0.15 else "neutral"
    now = float(after["close"].iloc[-1])
    move = (now / base - 1) * 100
    rets = before["close"].pct_change().dropna().tail(60)
    sigma = float(rets.std() * 100) if len(rets) > 10 else 2.0
    thr = max(1.0, 1.5 * sigma * math.sqrt(min(len(after), 5)))
    if abs(sentiment) < 0.15:
        return base, move, "neutral"
    same = np.sign(move) == np.sign(sentiment)
    if abs(move) >= thr:
        return base, move, "priced_in" if same else "opposite"
    if abs(move) >= 0.5 * thr and same:
        return base, move, "partly"
    return base, move, "pending"


def update_news_reactions(con, days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = con.execute("""SELECT ns.news_id, ns.symbol, ns.sentiment, ns.horizon, n.published
        FROM news_symbols ns JOIN news n ON n.id = ns.news_id WHERE n.published >= ?""", (since,)).fetchall()
    cache = {}
    for r in rows:
        if r["symbol"] not in cache:
            cache[r["symbol"]] = history(con, r["symbol"], 120)
        base, move, status = news_reaction(cache[r["symbol"]], r["published"], r["sentiment"] or 0)
        con.execute("UPDATE news_symbols SET price_at_news=?, reaction_pct=?, reaction_status=? "
                    "WHERE news_id=? AND symbol=?",
                    (base, None if move is None else round(move, 2), status, r["news_id"], r["symbol"]))
    con.commit()
    return len(rows)


def update_moves_5d(con):
    """For news at least a week old, record how the price moved over the next 5 trading
    days. These records are what the tracker learns from."""
    now = datetime.now(timezone.utc)
    rows = con.execute("""SELECT ns.news_id, ns.symbol, ns.price_at_news, n.published FROM news_symbols ns
        JOIN news n ON n.id=ns.news_id WHERE ns.move_5d IS NULL AND n.published <= ?""",
                       ((now - timedelta(days=8)).isoformat(),)).fetchall()
    cache, n = {}, 0
    for r in rows:
        if r["symbol"] not in cache:
            cache[r["symbol"]] = history(con, r["symbol"], 2000)
        px = cache[r["symbol"]]
        if px.empty:
            continue
        pub = datetime.fromisoformat(r["published"]).astimezone(IST)
        first = pub.date() if pub.hour < 15 or (pub.hour == 15 and pub.minute < 30) else pub.date() + timedelta(days=1)
        before, after = px[px.index.date < first], px[px.index.date >= first]
        if before.empty or len(after) < 5:
            continue
        base = r["price_at_news"] or float(before["close"].iloc[-1])
        move = (float(after["close"].iloc[4]) / base - 1) * 100
        con.execute("UPDATE news_symbols SET move_5d=? WHERE news_id=? AND symbol=?",
                    (round(move, 2), r["news_id"], r["symbol"]))
        n += 1
    con.commit()
    return n


_EV_CACHE = {}


def event_stats(con, fresh=False):
    """Average 5-day price move after past news, by event type and direction (from the
    tracker's own records). -> {(event_type, +1/-1): dict(n, avg, up, label)}"""
    key = id(con)
    if not fresh and key in _EV_CACHE:
        return _EV_CACHE[key]
    from .nlp import EVENTS
    out = {}
    for r in con.execute("""SELECT event_type, CASE WHEN sentiment>0 THEN 1 ELSE -1 END d, COUNT(*) n,
            AVG(move_5d) avg, SUM(move_5d>0) up FROM news_symbols
            WHERE move_5d IS NOT NULL AND ABS(sentiment)>=0.15 GROUP BY 1, 2"""):
        label = EVENTS[r["event_type"]][-1] if r["event_type"] in EVENTS else (
            "Global/macro theme" if r["event_type"] == "macro" else "General news")
        out[(r["event_type"], r["d"])] = dict(n=r["n"], avg=r["avg"], up=r["up"], label=label)
    _EV_CACHE.clear()
    _EV_CACHE[key] = out
    return out


def past_line(m, positive):
    return (f"In your records, {m['n']} similar {'positive' if positive else 'negative'} \"{m['label']}\" news items "
            f"moved the price {m['avg']:+.1f}% on average within 5 trading days ({m['up']} of {m['n']} went up)")


def past_weight(m, sentiment):
    """How much to trust a news item given what similar news did before."""
    if not m or m["n"] < 3:
        return 1.0
    same = np.sign(m["avg"]) == np.sign(sentiment)
    return 1 + min(abs(m["avg"]) / 5, 0.5) if same else 0.6


def reaction_text(status, move, horizon):
    t = STATUS_TEXT.get(status or "no_price", "")
    if move is not None and status not in ("no_price", "not_traded"):
        t = f"Since the news the price is {move:+.1f}%. " + t
    if horizon in ("long", "both") and status in ("priced_in", "partly"):
        t += " Being a long-term factor, it can keep affecting the share over coming months."
    return t


# ---------- buying / selling activity ----------

def activity(con, symbol):
    df = pd.read_sql_query("SELECT * FROM delivery WHERE symbol=? ORDER BY date DESC LIMIT 25", con, params=(symbol,))
    from_delivery = not df.empty
    if df.empty:
        px = history(con, symbol, 25)
        if px.empty:
            return None
        df = pd.DataFrame({"date": px.index.strftime("%Y-%m-%d"), "traded_qty": px["volume"].values,
                           "delivery_qty": np.nan, "delivery_pct": np.nan, "close": px["close"].values}).iloc[::-1]
    df = df.sort_values("date")
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    avg = df.iloc[:-1].tail(20)
    vol_ratio = last.traded_qty / avg.traded_qty.mean() if len(avg) and avg.traded_qty.mean() else None
    del_ratio = (last.delivery_qty / avg.delivery_qty.mean()
                 if len(avg) and pd.notna(last.delivery_qty) and avg.delivery_qty.mean() else None)
    return dict(date=last.date, traded_qty=last.traded_qty, delivery_qty=last.delivery_qty,
                delivery_pct=last.delivery_pct, prev_traded_qty=prev.traded_qty,
                qty_change_pct=(last.traded_qty / prev.traded_qty - 1) * 100 if prev.traded_qty else None,
                vol_ratio=vol_ratio, del_ratio=del_ratio,
                price_change_pct=(last.close / prev.close - 1) * 100 if prev.close else None,
                table=df, from_delivery=from_delivery)


def deals_summary(con, symbol, days=30):
    since = (date.today() - timedelta(days=days)).isoformat()
    df = pd.read_sql_query("SELECT * FROM deals WHERE symbol=? AND date>=? ORDER BY date DESC", con,
                           params=(symbol, since))
    buy = df[df.side == "BUY"].qty.sum() if not df.empty else 0
    sell = df[df.side == "SELL"].qty.sum() if not df.empty else 0
    big = df[df.big_investor.notna()] if not df.empty else df
    return dict(df=df, buy=buy, sell=sell, big=big)


def shareholding_change(con, symbol):
    rows = con.execute("SELECT * FROM shareholding WHERE symbol=? ORDER BY period DESC LIMIT 2", (symbol,)).fetchall()
    if not rows:
        return None
    cur = dict(rows[0])
    cur["big_holders"] = json.loads(cur.get("big_holders") or "[]")
    if len(rows) > 1:
        for k in ("promoter", "fii", "dii", "mutual_funds"):
            a, b = rows[0][k], rows[1][k]
            cur[k + "_chg"] = None if a is None or b is None else round(a - b, 2)
    return cur


# ---------- short and long term scores ----------

def _news_rows(con, symbol, days, horizons):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q = ",".join("?" * len(horizons))
    return con.execute(f"""SELECT ns.*, n.title, n.published, n.source, n.link FROM news_symbols ns JOIN news n ON n.id=ns.news_id
        WHERE ns.symbol=? AND n.published>=? AND ns.horizon IN ({q}) ORDER BY n.published DESC""",
                       (symbol, since, *horizons)).fetchall()


def _ist(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(IST).strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return ""


LEFT = {"not_traded": 1.0, "pending": 1.0, "partly": 0.6, "priced_in": 0.3, "opposite": 0.5,
        "neutral": 0.2, "no_price": 0.8, None: 0.8}


def score_symbol(con, symbol, cfg):
    a = cfg["alerts"]
    s_short, s_reasons = 0.0, []
    news = _news_rows(con, symbol, 3, ("short", "both"))
    stats = event_stats(con)
    nsum = sum((r["sentiment"] or 0) * (r["impact_score"] or 0) * LEFT.get(r["reaction_status"], 0.8)
               * past_weight(stats.get((r["event_type"], 1 if (r["sentiment"] or 0) > 0 else -1)), r["sentiment"] or 0)
               for r in news)
    nsum = max(-40, min(40, nsum * 25))
    if abs(nsum) >= 3:
        s_short += nsum
        top = max(news, key=lambda r: abs((r["sentiment"] or 0) * (r["impact_score"] or 0)))
        s_reasons.append(with_src(
            f"{'Positive' if nsum > 0 else 'Negative'} recent news not fully priced in ({len(news)} item"
            f"{'s' if len(news) != 1 else ''}, e.g. \"{top['title'][:90]}\")",
            name=top["source"], date=_ist(top["published"]), url=top["link"]))
        m = stats.get((top["event_type"], 1 if (top["sentiment"] or 0) > 0 else -1))
        if m and m["n"] >= 3:
            s_reasons.append(with_src(past_line(m, (top["sentiment"] or 0) > 0), "records"))

    act = activity(con, symbol)
    asrc = dict(key="delivery") if act and act.get("from_delivery") else dict(key="prices", ticker=_ticker(con, symbol))
    if act and act["vol_ratio"]:
        up = (act["price_change_pct"] or 0) > 0
        if act["vol_ratio"] >= a["volume_spike_ratio"]:
            s_short += 15 if up else -15
            s_reasons.append(with_src(f"Volume {act['vol_ratio']:.1f}x the 20-day average with price {'up' if up else 'down'}"
                                      f" ({'buying interest' if up else 'selling pressure'})", date=act["date"], **asrc))
        if act["del_ratio"] and act["del_ratio"] >= a["delivery_spike_ratio"] and up and (act["delivery_pct"] or 0) >= 45:
            s_short += 10
            s_reasons.append(with_src(f"Delivery quantity {act['del_ratio']:.1f}x normal ({act['delivery_pct']:.0f}% delivered), "
                                      "genuine buying", "delivery", act["date"]))

    d5 = deals_summary(con, symbol, 7)
    if d5["buy"] or d5["sell"]:
        if d5["buy"] > d5["sell"] * 1.2:
            s_short += 10
            s_reasons.append(with_src(f"Bulk/block deals: net buying {d5['buy'] - d5['sell']:,.0f} shares this week",
                                      "deals", d5["df"].date.max()))
        elif d5["sell"] > d5["buy"] * 1.2:
            s_short -= 10
            s_reasons.append(with_src(f"Bulk/block deals: net selling {d5['sell'] - d5['buy']:,.0f} shares this week",
                                      "deals", d5["df"].date.max()))
        for r in d5["big"].itertuples():
            s_short += 12 if r.side == "BUY" else -12
            s_reasons.append(with_src(f"{r.big_investor} {'bought' if r.side == 'BUY' else 'sold'} {r.qty:,.0f} shares on {r.date}",
                                      "deals", r.date, url=getattr(r, "source", None) or None))

    px = history(con, symbol, 260)
    trend = None
    psrc = dict(key="prices", date=px.index[-1].strftime("%Y-%m-%d") if len(px) else None, ticker=_ticker(con, symbol))
    if len(px) >= 30:
        c = px["close"]
        r5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100
        above20 = c.iloc[-1] > c.tail(20).mean()
        rv = float(rsi(c).iloc[-1])
        if r5 > 0 and above20:
            s_short += 8
            s_reasons.append(with_src(f"Short-term uptrend (+{r5:.1f}% in 5 days, above 20-day average)", **psrc))
        elif r5 < 0 and not above20:
            s_short -= 8
            s_reasons.append(with_src(f"Short-term downtrend ({r5:.1f}% in 5 days, below 20-day average)", **psrc))
        if rv > 75:
            s_short -= 10
            s_reasons.append(with_src(f"Overbought (RSI {rv:.0f}), risk of pull-back", **psrc))
        elif rv < 30:
            s_short += 4
            s_reasons.append(with_src(f"Oversold (RSI {rv:.0f}), bounce possible", **psrc))
        if len(c) >= 200:
            trend = c.iloc[-1] > c.tail(200).mean()

    # long term
    l_reasons = []
    f = con.execute("SELECT score, brief, data, updated_at FROM fundamentals WHERE symbol=?", (symbol,)).fetchone()
    fscore = f["score"] if f else 50
    l = fscore * 0.6 + 20
    if f:
        fd = json.loads(f["data"] or "{}")
        l_reasons.append(with_src(f"Financial health score {fscore:.0f}/100", "fund", (f["updated_at"] or "")[:10],
                                  url=fd.get("source_url"), name=fd.get("source_name")))
        from .fundamentals import latest_quarter
        lq = latest_quarter(fd)
        if lq and (lq.get("profit_yoy") is not None or lq.get("sales_yoy") is not None):
            bits = [f"{k} {lq[k + '_yoy']:+.0f}%" for k in ("sales", "profit") if lq.get(k + "_yoy") is not None]
            l_reasons.append(with_src(f"Latest quarter ({lq['period']}) vs a year ago: " + ", ".join(bits), "fund",
                                      (f["updated_at"] or "")[:10], url=fd.get("source_url"), name=fd.get("source_name")))
    else:
        l_reasons.append("Financial health score 50/100 (no data yet, assumed average)")
    lnews = _news_rows(con, symbol, 30, ("long", "both"))
    lsum = max(-15, min(15, sum((r["sentiment"] or 0) * (r["impact_score"] or 0) for r in lnews) * 10))
    if abs(lsum) >= 2:
        l += lsum
        l_reasons.append(with_src(f"{'Positive' if lsum > 0 else 'Negative'} long-term news this month "
                                  f"(e.g. \"{lnews[0]['title'][:90]}\")", name=lnews[0]["source"],
                                  date=_ist(lnews[0]["published"]), url=lnews[0]["link"]))
    if trend is not None:
        l += 8 if trend else -5
        l_reasons.append(with_src("Price above its 200-day average (long-term uptrend)" if trend
                                  else "Price below its 200-day average (long-term downtrend)", **psrc))
    sh = shareholding_change(con, symbol)
    if sh:
        shsrc = dict(key="shp", date=f"quarter ending {sh['period']}", name="NSE shareholding pattern" if sh.get("source") in (None, "NSE")
                     else f"NSE shareholding pattern ({sh['source']})")
        if (sh.get("mutual_funds_chg") or 0) > 0.2:
            l += 5
            l_reasons.append(with_src(f"Mutual funds raised holding by {sh['mutual_funds_chg']:.2f}% last quarter", **shsrc))
        if (sh.get("fii_chg") or 0) > 0.2:
            l += 4
            l_reasons.append(with_src(f"FIIs raised holding by {sh['fii_chg']:.2f}% last quarter", **shsrc))
        if (sh.get("promoter_chg") or 0) < -0.5:
            l -= 6
            l_reasons.append(with_src(f"Promoters cut holding by {-sh['promoter_chg']:.2f}% last quarter", **shsrc))
        if sh["big_holders"]:
            l += 4
            l_reasons.append(with_src("Well-known investors hold it: " + ", ".join(sh["big_holders"]), **shsrc))
    d30 = deals_summary(con, symbol, 30)
    bigbuy = [r for r in d30["big"].itertuples() if r.side == "BUY"]
    if bigbuy:
        l += 6
        l_reasons.append(with_src("Bought by " + ", ".join(sorted({r.big_investor for r in bigbuy})) + " this month",
                                  "deals", max(r.date for r in bigbuy)))
    return round(max(-100, min(100, s_short)), 1), round(max(0, min(100, l)), 1), s_reasons, l_reasons


def update_signals(con, symbols, cfg):
    """Score each share and save today's scores; every day's scores stay in the database
    so the tracker can later check how its calls worked out."""
    today = date.today().isoformat()
    event_stats(con, fresh=True)
    for s in symbols:
        ss, ls, sr, lr = score_symbol(con, s, cfg)
        p = con.execute("SELECT close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 1", (s,)).fetchone()
        con.execute("INSERT OR REPLACE INTO signals (symbol, date, short_score, long_score, short_reasons, long_reasons, price) "
                    "VALUES (?,?,?,?,?,?,?)", (s, today, ss, ls, json.dumps(sr), json.dumps(lr), p[0] if p else None))
    con.commit()
    return len(symbols)


def _ticker(con, symbol):
    from .symbols import yahoo_ticker
    r = con.execute("SELECT * FROM symbols WHERE symbol=?", (symbol,)).fetchone()
    return yahoo_ticker(r) if r else None


# ---------- how did past calls work out? ----------

def _move_after(px, d, n):
    """Close n trading days after date d vs close on d (percent), or None if too recent."""
    after = px[px.index >= pd.Timestamp(d)]
    if len(after) <= n:
        return None
    return (float(after["close"].iloc[n]) / float(after["close"].iloc[0]) - 1) * 100


def track_record(con, symbol=None, days=365):
    """Check past recommendations against what the price did next: short-term buys after
    5 trading days, long-term buys after 20. -> dict(short=dict(n, hits, avg), long=...)"""
    since = (date.today() - timedelta(days=days)).isoformat()
    q = "SELECT symbol, date, short_score, long_score FROM signals WHERE date>=?"
    params = [since]
    if symbol:
        q += " AND symbol=?"
        params.append(symbol)
    sig = pd.read_sql_query(q, con, params=params)
    out = {"short": dict(n=0, hits=0, moves=[]), "long": dict(n=0, hits=0, moves=[])}
    cache = {}
    for r in sig.itertuples():
        for kind, ok, n in (("short", r.short_score >= 20, 5), ("long", r.long_score >= 60, 20)):
            if not ok:
                continue
            if r.symbol not in cache:
                cache[r.symbol] = history(con, r.symbol, 2000)
            mv = _move_after(cache[r.symbol], r.date, n) if not cache[r.symbol].empty else None
            if mv is None:
                continue
            o = out[kind]
            o["n"] += 1
            o["hits"] += mv > 0
            o["moves"].append(mv)
    for o in out.values():
        o["avg"] = float(np.mean(o.pop("moves"))) if o["n"] else None
    return out


def track_text(tr):
    lines = []
    for kind, lbl, n in (("short", "Short-term buy calls", 5), ("long", "Long-term buy calls", 20)):
        o = tr[kind]
        if o["n"]:
            lines.append(f"{lbl}: {o['hits']} of {o['n']} were higher {n} trading days later "
                         f"(average {o['avg']:+.1f}%)")
        else:
            lines.append(f"{lbl}: none old enough to check yet (needs {n} trading days)")
    return lines


def score_history(con, symbol, n=15):
    return pd.read_sql_query("SELECT date, short_score, long_score, price FROM signals WHERE symbol=? "
                             "ORDER BY date DESC LIMIT ?", con, params=(symbol, n))


def latest_signals(con):
    df = pd.read_sql_query("""SELECT s.*, y.name FROM signals s JOIN symbols y ON y.symbol=s.symbol
        WHERE s.date=(SELECT MAX(date) FROM signals)""", con)
    for c in ("short_reasons", "long_reasons"):
        df[c] = df[c].apply(json.loads)
    return df


def picks(con, n=15):
    df = latest_signals(con)
    if df.empty:
        return df, df
    short = df[df.short_score >= 20].sort_values("short_score", ascending=False).head(n)
    long = df[df.long_score >= 60].sort_values("long_score", ascending=False).head(n)
    return short, long
