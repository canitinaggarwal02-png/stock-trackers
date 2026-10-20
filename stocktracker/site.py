"""The phone web page: a home screen with buttons (short term, long term, bought, sold,
watchlist, reminders), a one-line-per-share list behind each button, and the full
details of a share when its line is tapped. Details are drawn only when opened, so the
page stays light."""
import html
import json
from datetime import datetime

import pandas as pd

from . import DISCLAIMER, analysis, portfolio, report, sources
from .prices import history

LIST_LIMIT = 25
LISTS = [  # key, button title, icon, what the list shows
    ("short", "Short term", "⚡", "Shares likely to gain in the coming days or weeks"),
    ("long", "Long term", "🌱", "Strong shares to hold for months or years"),
    ("bought", "Bought", "💼", "Shares you hold"),
    ("sold", "Sold", "📤", "Shares you sold or short-sold"),
    ("watch", "Watchlist", "👀", "Shares you want to buy or sell"),
    ("avoid", "Be careful", "⚠️", "Weak signals or bad news: avoid for now"),
]


def _e(x):
    return html.escape(str(x if x is not None else ""))


def _price(con, sym):
    px = history(con, sym, 2)
    if px.empty:
        return None, None
    last = float(px.close.iloc[-1])
    prev = float(px.close.iloc[-2]) if len(px) > 1 else last
    return last, (last / prev - 1) * 100 if prev else None


def _row(con, r, sym, name, score, score_txt, line, extra=""):
    last, chg = _price(con, sym)
    p = "" if last is None else f"₹{last:,.2f}" + ("" if chg is None else
                                                  f" <span class='{'up' if chg >= 0 else 'dn'}'>{chg:+.1f}%</span>")
    tone = ("bad" if score_txt.startswith("-") else
            "good" if score_txt.startswith("+") or (score is not None and score >= 60) else "neutral")
    return (f"<a class='row' href='#s/{_e(sym)}'><div class='r1'><b>{_e(sym)}</b>"
            f"<span class='nm'>{_e(name)}</span><span class='sc {tone}'>{_e(score_txt)}</span></div>"
            f"<div class='r2'><span class='px'>{p}</span>{extra}</div><div class='why'>{_e(line)}</div></a>")


def build(con, cfg, steps=(), notes=()):
    short, long, avoid = report.recommended(con, cfg)
    short, long, avoid = short.head(LIST_LIMIT), long.head(LIST_LIMIT), avoid.head(LIST_LIMIT)
    pos = portfolio.active(con)
    rows, syms = {k: [] for k, *_ in LISTS}, []

    for key, df, col, rc in (("short", short, "short_score", "short_reasons"),
                             ("long", long, "long_score", "long_reasons"),
                             ("avoid", avoid, "short_score", "short_reasons")):
        for r in df.itertuples():
            reasons = getattr(r, rc) or r.long_reasons
            sc = getattr(r, col)
            rows[key].append(_row(con, r, r.symbol, r.name, sc,
                                  f"{sc:+.0f}" if col == "short_score" else f"{sc:.0f}/100",
                                  sources.plain(reasons[0]) if reasons else ""))
            syms.append(r.symbol)

    sig = analysis.latest_signals(con).set_index("symbol") if not pos.empty else pd.DataFrame()
    for p in pos.itertuples():
        key = {"bought": "bought", "sold": "sold", "short": "sold"}.get(p.kind, "watch")
        price = p.price if pd.notna(p.price) else None
        target = p.target if pd.notna(p.target) else None
        last, _ = _price(con, p.symbol)
        if p.kind in ("bought", "short") and price and last:
            pnl = (last / price - 1) * 100 * (-1 if p.kind == "short" else 1)
            extra = f"<span class='{'up' if pnl >= 0 else 'dn'}'>P&amp;L {pnl:+.1f}%</span>"
        elif target:
            extra = f"<span>target ₹{target:,.2f}</span>"
        else:
            extra = ""
        if pd.notna(p.qty) and p.qty:
            extra = f"<span>{p.qty:g} shares</span>" + extra
        a = con.execute("SELECT action FROM alerts WHERE symbol=? ORDER BY id DESC LIMIT 1", (p.symbol,)).fetchone()
        s = sig.loc[p.symbol] if p.symbol in sig.index else None
        line = (f"Reminder: {a['action']}" if a else
                report.verdicts(s.short_score, s.long_score)[0][0] if s is not None else portfolio.KINDS[p.kind])
        rows[key].append(_row(con, p, p.symbol, p.name, None,
                              "" if s is None else f"{s.short_score:+.0f}", line, extra))
        syms.append(p.symbol)

    details = {}
    for sym in dict.fromkeys(syms):
        details[sym] = detail_html(con, cfg, sym)

    al = con.execute("SELECT * FROM alerts WHERE created_at >= datetime('now','-7 days') ORDER BY id DESC LIMIT 40").fetchall()
    alerts_html = "".join(
        f"<details class='card'><summary><b>{_e(a['title'])}</b> <small>{_e(a['created_at'][:16].replace('T', ' '))}</small></summary><ul>"
        + "".join(f"<li>{sources.to_html(ln[2:] if ln.startswith('• ') else ln)}</li>"
                  for ln in (a['body'] or '').splitlines() if ln.strip()) + "</ul></details>" for a in al) \
        or "<p class='muted'>No reminders in the last 7 days.</p>"

    failed = [s["step"] for s in steps if not s.get("ok", True)]
    now = datetime.now(report.IST).strftime("%d %b %Y, %I:%M %p")
    status = f"Updated {now} IST" + (f" · could not update: {', '.join(failed)}" if failed else "")
    tiles = "".join(f"<a class='tile' href='#l/{k}'><span class='ic'>{ic}</span><span class='n'>{len(rows[k])}</span>"
                    f"<span class='t'>{_e(t)}</span></a>" for k, t, ic, _ in LISTS)
    tiles += f"<a class='tile' href='#alerts'><span class='ic'>🔔</span><span class='n'>{len(al)}</span><span class='t'>Reminders</span></a>"
    lists = {k: dict(title=f"{ic} {t}", sub=sub, html="".join(rows[k]) or "<p class='muted'>Nothing here today.</p>")
             for k, t, ic, sub in LISTS}
    lists["alerts"] = dict(title="🔔 Reminders", sub="Last 7 days, newest first", html=alerts_html)
    track = " · ".join(analysis.track_text(analysis.track_record(con)))
    data = json.dumps(dict(lists=lists, details=details), ensure_ascii=False).replace("</", "<\\/")
    notes_html = "".join(f"<p class='note'>{_e(n)}</p>" for n in notes)
    return PAGE.format(css=report.CSS + CSS, status=_e(status), tiles=tiles, notes=notes_html,
                       track=_e(track), disclaimer=_e(DISCLAIMER), data=data)


