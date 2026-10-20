"""Full report for one share: the recommendation, the reasons behind it, and every
piece of data the tracker has gathered (news, activity, deals, holdings, financials)."""
import html
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import DISCLAIMER, analysis, sources
from .sources import src_md
from .nlp import HORIZON_TEXT
from .prices import history, rsi

IST = timezone(timedelta(hours=5, minutes=30))


def _q(x):
    if x is None or pd.isna(x):
        return "–"
    x = float(x)
    return f"{x/1e7:.2f} Cr" if x >= 1e7 else f"{x/1e5:.2f} L" if x >= 1e5 else f"{x:,.0f}"


def _pct(x):
    return "–" if x is None or pd.isna(x) else f"{x*100:.1f}%"


def verdicts(ss, ls):
    """Plain-language calls from the two scores."""
    out = []
    if ss >= 40:
        out.append(("Short-term BUY candidate (strong)", "good"))
    elif ss >= 20:
        out.append(("Short-term BUY candidate", "good"))
    elif ss <= -25:
        out.append(("Short term: AVOID / be careful", "bad"))
    else:
        out.append(("Short term: no clear signal, wait", "neutral"))
    if ls >= 75:
        out.append(("Long-term BUY candidate (strong)", "good"))
    elif ls >= 60:
        out.append(("Long-term BUY candidate", "good"))
    elif ls < 40:
        out.append(("Long term: weak, avoid for now", "bad"))
    else:
        out.append(("Long term: average, keep watching", "neutral"))
    return out


