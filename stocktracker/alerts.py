"""Reminders with a recommendation and the reasons behind it, for every share
you told the tracker about."""
from datetime import datetime, timedelta, timezone

from . import notify, sources
from .analysis import STATUS_TEXT, activity, deals_summary, score_symbol
from .portfolio import active
from .nlp import HORIZON_TEXT
from .prices import history, rsi


def _recent_news(con, symbol, hours=48):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return con.execute("""SELECT ns.*, n.title, n.source, n.link, n.published FROM news_symbols ns JOIN news n ON n.id=ns.news_id
        WHERE ns.symbol=? AND n.published>=? ORDER BY ns.impact_score DESC""", (symbol, since)).fetchall()


def evaluate_position(con, p, cfg):
    """Return a list of (rule_key, action, reason) for one position."""
    a = cfg["alerts"]
    px = history(con, p["symbol"], 260)
    if px.empty:
        return []
    last = float(px["close"].iloc[-1])
    prev = float(px["close"].iloc[-2]) if len(px) > 1 else last
    day = (last / prev - 1) * 100
    r = float(rsi(px["close"]).iloc[-1]) if len(px) > 20 else 50
    ss, ls, sreasons, lreasons = score_symbol(con, p["symbol"], cfg)
    news = _recent_news(con, p["symbol"])
    bad = [n for n in news if (n["sentiment"] or 0) <= -0.3 and (n["impact_score"] or 0) >= 0.5]
    good = [n for n in news if (n["sentiment"] or 0) >= 0.3 and (n["impact_score"] or 0) >= 0.5]
    act = activity(con, p["symbol"])
    deals = deals_summary(con, p["symbol"], 3)
    kind, ref = p["kind"], p["price"]
    out = []

    def news_line(n):
        return sources.with_src(f"\"{n['title'][:120]}\" — {n['explanation'].split(':')[0]}, "
                                f"effect {HORIZON_TEXT.get(n['horizon'], n['horizon'])}. "
                                f"{STATUS_TEXT.get(n['reaction_status'] or 'no_price', '')}",
                                name=n["source"], date=(n["published"] or "")[:10], url=n["link"])

    from .analysis import _ticker
    psrc = sources.tag("prices", px.index[-1].strftime("%Y-%m-%d"), ticker=_ticker(con, p["symbol"]))

    if kind == "bought" and ref:
        pnl = (last / ref - 1) * 100
        sl = p["stop_loss"] or ref * (1 - a["stop_loss_pct"] / 100)
        tg = p["target"] or ref * (1 + a["target_pct"] / 100)
        if last <= sl:
            why = [f"Price ₹{last:.2f} is at/below your stop loss ₹{sl:.2f} (P&L {pnl:+.1f}%)."]
            why += [news_line(n) for n in bad[:2]]
            why.append("Long-term picture still looks fine, so a partial exit may be enough." if ls >= 65
                       else "Long-term signals are not strong either.")
            out.append(("stoploss", "SELL / EXIT (stop loss hit)", why))
        if last >= tg:
            why = [f"Price ₹{last:.2f} reached your target ₹{tg:.2f} (P&L {pnl:+.1f}%)."]
            why.append("Momentum is still positive; consider booking part and trailing a stop on the rest." if ss > 15
                       else "Momentum is fading; booking profit looks sensible.")
            out.append(("target", "BOOK PROFIT", why))
    if kind == "short" and ref:
        sl = p["stop_loss"] or ref * (1 + a["stop_loss_pct"] / 100)
        if last >= sl:
            out.append(("stoploss", "COVER SHORT (stop loss hit)", [f"Price ₹{last:.2f} rose above your stop ₹{sl:.2f}."]))
        if p["target"] and last <= p["target"]:
            out.append(("target", "COVER SHORT (target reached)", [f"Price ₹{last:.2f} reached your target ₹{p['target']:.2f}."]))
    if kind == "sold" and ref:
        chg = (last / ref - 1) * 100
        if chg <= -10 and ls >= 60:
            out.append(("rebuy", "CONSIDER RE-BUYING", [f"Price is {chg:.1f}% below where you sold (₹{ref:.2f}) and the "
                                                        f"long-term score is {ls:.0f}/100."] + lreasons[:3]))
        if chg >= 10 and good:
            out.append(("missed", "REVIEW (rising after you sold)", [f"Price is {chg:+.1f}% above your sell price."] +
                        [news_line(n) for n in good[:2]]))
    if kind == "want_buy":
        if p["target"] and last <= p["target"]:
            why = [f"Price ₹{last:.2f} is at/below your buy price ₹{p['target']:.2f}."]
            if bad:
                why += ["But there is negative news; check it first:"] + [news_line(n) for n in bad[:2]]
                out.append(("buyzone", "BUY ZONE REACHED – CHECK NEWS FIRST", why))
            else:
                out.append(("buyzone", "BUY ZONE REACHED", why + lreasons[:2]))
        if ss >= 25 and r < 70 and not bad:
            out.append(("favourable", "GOOD TIME TO BUY (short term)", sreasons[:4]))
        if bad:
            out.append(("wait", "WAIT – NEGATIVE NEWS", [news_line(n) for n in bad[:3]]))
        elif r > 75:
            out.append(("overbought", "WAIT FOR A DIP", [f"RSI {r:.0f}: the share is overbought after a sharp rise."]))
    if kind == "want_sell":
        if p["target"] and last >= p["target"]:
            out.append(("sellzone", "SELL PRICE REACHED", [f"Price ₹{last:.2f} is at/above your sell price ₹{p['target']:.2f}."]))
        if bad or ss <= -25:
            out.append(("sellsoon", "CONSIDER SELLING SOONER", [news_line(n) for n in bad[:2]] + sreasons[:3]))

    if kind in ("bought", "want_sell"):
        if bad:
            out.append(("badnews", "REVIEW – NEGATIVE NEWS", [news_line(n) for n in bad[:3]]))
        elif good and kind == "bought":
            out.append(("goodnews", "HOLD – POSITIVE NEWS", [news_line(n) for n in good[:3]]))
        big_sell = [d for d in deals["big"].itertuples() if d.side == "SELL"]
        if big_sell:
            out.append(("bigsell", "CAUTION – BIG INVESTOR SELLING",
                        [sources.with_src(f"{d.big_investor} sold {d.qty:,.0f} shares at ₹{d.price:.2f} on {d.date} ({d.kind} deal)",
                                          "deals", d.date) for d in big_sell]))
        if act and act["vol_ratio"] and act["vol_ratio"] >= a["volume_spike_ratio"] and (act["price_change_pct"] or 0) < 0:
            out.append(("volsell", "CAUTION – HEAVY SELLING",
                        [sources.with_src(f"Volume {act['vol_ratio']:.1f}x normal while price fell {act['price_change_pct']:.1f}%.",
                                          "delivery" if act.get("from_delivery") else "prices", act["date"])]))
        if ss <= -30:
            out.append(("weak", "CAUTION – WEAK SHORT-TERM SIGNALS", sreasons[:4]))
    if abs(day) >= a["big_move_pct"]:
        out.append((f"move{date_key()}", f"BIG MOVE {day:+.1f}% TODAY",
                    [f"{p['symbol']} moved {day:+.1f}% to ₹{last:.2f}."] + ([news_line(n) for n in news[:2]] or
                                                                          ["No matching news found; check exchange announcements."])))
    # price-based lines get the price source
    return [(k, a, [w + psrc if w and "[Source:" not in w and ("₹" in w or "%" in w) else w for w in why])
            for k, a, why in out]


