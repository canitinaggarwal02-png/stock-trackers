"""Target prices and the time they may take, for the short-term and long-term views.

Short term: 3x the average daily range above today's price (same as the report's indicative
target). The expected time comes from this share's own past year: from every day, how many
trading days it took the price to rise by the same percentage, and how often it did so within
two months.

Long term (about 12 months): the analysts' average target from Yahoo Finance when there is one;
otherwise today's price grown by the average of the company's earnings growth, sales growth
and the share's own average yearly return, each kept between 0% and 30%."""
import json

from . import report, sources
from .prices import history

WINDOW = 40          # trading days looked at for the short-term target (about 2 months)


def _days_to_rise(px, pct):
    close, high = px["close"].values, px["high"].values
    days, tried = [], 0
    for i in range(max(0, len(close) - 250 - WINDOW), len(close) - WINDOW):
        tried += 1
        goal = close[i] * (1 + pct / 100)
        for j in range(i + 1, i + WINDOW + 1):
            if high[j] >= goal:
                days.append(j - i)
                break
    if not tried:
        return None, None
    days.sort()
    return (days[len(days) // 2] if days else None), len(days) / tried * 100


def short_target(px):
    lv = report.levels(px)
    if not lv:
        return None
    pct = (lv["target"] / lv["last"] - 1) * 100
    median, hit = _days_to_rise(px, pct)
    return dict(target=lv["target"], pct=pct, stop=lv["stop"], days=median, hit=hit,
                stop_pct=(lv["stop"] / lv["last"] - 1) * 100)


def long_target(px, fd, updated=""):
    if px is None or px.empty:
        return None
    last = float(px["close"].iloc[-1])
    t, n = fd.get("targetMeanPrice"), fd.get("numberOfAnalystOpinions")
    if t and t > 0:
        basis = sources.with_src(f"Average of {n or 'several'} analysts' 12-month targets", "fund", updated,
                                 url=fd.get("source_url"))
        return dict(target=float(t), pct=(t / last - 1) * 100, months=12, basis=basis)
    parts, c = [], px["close"]
    for k, what in (("earningsGrowth", "earnings growth"), ("revenueGrowth", "sales growth")):
        if fd.get(k) is not None:
            parts.append((min(max(fd[k], 0), 0.30), what))
    yrs = (c.index[-1] - c.index[0]).days / 365
    if yrs >= 1:
        cagr = (float(c.iloc[-1]) / float(c.iloc[0])) ** (1 / yrs) - 1
        parts.append((min(max(cagr, 0), 0.30), f"the share's own average yearly return over {yrs:.0f} years"))
    if not parts:
        return None
    g = sum(x for x, _ in parts) / len(parts)
    text = ("Today's price grown by the average of " + ", ".join(f"{w} ({x * 100:.0f}%)" for x, w in parts)
            + " = " + f"{g * 100:.0f}% a year (each kept within 0–30%)")
    basis = sources.with_src(text, "fund", updated, url=fd.get("source_url")) if len(parts) > 1 or yrs < 1 else text
    return dict(target=last * (1 + g), pct=g * 100, months=12, basis=basis)


def for_symbol(con, sym):
    px = history(con, sym, 1300)
    f = con.execute("SELECT data, updated_at FROM fundamentals WHERE symbol=?", (sym,)).fetchone()
    fd = json.loads(f["data"]) if f and f["data"] else {}
    return dict(short=short_target(px.tail(400)) if not px.empty else None,
                long=long_target(px, fd, (f["updated_at"] or "")[:10] if f else ""))


def short_text(s):
    if not s:
        return ""
    when = f"usually within ~{s['days']} trading days" if s["days"] else "rarely reached within 2 months"
    return f"🎯 ₹{s['target']:,.0f} ({s['pct']:+.1f}%), {when}"


def long_text(lt):
    return f"🎯 ₹{lt['target']:,.0f} ({lt['pct']:+.0f}%) in ~{lt['months']} months" if lt else ""


def lines(t):
    """Markdown lines for the report section."""
    out = []
    s, lt = t["short"], t["long"]
    if s:
        out.append(f"- **Short term:** target **₹{s['target']:,.2f}** ({s['pct']:+.1f}%), stop loss ₹{s['stop']:,.2f} ({s['stop_pct']:+.1f}%)")
        if s["days"]:
            out.append(f"- Expected time: about **{s['days']} trading days** (~{max(1, round(s['days'] / 5))} weeks). "
                       f"In the past year this share rose {s['pct']:.1f}% within 2 months {s['hit']:.0f}% of the time.")
        else:
            out.append(f"- In the past year this share rarely rose {s['pct']:.1f}% within 2 months ({s['hit'] or 0:.0f}% of the time), so treat it as a stretch.")
        out.append("- How the short-term target is set: " + sources.md(sources.with_src(
            "3× the average daily price range (ATR) above today's price; time from this share's own past moves", "prices")))
    if lt:
        out.append(f"- **Long term:** target **₹{lt['target']:,.2f}** ({lt['pct']:+.0f}%) in about **{lt['months']} months**")
        out.append(f"- How the long-term target is set: {sources.md(lt['basis'])}")
    if not out:
        out.append("- Not enough price history to set targets yet.")
    out.append("- _Targets are estimates from past data, not promises. Prices can fall instead._")
    return out