def levels(px):
    """Indicative entry / stop loss / short-term target from recent volatility."""
    if px is None or len(px) < 25:
        return None
    c = px["close"]
    tr = pd.concat([px["high"] - px["low"], (px["high"] - c.shift()).abs(), (px["low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    last = float(c.iloc[-1])
    stop = min(float(px["low"].tail(20).min()), last - 2 * atr)
    return dict(last=last, atr=atr, stop=stop, target=last + 3 * atr,
                hi52=float(px["high"].tail(250).max()), lo52=float(px["low"].tail(250).min()),
                rsi=float(rsi(c).iloc[-1]),
                r1m=(last / float(c.iloc[-22]) - 1) * 100 if len(c) > 22 else None,
                r1y=(last / float(c.iloc[0]) - 1) * 100 if len(c) > 200 else None,
                dma50=float(c.tail(50).mean()), dma200=float(c.tail(200).mean()) if len(c) >= 200 else None)


def build(con, cfg, sym):
    """Return dict(symbol, name, sections=[(title, [markdown lines])], verdicts, scores)."""
    row = con.execute("SELECT * FROM symbols WHERE symbol=?", (sym,)).fetchone()
    name = row["name"] if row else sym
    ss, ls, sr, lr = analysis.score_symbol(con, sym, cfg)
    px = history(con, sym, 400)
    lv = levels(px)
    f = con.execute("SELECT * FROM fundamentals WHERE symbol=?", (sym,)).fetchone()
    fd = json.loads(f["data"]) if f else {}
    S = []

    # 1. recommendation and basis
    v = verdicts(ss, ls)
    lines = [f"**{t}**" for t, _ in v]
    lines.append(f"Short-term score **{ss:.0f}** (−100 to +100) · Long-term score **{ls:.0f}/100**")
    lines.append("")
    lines.append("**Why (short term):**")
    lines += [f"- {sources.md(x)}" for x in sr] or ["- No strong short-term signal from news, volume, deals or trend."]
    lines.append("**Why (long term):**")
    lines += [f"- {sources.md(x)}" for x in lr]
    S.append(("Recommendation and its basis", lines))
    from . import targets
    S.append(("Target price and expected time", targets.lines(targets.for_symbol(con, sym))))

    # 2. risks
    risks = []
    if f:
        from .fundamentals import assess
        _, _, bad, _ = assess(fd)
        risks += [sources.with_src(b, "fund", (f["updated_at"] or "")[:10], url=fd.get("source_url")) for b in bad]
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    neg = con.execute("""SELECT n.title, n.source, n.link, n.published FROM news_symbols ns JOIN news n ON n.id=ns.news_id
        WHERE ns.symbol=? AND n.published>=? AND ns.sentiment<=-0.3 ORDER BY ns.impact_score DESC LIMIT 3""",
                      (sym, since)).fetchall()
    risks += [sources.with_src(f"Negative news: \"{r['title']}\"", name=r["source"], date=r["published"][:10], url=r["link"])
              for r in neg]
    if lv and lv["rsi"] > 70:
        risks.append(f"Overbought (RSI {lv['rsi']:.0f}); a pull-back is common after sharp rises")
    d30 = analysis.deals_summary(con, sym, 30)
    risks += [sources.with_src(f"{r.big_investor} sold {_q(r.qty)} shares on {r.date}", "deals", r.date)
              for r in d30["big"].itertuples() if r.side == "SELL"]
    sh = analysis.shareholding_change(con, sym)
    if sh and (sh.get("promoter_chg") or 0) < -0.5:
        risks.append(sources.with_src(f"Promoters reduced holding by {-sh['promoter_chg']:.2f}% last quarter", "shp",
                                      f"quarter ending {sh['period']}"))
    S.append(("Risks / what could go wrong", [f"- {sources.md(x)}" for x in risks] or ["- No major red flags found in the data gathered."]))

    # 3. price levels
    if lv:
        lines = [f"- Last price **₹{lv['last']:,.2f}**"
                 + (f" · 1 month {lv['r1m']:+.1f}%" if lv["r1m"] is not None else "")
                 + (f" · 1 year {lv['r1y']:+.1f}%" if lv["r1y"] is not None else ""),
                 f"- 52-week range ₹{lv['lo52']:,.2f} – ₹{lv['hi52']:,.2f}",
                 f"- 50-day average ₹{lv['dma50']:,.2f}" + (f" · 200-day average ₹{lv['dma200']:,.2f}" if lv["dma200"] else ""),
                 f"- RSI {lv['rsi']:.0f} (above 70 overbought, below 30 oversold)",
                 f"- Indicative stop loss **₹{lv['stop']:,.2f}** (below recent low / 2× daily range)"]
        if ss > 0:
            lines.append(f"- Indicative short-term target **₹{lv['target']:,.2f}** (3× average daily range)")
        lines.append(src_md("prices", f"up to {px.index[-1]:%Y-%m-%d}", ticker=analysis._ticker(con, sym)))
        S.append(("Price and levels", lines))

    # 4. news
    nd = con.execute("""SELECT n.title, n.source, n.link, n.published, ns.* FROM news_symbols ns JOIN news n ON n.id=ns.news_id
        WHERE ns.symbol=? ORDER BY n.published DESC LIMIT 15""", (sym,)).fetchall()
    lines = []
    stats = analysis.event_stats(con)
    for r in nd:
        m = stats.get((r["event_type"], 1 if (r["sentiment"] or 0) > 0 else -1))
        past = (f"  \n  {analysis.past_line(m, (r['sentiment'] or 0) > 0)}."
                if m and m["n"] >= 3 and abs(r["sentiment"] or 0) >= 0.15 else "")
        dot = "🟢" if r["sentiment"] > 0.15 else "🔴" if r["sentiment"] < -0.15 else "⚪"
        when = datetime.fromisoformat(r["published"]).astimezone(IST).strftime("%d %b %H:%M")
        lines.append(f"- {dot} **{when}** [{r['title']}]({r['link']})  \n"
                     f"  {r['explanation'].split(' (')[0]}  \n"
                     f"  Effect: {HORIZON_TEXT.get(r['horizon'], r['horizon'])}. "
                     + ("" if r["reaction_status"] is None
                        else analysis.reaction_text(r['reaction_status'], r['reaction_pct'], r['horizon']))
                     + ("" if r["move_5d"] is None else f" 5 trading days after the news it was {r['move_5d']:+.1f}%.")
                     + past + f"  \n  _Source: [{sources._clean(r['source'])}]({r['link']}) · {when}_")
    S.append(("News and its effect on the price", lines or ["- No news matched to this share yet."]))

    # 5. buying / selling activity
    act = analysis.activity(con, sym)
    if act:
        lines = [f"- On {act['date']}: traded **{_q(act['traded_qty'])}** shares"
                 + ("" if act["qty_change_pct"] is None else f" ({act['qty_change_pct']:+.0f}% vs previous day)"),
                 f"- Delivery (actually bought to keep): **{_q(act['delivery_qty'])}**"
                 + ("" if pd.isna(act["delivery_pct"]) else f" = {act['delivery_pct']:.0f}% of traded"),
                 "- Volume vs 20-day average: " + ("–" if not act["vol_ratio"] else f"{act['vol_ratio']:.1f}x"),
                 "- Delivery vs 20-day average: " + ("–" if not act["del_ratio"] else f"{act['del_ratio']:.1f}x"),
                 "", "| Date | Traded | Delivery | Delivery % | Close |", "|---|---|---|---|---|"]
        for t in act["table"].tail(7).iloc[::-1].itertuples():
            lines.append(f"| {t.date} | {_q(t.traded_qty)} | {_q(t.delivery_qty)} | "
                         f"{'–' if pd.isna(t.delivery_pct) else f'{t.delivery_pct:.0f}%'} | ₹{t.close:,.2f} |")
        lines.append(src_md("delivery", f"up to {act['date']}") if act.get("from_delivery")
                     else src_md("prices", f"up to {act['date']}", ticker=analysis._ticker(con, sym)))
        S.append(("Shares bought and sold daily", lines))

    # 6. deals
    d90 = analysis.deals_summary(con, sym, 90)["df"]
    if d90.empty:
        lines = ["- No bulk or block deals in the last 90 days."]
    else:
        lines = ["| Date | Who | Buy/Sell | Qty | Price | Type |", "|---|---|---|---|---|---|"]
        lines += [f"| {r.date} | {r.client}{' ⭐' if r.big_investor else ''} | {r.side} | {_q(r.qty)} | ₹{r.price:,.2f} | {r.kind} |"
                  for r in d90.itertuples()]
        lines.append("⭐ = well-known investor")
        lines.append(src_md("deals", f"latest {d90.date.max()}",
                            url=next((u for u in d90.get("source", pd.Series(dtype=str)).dropna()), None)))
    S.append(("Bulk and block deals (90 days)", lines))

    # 7. ownership
    if sh:
        lines = ["| Holder | Holding | Change vs last quarter |", "|---|---|---|"]
        for k, lbl in (("promoter", "Promoters"), ("fii", "FII / FPI"), ("dii", "DII (all domestic institutions)"),
                       ("mutual_funds", "Mutual funds"), ("public", "Public")):
            if sh.get(k) is not None:
                c = sh.get(k + "_chg")
                lines.append(f"| {lbl} | {sh[k]:.2f}% | {'' if c is None else f'{c:+.2f}%'} |")
        lines.append(f"Quarter ending {sh['period']}.")
        past_q = con.execute("SELECT * FROM shareholding WHERE symbol=? ORDER BY period DESC LIMIT 8", (sym,)).fetchall()
        if len(past_q) > 2:
            lines += ["**Past quarters (from your records):**", "| Quarter | Promoters | FII | DII | MF |", "|---|---|---|---|---|"]
            lines += [f"| {q['period']} | " + " | ".join("–" if q[k] is None else f"{q[k]:.2f}%"
                                                         for k in ("promoter", "fii", "dii", "mutual_funds")) + " |"
                      for q in past_q]
        lines.append(src_md("shp", f"quarter ending {sh['period']}"))
        if sh["big_holders"]:
            lines.append("**Well-known investors holding it:** " + ", ".join(sh["big_holders"]))
    else:
        lines = ["- No shareholding data yet."]
    bigs = d90[d90.big_investor.notna()] if not d90.empty else d90
    if not bigs.empty:
        lines.append("**Well-known investors in recent deals:** " + ", ".join(
            f"{r.big_investor} ({'bought' if r.side == 'BUY' else 'sold'} {_q(r.qty)} on {r.date})" for r in bigs.itertuples()))
    S.append(("Who owns it: promoters, FII, DII, mutual funds, big investors", lines))

    # 8. financials
    if f:
        h = fd.get("history") or {}
        lines = [f["brief"], "", "| Measure | Value |", "|---|---|"]
        for lbl, val in (("Sector", fd.get("sector") or "–"),
                         ("Market cap", "–" if not fd.get("marketCap") else f"₹{fd['marketCap']/1e7:,.0f} Cr"),
                         ("P/E", "–" if fd.get("trailingPE") is None else f"{fd['trailingPE']:.1f}"),
                         ("Price / book", "–" if fd.get("priceToBook") is None else f"{fd['priceToBook']:.1f}"),
                         ("Return on equity", _pct(fd.get("returnOnEquity"))),
                         ("Debt / equity", "–" if fd.get("debtToEquity") is None else f"{fd['debtToEquity']/100:.2f}"),
                         ("Profit margin", _pct(fd.get("profitMargins"))),
                         ("Sales growth (yoy)", _pct(fd.get("revenueGrowth"))),
                         ("Profit growth (yoy)", _pct(fd.get("earningsGrowth"))),
                         ("Dividend yield", _pct(fd.get("dividendYield")))):
            lines.append(f"| {lbl} | {val} |")
        for key, lbl in (("Total Revenue", "Sales"), ("Net Income", "Net profit")):
            if h.get(key):
                lines.append(f"\n**{lbl} by year:** " + " → ".join(
                    f"{k[:4]}: ₹{v/1e7:,.0f} Cr" for k, v in sorted(h[key].items())))
        q = fd.get("quarters") or {}
        if q:
            from .fundamentals import latest_quarter
            lines += ["", "**Quarterly results:**", "| Quarter ending | Sales | Net profit |", "|---|---|---|"]
            lines += [f"| {k} | " + " | ".join("–" if v.get(c) is None else f"₹{v[c]/1e7:,.0f} Cr" for c in ("sales", "profit")) + " |"
                      for k, v in sorted(q.items(), reverse=True)[:6]]
            lq = latest_quarter(fd)
            if lq and (lq.get("sales_yoy") is not None or lq.get("profit_yoy") is not None):
                lines.append("Latest quarter vs same quarter last year: "
                             + ", ".join(f"{k} {lq[k + '_yoy']:+.1f}%" for k in ("sales", "profit") if lq.get(k + "_yoy") is not None))
        lines.append(src_md("fund", f"fetched {(f['updated_at'] or '')[:10]}", url=fd.get("source_url"),
                            name=fd.get("source_name")))
        S.append(("Financial analysis", lines))
    else:
        S.append(("Financial analysis", ["- Not fetched yet (it is fetched in the next update for recommended and tracked shares)."]))

    # 9. history from the tracker's own records
    hist = analysis.score_history(con, sym, 12)
    tr = analysis.track_record(con, sym)
    lines = ["The tracker saves its scores every update, so you can see how its past calls on this share worked out.", ""]
    lines += [f"- {t}" for t in analysis.track_text(tr)]
    if len(hist):
        lines += ["", "| Date | Short score | Long score | Price then |", "|---|---|---|---|"]
        lines += [f"| {h.date} | {h.short_score:.0f} | {h.long_score:.0f} | "
                  f"{'–' if h.price is None or pd.isna(h.price) else f'₹{h.price:,.2f}'} |" for h in hist.itertuples()]
    lines.append(src_md("records", f"{len(hist)} saved days"))
    S.append(("History from your records", lines))

    # 10. market backdrop
    fii = con.execute("SELECT * FROM fii_dii WHERE date=(SELECT MAX(date) FROM fii_dii)").fetchall()
    if fii:
        S.append(("Market backdrop", [f"- {r['category']} net {r['net']:+,.0f} crore on {r['date']}" for r in fii]
                  + [src_md("fii", f"fetched {sources.fetched(con, 'fii') or fii[0]['date']}")]))
    S.append(("Note", [DISCLAIMER]))
    return dict(symbol=sym, name=name, verdicts=v, short=ss, long=ls, sections=S,
                generated=datetime.now(IST).strftime("%d %b %Y %H:%M IST"))


def section_markdown(lines):
    """Join lines so plain sentences stay separate paragraphs and tables/lists render."""
    out = []
    for ln in lines:
        plain = ln.strip() and not ln.startswith(("- ", "|"))
        if plain or (ln.startswith("|") and out and not out[-1].startswith("|")):
            out.append("")
        out.append(ln)
        if plain:
            out.append("")
    return "\n".join(out)


def to_markdown(rep):
    out = [f"# {rep['symbol']} — {rep['name']}", f"_Report generated {rep['generated']}_", ""]
    for title, lines in rep["sections"]:
        out += [f"## {title}", section_markdown(lines), ""]
    return "\n".join(out)


def _inline(t):
    import re
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank">\1</a>', t)
    return re.sub(r"(?m)_Source: (.+?)_\s*$", r"<span class='src'>Source: \1</span>", t)


def _lines_html(lines):
    body, table, ul = [], [], False
    for ln in lines + [""]:
        if ln.startswith("|"):
            if set(ln.replace("|", "").strip()) <= set("-"):
                continue
            cells = [_inline(c.strip()) for c in ln.strip("|").split("|")]
            tag = "th" if not table else "td"
            table.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
            continue
        if table:
            body.append("<div class='tw'><table>" + "".join(table) + "</table></div>")
            table = []
        if ln.startswith("- "):
            if not ul:
                body.append("<ul>")
                ul = True
            body.append("<li>" + _inline(ln[2:]).replace("  \n", "<br>").replace("\n", "<br>") + "</li>")
            continue
        if ul:
            body.append("</ul>")
            ul = False
        if ln.strip():
            body.append(f"<p>{_inline(ln)}</p>")
    return "".join(body)


CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#5f6b7a;--line:#e3e6ea;--card:#f6f8fa;--good:#e7f6ec;--goodt:#13642f;
--bad:#fdecec;--badt:#9b1c1c;--neu:#eef3fb;--neut:#234a7a;--link:#0b63c4}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8eb;--muted:#9aa4b2;--line:#2a2f37;--card:#171a20;
--good:#12301d;--goodt:#7fd69b;--bad:#3a1515;--badt:#f19a9a;--neu:#16233a;--neut:#9cc0f2;--link:#6fb0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:900px;margin:auto;padding:16px}a{color:var(--link)}h1{font-size:1.6rem;margin:.2em 0}
h2{font-size:1.15rem;border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:26px}
.muted{color:var(--muted);font-size:.9rem}.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:8px 0;font-size:.92rem}
td,th{border:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}th{background:var(--card)}
.badge{display:block;padding:10px 12px;border-radius:8px;margin:6px 0;font-weight:600}
.good{background:var(--good);color:var(--goodt)}.bad{background:var(--bad);color:var(--badt)}.neutral{background:var(--neu);color:var(--neut)}
nav.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}nav.tabs a{padding:6px 12px;border:1px solid var(--line);border-radius:20px;text-decoration:none}
.pick{display:block;padding:12px 14px;border:1px solid var(--line);border-radius:10px;margin:8px 0;text-decoration:none;color:var(--fg);background:var(--card)}
.pick b{color:var(--link)}.pick small{display:block;color:var(--muted);margin-top:4px}
.src{display:inline-block;font-size:.8rem;color:var(--muted);background:var(--card);border-radius:6px;padding:1px 6px;margin-top:2px}
.report{border-top:3px solid var(--line);margin-top:40px;padding-top:8px}.top{font-size:.9rem}
"""


def _report_html(rep, back=None):
    b = [f"<h1>{html.escape(rep['symbol'])} — {html.escape(rep['name'])}</h1>",
         f"<p class='muted'>Report generated {rep['generated']} · short-term score {rep['short']:.0f} (−100…100) · "
         f"long-term score {rep['long']:.0f}/100</p>"]
    b += [f"<span class='badge {tone}'>{html.escape(t)}</span>" for t, tone in rep["verdicts"]]
    for i, (title, lines) in enumerate(rep["sections"]):
        b.append(f"<h2>{html.escape(title)}</h2>" + _lines_html(lines[3:] if i == 0 else lines))
    if back:
        b.append(f"<p class='top'><a href='{back}'>↑ Back to the list</a></p>")
    return "".join(b)


def _page(title, inner):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><main>{inner}</main></body></html>")


def to_html(rep):
    """One share's report as a self-contained HTML file."""
    return _page(f"{rep['symbol']} report", _report_html(rep))


def recommended_html(con, cfg, demo=False, top="", extra_syms=()):
    """One HTML file: today's recommended shares (short term, long term, avoid), each name
    linking to that share's full report further down the same file. Opens on any phone or computer."""
    short, long, avoid = recommended(con, cfg)
    now = datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    b = ["<h1 id='top'>📈 Recommended shares</h1>",
         f"<p class='muted'>Based on the news and data gathered up to {now}. Tap a share for its full report.</p>"]
    if demo:
        b.append("<span class='badge bad'>DEMO: everything in this file is made-up sample data, not real market data.</span>")
    b.append("<p class='muted'><b>Track record (from the tracker's own history):</b> "
             + html.escape(" · ".join(analysis.track_text(analysis.track_record(con)))) + "</p>")
    b.append(top)
    b.append("<nav class='tabs'><a href='#short'>Short term (%d)</a><a href='#long'>Long term (%d)</a>"
             "<a href='#avoid'>Avoid / careful (%d)</a></nav>" % (len(short), len(long), len(avoid)))
    syms = list(dict.fromkeys(extra_syms))
    for anchor, title, df, col, rc in (("short", "Short term (days to weeks)", short, "short_score", "short_reasons"),
                                       ("long", "Long term (months to years)", long, "long_score", "long_reasons"),
                                       ("avoid", "Avoid / be careful", avoid, "short_score", "short_reasons")):
        b.append(f"<h2 id='{anchor}'>{title}</h2>")
        if df.empty:
            b.append("<p class='muted'>No shares in this list today.</p>")
        for r in df.itertuples():
            reasons = getattr(r, rc) or r.long_reasons
            b.append(f"<a class='pick' href='#r-{html.escape(r.symbol)}'><b>{html.escape(r.symbol)}</b> · "
                     f"{html.escape(r.name or '')} — score {getattr(r, col):.0f}"
                     f"<small>{html.escape(' • '.join(sources.plain(x) for x in reasons[:2]))}</small></a>")
            if r.symbol not in syms:
                syms.append(r.symbol)
    for sym in syms:
        b.append(f"<section class='report' id='r-{html.escape(sym)}'>{_report_html(build(con, cfg, sym), '#top')}</section>")
    b.append(f"<p class='muted'>{html.escape(DISCLAIMER)}</p>")
    return _page("Recommended shares", "".join(b))


def write_recommended_html(con, cfg, path=None, demo=False):
    from pathlib import Path
    path = Path(path or Path(cfg["db_path"]).parent / "recommended.html")
    path.write_text(recommended_html(con, cfg, demo), encoding="utf-8")
    return str(path)


def to_text(rep):
    """Plain text for Telegram."""
    import re
    t = to_markdown(rep)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"_Source: \[(.+?)\]\((https?://[^)]+)\)(.*?)_", r"Source: \1\3 \2", t)
    t = re.sub(r"_Source: (.+?)_", r"Source: \1", t)
    t = re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r"\1 \2", t)
    t = re.sub(r"^#+ ", "", t, flags=re.M)
    t = re.sub(r"^\|[-|]+\|$\n", "", t, flags=re.M)
    t = re.sub(r"^_(.+)_$", r"\1", t.replace("  \n", "\n"), flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t)


def recommended(con, cfg):
    """Latest signals split into short-term buys, long-term buys and shares to avoid."""
    df = analysis.latest_signals(con)
    if df.empty:
        return df, df, df
    short = df[df.short_score >= 20].sort_values("short_score", ascending=False)
    long = df[df.long_score >= 60].sort_values("long_score", ascending=False)
    avoid = df[(df.short_score <= -25) | (df.long_score < 40)].sort_values("short_score")
    return short, long, avoid
