"""Short plain-text views of the tracker's data, sized for a phone screen (used by the Telegram bot)."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import DISCLAIMER, analysis, portfolio, sources
from .nlp import HORIZON_TEXT
from .prices import history

ICON = {"not_traded": "⏳ still to come", "pending": "⏳ not yet in price", "partly": "◐ partly in price",
        "priced_in": "✅ already in price", "opposite": "↔ price moved opposite", "neutral": "– neutral",
        "no_price": "? no price data", None: "?"}


def qty(x):
    if x is None or pd.isna(x):
        return "–"
    x = float(x)
    return f"{x/1e7:.2f} Cr" if x >= 1e7 else f"{x/1e5:.2f} L" if x >= 1e5 else f"{x:,.0f}"


def _dot(s):
    return "🟢" if (s or 0) > 0.15 else "🔴" if (s or 0) < -0.15 else "⚪"


def picks(con, n=8):
    short, long = analysis.picks(con, n)
    out = ["📈 SHORT TERM IDEAS (days–weeks)"]
    out += [f"\n{r.symbol} · score {r.short_score:.0f}\n" + "\n".join(f"  • {sources.short(x)}" for x in r.short_reasons[:3])
            for r in short.itertuples()] or ["  none pass the bar today"]
    out += ["\n🌱 LONG TERM IDEAS (months–years)"]
    out += [f"\n{r.symbol} · score {r.long_score:.0f}/100\n" + "\n".join(f"  • {sources.short(x)}" for x in r.long_reasons[:3])
            for r in long.itertuples()] or ["  none pass the bar today"]
    out.append("\nSend /report SYMBOL for the full report.\n\n" + DISCLAIMER)
    return "\n".join(out)


def share(con, cfg, sym):
    sym = portfolio.resolve(con, sym)
    row = con.execute("SELECT name FROM symbols WHERE symbol=?", (sym,)).fetchone()
    out = [f"🔎 {sym} — {row['name'] if row else ''}"]
    px = history(con, sym, 5)
    if not px.empty:
        last = px.close.iloc[-1]
        prev = px.close.iloc[-2] if len(px) > 1 else last
        out.append(f"Price ₹{last:,.2f} ({(last/prev-1)*100:+.2f}%)")
    sig = con.execute("SELECT * FROM signals WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)).fetchone()
    if sig:
        ss, ls, sr, lr = sig["short_score"], sig["long_score"], json.loads(sig["short_reasons"]), json.loads(sig["long_reasons"])
    else:
        ss, ls, sr, lr = analysis.score_symbol(con, sym, cfg)
    f = con.execute("SELECT brief FROM fundamentals WHERE symbol=?", (sym,)).fetchone()
    if f:
        out.append("\n📋 " + f["brief"])
    out.append(f"\nShort-term score {ss:.0f} (−100…100)")
    out += [f"  • {sources.short(x)}" for x in sr[:4]]
    out.append(f"Long-term score {ls:.0f}/100")
    out += [f"  • {sources.short(x)}" for x in lr[:4]]
    act = analysis.activity(con, sym)
    if act:
        chg = "" if act["qty_change_pct"] is None else f" ({act['qty_change_pct']:+.0f}% vs prev day)"
        out.append(f"\n📊 {act['date']}: traded {qty(act['traded_qty'])}{chg}, delivery {qty(act['delivery_qty'])}"
                   + ("" if pd.isna(act["delivery_pct"]) else f" ({act['delivery_pct']:.0f}%)"))
        if act["vol_ratio"]:
            out.append(f"  volume {act['vol_ratio']:.1f}x 20-day avg"
                       + (f", delivery {act['del_ratio']:.1f}x" if act["del_ratio"] else ""))
    d = analysis.deals_summary(con, sym, 90)["df"]
    if not d.empty:
        out.append("\n🤝 Bulk/block deals (90 days)")
        out += [f"  {r.date} {r.side} {qty(r.qty)} @ ₹{r.price:,.2f} by {r.client}" + (" ⭐" if r.big_investor else "")
                for r in d.head(5).itertuples()]
    sh = analysis.shareholding_change(con, sym)
    if sh:
        parts = []
        for k, lbl in (("promoter", "Promoter"), ("fii", "FII"), ("dii", "DII"), ("mutual_funds", "MF")):
            if sh.get(k) is not None:
                c = sh.get(k + "_chg")
                parts.append(f"{lbl} {sh[k]:.1f}%" + ("" if c is None else f" ({c:+.2f})"))
        out.append(f"\n🏦 Holding ({sh['period']}): " + ", ".join(parts))
        if sh["big_holders"]:
            out.append("  ⭐ " + ", ".join(sh["big_holders"]))
    out.append("\n📰 " + news(con, sym, 5))
    return "\n".join(out)


def news(con, sym=None, n=10):
    since = (datetime.now(timezone.utc) - timedelta(days=7 if sym else 2)).isoformat()
    q = """SELECT n.title, n.link, n.published, ns.symbol, ns.sentiment, ns.horizon, ns.reaction_status, ns.reaction_pct,
           ns.impact_score FROM news_symbols ns JOIN news n ON n.id=ns.news_id WHERE n.published>=?"""
    args = [since]
    if sym:
        q += " AND ns.symbol=?"
        args.append(sym)
    rows = con.execute(q + " ORDER BY ns.impact_score DESC LIMIT ?", (*args, n)).fetchall()
    if not rows:
        return "No recent news."
    out = ["NEWS" + (f" for {sym}" if sym else " (last 48 h, most important first)")]
    for r in rows:
        mv = "" if r["reaction_pct"] is None else f" {r['reaction_pct']:+.1f}%"
        out.append(f"\n{_dot(r['sentiment'])} {'' if sym else r['symbol'] + ': '}{r['title']}\n"
                   f"   {HORIZON_TEXT.get(r['horizon'], r['horizon'])} · {ICON.get(r['reaction_status'], '?')}{mv}\n   {r['link']}")
    return "\n".join(out)


def my(con):
    pos = portfolio.active(con)
    if pos.empty:
        return "You are not tracking any shares yet. Try:\n/bought INFY 10 1500"
    out = ["💼 YOUR SHARES"]
    for p in pos.itertuples():
        h = history(con, p.symbol, 2)
        last = h.close.iloc[-1] if not h.empty else None
        line = f"\n#{p.id} {p.symbol} · {portfolio.KINDS[p.kind]}"
        if last is not None:
            line += f"\n   now ₹{last:,.2f}"
        has = lambda v: v is not None and pd.notna(v)
        if has(p.price):
            line += f" · {'sold' if p.kind in ('sold', 'short') else 'bought'} at ₹{p.price:,.2f}"
        if has(p.price) and last is not None and p.kind in ("bought", "short"):
            sign = -1 if p.kind == "short" else 1
            pnl = (last / p.price - 1) * 100 * sign
            line += f" · P&L {pnl:+.1f}%"
            if has(p.qty):
                line += f" (₹{(last - p.price) * p.qty * sign:+,.0f})"
        if has(p.target):
            line += f" · {'wanted price' if p.kind.startswith('want') else 'target'} ₹{p.target:,.2f}"
        if has(p.stop_loss):
            line += f" · stop ₹{p.stop_loss:,.2f}"
        out.append(line)
    out.append("\nRemove one with /remove ID")
    return "\n".join(out)


def fii(con):
    df = pd.read_sql_query("SELECT * FROM fii_dii ORDER BY date DESC LIMIT 10", con)
    if df.empty:
        return "No FII/DII data yet."
    out = ["🌍 FII / DII net (₹ crore)"]
    for d, g in df.groupby("date", sort=False):
        out.append(f"{d}: " + ", ".join(f"{r.category} {r.net:+,.0f}" for r in g.itertuples()))
    return "\n".join(out)


def big_deals(con, days=14):
    df = pd.read_sql_query("SELECT * FROM deals WHERE big_investor IS NOT NULL AND date>=? ORDER BY date DESC LIMIT 15",
                           con, params=((datetime.now() - timedelta(days=days)).date().isoformat(),))
    if df.empty:
        return f"No deals by well-known investors in the last {days} days."
    return "⭐ BIG INVESTOR DEALS\n" + "\n".join(
        f"{r.date} {r.big_investor} {'bought' if r.side == 'BUY' else 'sold'} {qty(r.qty)} {r.symbol} @ ₹{r.price:,.2f}"
        for r in df.itertuples())