def detail_html(con, cfg, sym):
    rep = report.build(con, cfg, sym)
    last, chg = _price(con, sym)
    head = [f"<h2>{_e(rep['symbol'])}</h2><p class='muted'>{_e(rep['name'])}"
            + ("" if last is None else f" · <b>₹{last:,.2f}</b>"
               + ("" if chg is None else f" <span class='{'up' if chg >= 0 else 'dn'}'>{chg:+.1f}%</span>"))
            + f"<br>Short-term score {rep['short']:+.0f} · long-term score {rep['long']:.0f}/100</p>"]
    head += [f"<span class='badge {tone}'>{_e(t)}</span>" for t, tone in rep["verdicts"]]
    body = []
    for i, (title, lines) in enumerate(rep["sections"]):
        if title == "Note":
            continue
        inner = report._lines_html(lines[3:] if i == 0 else lines)
        body.append(f"<details class='card'{' open' if i == 0 else ''}><summary>{_e(title)}</summary>{inner}</details>")
    return "".join(head + body)


CSS = """
body{background:var(--card)}main{padding:12px 14px 40px}
.bar{display:flex;align-items:center;gap:10px}.bar h1{font-size:1.35rem;margin:6px 0;flex:1}
.back{border:0;background:none;font-size:1.6rem;color:var(--link);padding:0 6px;cursor:pointer}
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:12px 0}
.tile{display:flex;flex-direction:column;gap:2px;padding:14px;border-radius:14px;background:var(--bg);border:1px solid var(--line);
text-decoration:none;color:var(--fg)}.tile .ic{font-size:1.4rem}.tile .n{font-size:1.6rem;font-weight:700}.tile .t{color:var(--muted)}
.row{display:block;padding:12px;margin:8px 0;border-radius:12px;background:var(--bg);border:1px solid var(--line);text-decoration:none;color:var(--fg)}
.r1{display:flex;align-items:baseline;gap:8px}.r1 .nm{flex:1;color:var(--muted);font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc{font-weight:700;padding:1px 8px;border-radius:10px;font-size:.85rem}
.r2{display:flex;gap:8px;font-size:.88rem;margin-top:4px;align-items:baseline}.r2 .px{white-space:nowrap}
.why{color:var(--muted);font-size:.85rem;margin-top:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.up{color:var(--goodt)}.dn{color:var(--badt)}
.card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:10px 0}
.card summary{font-weight:600;cursor:pointer}.card[open] summary{margin-bottom:6px}
.note{background:var(--neu);color:var(--neut);padding:8px 10px;border-radius:8px}
.hidden{display:none}h2{border:0;margin:8px 0 2px}
"""

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex'>
<title>Stock Tracker</title><style>{css}</style></head><body><main>
<div class='bar'><button class='back hidden' id='back' onclick='history.back()'>‹</button><h1 id='title'>📈 Stock Tracker</h1></div>
<div id='home'><p class='muted'>{status}</p>{notes}<div class='tiles'>{tiles}</div>
<p class='muted small'><b>Track record:</b> {track}</p></div>
<div id='view' class='hidden'></div>
<p class='muted small'>{disclaimer}</p></main>
<script>
const D={data};
const $=id=>document.getElementById(id);
function show(){{
  const h=decodeURIComponent(location.hash.slice(1)); let html=null, title='📈 Stock Tracker';
  if(h.startsWith('l/')&&D.lists[h.slice(2)]){{ const l=D.lists[h.slice(2)]; title=l.title; html=`<p class='muted'>${{l.sub}}</p>`+l.html; }}
  else if(h==='alerts'){{ const l=D.lists.alerts; title=l.title; html=`<p class='muted'>${{l.sub}}</p>`+l.html; }}
  else if(h.startsWith('s/')&&D.details[h.slice(2)]){{ title='Share details'; html=D.details[h.slice(2)]; }}
  $('home').classList.toggle('hidden',html!==null); $('view').classList.toggle('hidden',html===null);
  $('back').classList.toggle('hidden',html===null); $('title').textContent=title;
  if(html!==null) $('view').innerHTML=html; window.scrollTo(0,0);
}}
addEventListener('hashchange',show); show();
</script></body></html>"""