def date_key():
    return datetime.now().strftime("%Y%m%d")


def run_alerts(con, cfg, send=True):
    pos = active(con)
    min_h = cfg["alerts"]["min_hours_between_same_alert"]
    new = []
    for p in pos.to_dict("records"):
        for rule, action, why in evaluate_position(con, p, cfg):
            key = f"{p['id']}:{rule}"
            last = con.execute("SELECT created_at FROM alerts WHERE key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            if last and datetime.now() - datetime.fromisoformat(last[0]) < timedelta(hours=min_h):
                continue
            title = f"{p['symbol']}: {action}"
            body = "\n".join(f"• {w}" for w in why if w)
            con.execute("INSERT INTO alerts (symbol, key, action, title, body, created_at, sent) VALUES (?,?,?,?,?,?,?)",
                        (p["symbol"], key, action, title, body, datetime.now().isoformat(timespec="seconds"), 0))
            new.append((title, body))
    con.commit()
    if send:
        by_symbol = {}
        for title, body in new:
            sym, action = title.split(": ", 1)
            by_symbol.setdefault(sym, []).append((action, body))
        for sym, items in by_symbol.items():
            notify.send(cfg["notifications"], f"{sym}: " + " | ".join(a for a, _ in items),
                        "\n\n".join(f"{a}\n{b}" for a, b in items))
        con.execute("UPDATE alerts SET sent=1 WHERE sent=0")
        con.commit()
    return new
