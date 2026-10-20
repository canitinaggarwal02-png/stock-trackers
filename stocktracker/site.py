"""The phone web page: a home screen with buttons (short term, long term, bought, sold,
watchlist, reminders), a one-line-per-share list behind each button, and the full
details of a share when its line is tapped. Details are drawn only when opened, so the
page stays light."""
import html
import json
import os
from datetime import datetime

import pandas as pd

from . import DISCLAIMER, analysis, portfolio, report, sources
from .prices import history

CHART_DAYS = 60
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


def _row(con, r, sym, name, score, score_txt, line, extra="", rank=None):
    last, chg = _price(con, sym)
    p = "" if last is None else f"₹{last:,.2f}" + ("" if chg is None else
                                                  f" <span class='{'up' if chg >= 0 else 'dn'}'>{chg:+.1f}%</span>")
    tone = ("bad" if score_txt.startswith("-") else
            "good" if score_txt.startswith("+") or (score is not None and score >= 60) else "neutral")
    return (f"<a class='row' href='#s/{_e(sym)}'><div class='r1'>"
            + ("" if rank is None else f"<span class='rk'>{rank}</span>") + f"<b>{_e(sym)}</b>"
            f"<span class='nm'>{_e(name)}</span><span class='sc {tone}'>{_e(score_txt)}</span></div>"
            f"<div class='r2'><span class='px'>{p}</span>{extra}</div><div class='why'>{_e(line)}</div></a>")


def build(con, cfg, steps=(), notes=()):
    allsig = analysis.latest_signals(con)
    _, _, avoid = report.recommended(con, cfg)
    pos = portfolio.active(con)
    rows, syms, picked = {k: [] for k, *_ in LISTS}, [], {}

    # Every analysed share, best first; the ones that pass the buy rule come before a divider.
    for key, df, col, rc, cut in (
            ("short", allsig.sort_values("short_score", ascending=False) if not allsig.empty else allsig,
             "short_score", "short_reasons", 20),
            ("long", allsig.sort_values("long_score", ascending=False) if not allsig.empty else allsig,
             "long_score", "long_reasons", 60),
            ("avoid", avoid, "short_score", "short_reasons", None)):
        n, divided = 0, False
        for i, r in enumerate(df.itertuples(), 1):
            reasons = getattr(r, rc) or r.long_reasons
            sc = getattr(r, col)
            if cut is not None and sc < cut and not divided:
                rows[key].append("<p class='div'>Lower priority: not a buy signal yet</p>")
                divided = True
            n += cut is None or sc >= cut
            rows[key].append(_row(con, r, r.symbol, r.name, sc,
                                  f"{sc:+.0f}" if col == "short_score" else f"{sc:.0f}/100",
                                  sources.plain(reasons[0]) if reasons else "", rank=i))
            syms.append(r.symbol)
        picked[key] = (n, len(df))

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
        picked[key] = (picked.get(key, (0, 0))[0] + 1,) * 2
        rows[key].append(_row(con, p, p.symbol, p.name, None,
                              "" if s is None else f"{s.short_score:+.0f}", line, extra))
        syms.append(p.symbol)

    details, charts = {}, {}
    for sym in dict.fromkeys(syms):
        details[sym] = detail_html(con, cfg, sym)
        charts[sym] = chart_data(con, sym)
    market = {r["date"]: [None if r["fii"] is None else round(r["fii"]), None if r["dii"] is None else round(r["dii"])] for r in con.execute(
        "SELECT date, SUM(CASE WHEN category LIKE 'FII%' THEN net END) fii, "
        "SUM(CASE WHEN category='DII' THEN net END) dii FROM fii_dii GROUP BY date")}

    al = con.execute("SELECT * FROM alerts WHERE created_at >= datetime('now','-7 days') ORDER BY id DESC LIMIT 40").fetchall()
    alerts_html = "".join(
        f"<details class='card'><summary><b>{_e(a['title'])}</b> <small>{_e(a['created_at'][:16].replace('T', ' '))}</small></summary><ul>"
        + "".join(f"<li>{sources.to_html(ln[2:] if ln.startswith('• ') else ln)}</li>"
                  for ln in (a['body'] or '').splitlines() if ln.strip()) + "</ul></details>" for a in al) \
        or "<p class='muted'>No reminders in the last 7 days.</p>"

    failed = [s["step"] for s in steps if not s.get("ok", True)]
    now = datetime.now(report.IST).strftime("%d %b %Y, %I:%M %p")
    status = f"Updated {now} IST" + (f" · could not update: {', '.join(failed)}" if failed else "")
    def count(k):
        n, total = picked.get(k, (0, 0))
        return f"{n}<small> of {total}</small>" if total > n else str(n)
    tiles = "".join(f"<a class='tile' href='#l/{k}'><span class='ic'>{ic}</span><span class='n'>{count(k)}</span>"
                    f"<span class='t'>{_e(t)}</span></a>" for k, t, ic, _ in LISTS)
    tiles += f"<a class='tile' href='#alerts'><span class='ic'>🔔</span><span class='n'>{len(al)}</span><span class='t'>Reminders</span></a>"
    lists = {k: dict(title=f"{ic} {t}", sub=sub, html="".join(rows[k]) or "<p class='muted'>Nothing here today.</p>")
             for k, t, ic, sub in LISTS}
    lists["alerts"] = dict(title="🔔 Reminders", sub="Last 7 days, newest first", html=alerts_html)
    track = " · ".join(analysis.track_text(analysis.track_record(con)))
    label = {"bought": "bought", "sold": "sold", "short": "short", "want_buy": "want to buy", "want_sell": "want to sell"}
    mine = [dict(share=p.symbol, status=label[p.kind], **{k: (None if pd.isna(getattr(p, c)) else float(getattr(p, c)))
                                                          for k, c in (("qty", "qty"), ("price", "price"), ("target", "target"),
                                                                       ("stop_loss", "stop_loss"))}, note=p.note or "")
            for p in pos.itertuples()]
    names = [[r[0], r[1] or ""] for r in con.execute("SELECT symbol, name FROM symbols ORDER BY symbol")]
    data = json.dumps(dict(lists=lists, details=details, charts=charts, market=market, mine=mine, names=names,
                           repo=os.environ.get("GITHUB_REPOSITORY", ""), built=int(datetime.now().timestamp())), ensure_ascii=False).replace("</", "<\\/")
    notes_html = "".join(f"<p class='note'>{_e(n)}</p>" for n in notes)
    return PAGE.format(editjs=EDIT_JS, css=report.CSS + CSS, status=_e(status), tiles=tiles, notes=notes_html,
                       track=_e(track), disclaimer=_e(DISCLAIMER), data=data)


def detail_html(con, cfg, sym):
    rep = report.build(con, cfg, sym)
    last, chg = _price(con, sym)
    chart = (f"<div class='card'><b>Daily chart (last {CHART_DAYS} trading days)</b><div class='chart' data-s='{_e(sym)}'></div>"
             "<p class='muted small'>Tap the chart to see one day's numbers; tap a name below it to hide or show that line. "
             "Delivery and big-player deals: NSE daily reports. FII/DII: NSE daily totals for the whole market "
             "(₹ crore); their holding in this share is published quarterly, see Shareholding below.</p></div>")
    head = [f"<h2>{_e(rep['symbol'])}</h2><p class='muted'>{_e(rep['name'])}"
            + ("" if last is None else f" · <b>₹{last:,.2f}</b>"
               + ("" if chg is None else f" <span class='{'up' if chg >= 0 else 'dn'}'>{chg:+.1f}%</span>"))
            + f"<br>Short-term score {rep['short']:+.0f} · long-term score {rep['long']:.0f}/100</p>"]
    head += [f"<span class='badge {tone}'>{_e(t)}</span>" for t, tone in rep["verdicts"]]
    head.append(chart)
    body = []
    for i, (title, lines) in enumerate(rep["sections"]):
        if title == "Note":
            continue
        inner = report._lines_html(lines[3:] if i == 0 else lines)
        body.append(f"<details class='card'{' open' if i == 0 else ''}><summary>{_e(title)}</summary>{inner}</details>")
    return "".join(head + body)


def chart_data(con, sym):
    """Last CHART_DAYS trading days: close price, traded and delivered quantity, big-player buys and sells."""
    px = history(con, sym, CHART_DAYS)
    if px.empty:
        return None
    days = [d.strftime("%Y-%m-%d") for d in px.index]
    dl = {r["date"]: (r["traded_qty"], r["delivery_qty"]) for r in con.execute(
        "SELECT date, traded_qty, delivery_qty FROM delivery WHERE symbol=? AND date>=?", (sym, days[0]))}
    deals = {}
    for r in con.execute("SELECT date, client, side, qty FROM deals WHERE symbol=? AND date>=?", (sym, days[0])):
        day = next((d for d in days if d >= r["date"][:10]), days[-1])  # weekend deals go on the next trading day
        b = deals.setdefault(day, [0, 0, []])
        b[0 if r["side"].upper().startswith("B") else 1] += r["qty"] or 0
        b[2].append(f"{r['client']} {'bought' if r['side'].upper().startswith('B') else 'sold'} {r['qty']:,.0f}")
    return dict(d=days, p=[round(float(x), 2) for x in px.close],
                t=[dl.get(d, (None, None))[0] for d in days], v=[dl.get(d, (None, None))[1] for d in days],
                b=[deals.get(d, [0])[0] or None for d in days],
                s=[deals.get(d, [0, 0])[1] or None for d in days],
                n=["; ".join(deals[d][2]) if d in deals else "" for d in days])


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
.rk{color:var(--muted);font-size:.8rem;min-width:1.4em}.tile .n small{font-size:.8rem;font-weight:400;color:var(--muted)}
.div{margin:16px 0 4px;color:var(--muted);font-weight:600;border-top:1px dashed var(--line);padding-top:10px}
.chart svg{width:100%;height:auto;display:block;margin-top:6px}.chart text{font-size:9px;fill:var(--muted)}
.lg{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:.8rem;margin-top:4px}.lg span{cursor:pointer;white-space:nowrap}
.lg span.off{opacity:.35;text-decoration:line-through}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
.tip{font-size:.82rem;background:var(--card);border-radius:8px;padding:6px 8px;margin-top:6px}
.btn{display:block;text-align:center;padding:11px;border-radius:12px;background:var(--link);color:#fff;text-decoration:none;font-weight:600;margin:8px 0;border:0;font-size:1rem;width:100%;cursor:pointer}
.btn.light{background:var(--bg);color:var(--link);border:1px solid var(--line)}
.ed{display:flex;align-items:center;gap:8px;padding:10px 12px;margin:8px 0;border-radius:12px;background:var(--bg);border:1px solid var(--line)}
.ed div{flex:1;min-width:0}.ed button{border:0;background:var(--bad);color:var(--badt);border-radius:8px;padding:8px 10px;font-size:.9rem;cursor:pointer}
.fm{display:grid;grid-template-columns:1fr 1fr;gap:8px}.fm label{display:flex;flex-direction:column;font-size:.8rem;color:var(--muted)}
.fm .w{grid-column:1/3}.fm input,.fm select{box-sizing:border-box;width:100%;min-width:0;font-size:1rem;padding:9px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
#msg{margin:8px 0;font-weight:600}
.hidden{display:none}h2{border:0;margin:8px 0 2px}
"""

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex'>
<title>Stock Tracker</title><style>{css}</style></head><body><main>
<div class='bar'><button class='back hidden' id='back' onclick='history.back()'>‹</button><h1 id='title'>📈 Stock Tracker</h1></div>
<div id='home'><p class='muted'>{status}</p>{notes}<div class='tiles'>{tiles}</div>
<p class='muted small'><b>Track record:</b> {track}</p><p><a href='#' onclick='savePage();return false'>⬇ Save this page as an HTML file</a></p></div>
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
  else if(h.startsWith('edit/')&&EDIT[h.slice(5)]){{ title='✎ '+EDIT[h.slice(5)].t; html=editView(h.slice(5)); }}
  if(html!==null&&['l/bought','l/sold','l/watch'].includes(h)) html=`<a class='btn' href='#edit/${{h.slice(2)}}'>＋ Add or 🗑 remove shares</a>`+pendingNote()+html;
  $('home').classList.toggle('hidden',html!==null); $('view').classList.toggle('hidden',html===null);
  $('back').classList.toggle('hidden',html===null); $('title').textContent=title;
  if(html!==null){{ $('view').innerHTML=html; document.querySelectorAll('.chart').forEach(drawChart); if(h.startsWith('edit/')) bindEdit(h.slice(5)); }} window.scrollTo(0,0);
}}
const SER=[['p','Price ₹','#2563eb'],['t','Traded qty','#cbd5e1'],['v','Delivery qty','#64748b'],
  ['b','Big players bought','#16a34a'],['s','Big players sold','#dc2626'],['f','FII net ₹cr','#f59e0b'],['i','DII net ₹cr','#9333ea']];
const OFF={{}};
const fmt=x=>x==null?'–':Math.abs(x)>=1e7?(x/1e7).toFixed(2)+' cr':Math.abs(x)>=1e5?(x/1e5).toFixed(2)+' L':Math.round(x).toLocaleString('en-IN');
function drawChart(el){{
  const c=D.charts[el.dataset.s]; if(!c){{ el.innerHTML="<p class='muted'>No price history yet.</p>"; return; }}
  const n=c.d.length, W=400, L=34, R=6, X=i=>L+(i+.5)*(W-L-R)/n, bw=Math.max(1,(W-L-R)/n*.7);
  const f=c.d.map(d=>(D.market[d]||[])[0]??null), ii=c.d.map(d=>(D.market[d]||[])[1]??null), on=k=>!OFF[k];
  const S={{p:c.p,t:c.t,v:c.v,b:c.b,s:c.s,f:f,i:ii}};
  let g='';
  const band=(y0,h,label)=>g+=`<rect x='${{L}}' y='${{y0}}' width='${{W-L-R}}' height='${{h}}' fill='none' stroke='currentColor' stroke-opacity='.12'/><text x='2' y='${{y0+9}}'>${{label}}</text>`;
  const ax=(y,v)=>g+=`<text x='2' y='${{y+3}}'>${{v}}</text>`;
  // price strip
  const P=c.p, lo=Math.min(...P), hi=Math.max(...P), py=v=>12+100*(1-(v-lo)/((hi-lo)||1));
  band(8,108,''); ax(12,Math.round(hi)); ax(112,Math.round(lo));
  if(on('p')) g+=`<polyline fill='none' stroke='#2563eb' stroke-width='1.6' points='${{P.map((v,i)=>X(i)+','+py(v)).join(' ')}}'/>`;
  // quantity strip: traded & delivery bars up, big-player buys up, sells down
  const qs=[...(on('t')?c.t:[]),...(on('v')?c.v:[]),...(on('b')?c.b:[]),...(on('s')?c.s:[])].filter(x=>x), qm=Math.max(1,...qs);
  const Q0=200, qh=v=>70*v/qm; band(122,110,''); ax(130,fmt(qm)); g+=`<line x1='${{L}}' x2='${{W-R}}' y1='${{Q0}}' y2='${{Q0}}' stroke='currentColor' stroke-opacity='.25'/>`;
  c.d.forEach((_,i)=>{{
    if(on('t')&&c.t[i]) g+=`<rect x='${{X(i)-bw/2}}' y='${{Q0-qh(c.t[i])}}' width='${{bw}}' height='${{qh(c.t[i])}}' fill='#cbd5e1'/>`;
    if(on('v')&&c.v[i]) g+=`<rect x='${{X(i)-bw/2}}' y='${{Q0-qh(c.v[i])}}' width='${{bw}}' height='${{qh(c.v[i])}}' fill='#64748b'/>`;
    if(on('b')&&c.b[i]) g+=`<rect x='${{X(i)-bw/2}}' y='${{Q0-qh(c.b[i])}}' width='${{bw}}' height='${{qh(c.b[i])}}' fill='#16a34a' fill-opacity='.85'/>`;
    if(on('s')&&c.s[i]) g+=`<rect x='${{X(i)-bw/2}}' y='${{Q0}}' width='${{bw}}' height='${{Math.min(30,qh(c.s[i]))}}' fill='#dc2626' fill-opacity='.85'/>`;
  }});
  // FII / DII strip (market-wide, ₹ crore)
  const fm=Math.max(1,...[...(on('f')?f:[]),...(on('i')?ii:[])].filter(x=>x!=null).map(Math.abs)), F0=270, fy=v=>F0-30*v/fm;
  band(238,64,''); ax(244,'+'+Math.round(fm)); ax(298,'−'+Math.round(fm));
  g+=`<line x1='${{L}}' x2='${{W-R}}' y1='${{F0}}' y2='${{F0}}' stroke='currentColor' stroke-opacity='.25'/>`;
  [['f',f,'#f59e0b',-1],['i',ii,'#9333ea',1]].forEach(([k,a,col,o])=>{{ if(on(k)) a.forEach((v,i)=>{{ if(v!=null)
    g+=`<rect x='${{X(i)+o*bw/4-bw/4}}' y='${{Math.min(F0,fy(v))}}' width='${{bw/2}}' height='${{Math.abs(fy(v)-F0)}}' fill='${{col}}'/>`; }}); }});
  [0,Math.floor(n/2),n-1].forEach(i=>g+=`<text x='${{X(i)}}' y='314' text-anchor='middle'>${{c.d[i].slice(5)}}</text>`);
  g+=`<line id='cur' x1='-9' x2='-9' y1='8' y2='302' stroke='currentColor' stroke-opacity='.5'/>`;
  el.innerHTML=`<svg viewBox='0 0 ${{W}} 318'>${{g}}</svg><div class='lg'>`+SER.map(([k,t,col])=>`<span data-k='${{k}}' class='${{OFF[k]?'off':''}}'><i style='background:${{col}}'></i>${{t}}</span>`).join('')+`</div><div class='tip'>Tap a day on the chart</div>`;
  el.querySelectorAll('.lg span').forEach(s=>s.onclick=()=>{{ OFF[s.dataset.k]=!OFF[s.dataset.k]; drawChart(el); }});
  const svg=el.querySelector('svg');
  svg.onclick=e=>{{ const r=svg.getBoundingClientRect(), x=(e.clientX-r.left)*W/r.width, i=Math.max(0,Math.min(n-1,Math.floor((x-L)/((W-L-R)/n))));
    svg.querySelector('#cur').setAttribute('x1',X(i)); svg.querySelector('#cur').setAttribute('x2',X(i));
    el.querySelector('.tip').innerHTML=`<b>${{c.d[i]}}</b> · price ₹${{c.p[i]}}<br>Traded ${{fmt(c.t[i])}} · delivered ${{fmt(c.v[i])}}`+(c.t[i]&&c.v[i]?` (${{Math.round(100*c.v[i]/c.t[i])}}%)`:'')
      +`<br>Big players: bought ${{fmt(c.b[i])}} · sold ${{fmt(c.s[i])}}`+(c.n[i]?`<br><small>${{c.n[i]}}</small>`:'')
      +`<br>Market FII ₹${{f[i]??'–'}} cr · DII ₹${{ii[i]??'–'}} cr`; }};
}}
function savePage(){{
  const a=document.createElement('a'), d=new Date().toISOString().slice(0,10);
  a.href=URL.createObjectURL(new Blob(['<!doctype html>'+document.documentElement.outerHTML],{{type:'text/html'}}));
  a.download='stock-tracker-'+d+'.html'; document.body.appendChild(a); a.click(); a.remove();
}}
{editjs}
addEventListener('hashchange',show); show();
</script></body></html>"""

# Editing your shares from the page: the list is encrypted in the browser with your page password,
# saved to my_shares.enc in your GitHub repository, and an update is started straight away.
EDIT_JS = r"""
const EDIT={bought:{t:'Bought',k:['bought']},sold:{t:'Sold',k:['sold','short']},watch:{t:'Watchlist',k:['want to buy','want to sell']}};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ls=(k,v)=>{try{if(v===undefined)return localStorage.getItem(k);if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,v)}catch(e){return null}};
function mine(){const m=JSON.parse(ls('mine')||'null');return m&&m.t>D.built*1000?m.rows:D.mine;}
function pendingNote(){const m=JSON.parse(ls('mine')||'null');return m&&m.t>D.built*1000?
  "<p class='note'>Your last change is saved. This list updates when the next update finishes, in about 5 minutes.</p>":"";}
function editView(key){
  const E=EDIT[key], rows=mine().map((r,i)=>[r,i]).filter(([r])=>E.k.includes(r.status));
  let h=`<div id='msg'></div>`;
  h+=rows.map(([r,i])=>`<div class='ed'><div><b>${esc(r.share)}</b> <small class='muted'>${esc(r.status)}</small><br><small class='muted'>`+
    [r.qty?r.qty+' shares':'',r.price?'@ ₹'+r.price:'',r.target?'target ₹'+r.target:'',r.stop_loss?'stop ₹'+r.stop_loss:'',esc(r.note)].filter(x=>x).join(' · ')+
    `</small></div><button data-i='${i}'>🗑 Remove</button></div>`).join('')||"<p class='muted'>No shares here yet.</p>";
  h+=`<details class='card' open><summary>＋ Add a share</summary><form class='fm' id='add'>
    <label class='w'>Share (NSE symbol or name)<input name='share' list='syms' required autocomplete='off'></label>
    <label class='w'>Status<select name='status'>${E.k.map(k=>`<option>${k}</option>`).join('')}</select></label>
    <label>Quantity<input name='qty' inputmode='decimal'></label><label>Price ₹<input name='price' inputmode='decimal'></label>
    <label>Target ₹<input name='target' inputmode='decimal'></label><label>Stop loss ₹<input name='stop_loss' inputmode='decimal'></label>
    <label class='w'>Note / reason<input name='note'></label><button class='btn w'>Save</button></form></details>
    <datalist id='syms'>${D.names.map(([s,n])=>`<option value='${esc(s)}'>${esc(n)}</option>`).join('')}</datalist>`;
  h+=`<details class='card' ${ls('ghtok')?'':'open'}><summary>🔑 GitHub key ${ls('ghtok')?'(saved on this phone)':'(needed once)'}</summary>
    <p class='small'>Changes are saved to your GitHub account, so this phone needs a key once:</p><ol class='small'>
    <li>Open <a href='https://github.com/settings/personal-access-tokens/new' target='_blank'>github.com → new fine-grained token</a> (signed in).</li>
    <li>Name it "stock tracker", set Expiration to the longest allowed.</li>
    <li>Repository access: <b>Only select repositories</b> → <b>${esc(D.repo.split('/')[1]||'your repository')}</b>.</li>
    <li>Repository permissions: <b>Contents</b> = Read and write, <b>Actions</b> = Read and write.</li>
    <li>Tap Generate token, copy it, paste below.</li></ol>
    <input id='tok' type='password' placeholder='github_pat_…' style='width:100%;padding:9px;font-size:1rem;box-sizing:border-box'>
    <button class='btn light' id='savetok'>Save key on this phone</button></details>`;
  return h;
}
function bindEdit(key){
  $('savetok').onclick=()=>{const t=$('tok').value.trim(); if(t){ls('ghtok',t); show();}};
  $('add').onsubmit=e=>{e.preventDefault(); const f=Object.fromEntries(new FormData(e.target)); const r={share:f.share.trim().toUpperCase(),status:f.status,note:f.note.trim()};
    ['qty','price','target','stop_loss'].forEach(k=>r[k]=f[k].trim()?parseFloat(f[k].replace(/[,₹]/g,'')):null);
    if(!r.share) return; save(mine().filter(x=>!(x.share===r.share&&x.status===r.status)).concat([r]), key, `${r.share} added`);};
  document.querySelectorAll('.ed button').forEach(b=>b.onclick=()=>{const m=mine(), r=m[+b.dataset.i];
    if(confirm(`Remove ${r.share} (${r.status})?`)) save(m.filter((_,i)=>i!=b.dataset.i), key, `${r.share} removed`);});
}
async function encrypt(text,pw){
  const salt=crypto.getRandomValues(new Uint8Array(16)), iv=crypto.getRandomValues(new Uint8Array(12));
  const k0=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);
  const k=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:250000,hash:'SHA-256'},k0,{name:'AES-GCM',length:256},false,['encrypt']);
  const d=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv},k,new TextEncoder().encode(text)));
  const b=a=>btoa(String.fromCharCode(...a)); return JSON.stringify({s:b(salt),i:b(iv),d:b(d)});
}
async function save(rows,key,what){
  const say=(t,bad)=>{$('msg').textContent=t;$('msg').style.color=bad?'var(--badt)':'var(--goodt)';window.scrollTo(0,0);};
  const tok=ls('ghtok'); if(!tok) return say('Add your GitHub key first (see 🔑 below).',1);
  if(!D.repo) return say('This page copy cannot save changes. Open your website instead.',1);
  let pw=null; try{pw=sessionStorage.getItem('stpw')||localStorage.getItem('stpw')}catch(e){}
  if(!pw) pw=prompt('Enter your page password to save'); if(!pw) return;
  say('Saving…');
  const cell=v=>{v=String(v??'');return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
  const csv='share,status,qty,price,target,stop_loss,note\n'+rows.map(r=>['share','status','qty','price','target','stop_loss','note'].map(k=>cell(r[k])).join(',')).join('\n');
  const H={Authorization:'Bearer '+tok,Accept:'application/vnd.github+json'}, api=`https://api.github.com/repos/${D.repo}`;
  try{
    const cur=await fetch(`${api}/contents/my_shares.enc?ref=main`,{headers:H,cache:'no-store'});
    if(cur.status===401||cur.status===403) throw new Error('GitHub did not accept the key. Check its permissions (Contents and Actions: Read and write).');
    const sha=cur.ok?(await cur.json()).sha:undefined;
    const put=await fetch(`${api}/contents/my_shares.enc`,{method:'PUT',headers:H,body:JSON.stringify({message:'Update my shares (encrypted)',
      content:btoa(await encrypt(csv,pw)),sha,branch:'main'})});
    if(!put.ok) throw new Error('Saving failed ('+put.status+'). Check the key permissions.');
    ls('mine',JSON.stringify({t:Date.now(),rows}));
    const run=await fetch(`${api}/actions/workflows/daily.yml/dispatches`,{method:'POST',headers:H,body:JSON.stringify({ref:'main'})});
    show(); say(`✓ ${what}. `+(run.ok?'The update has started; new numbers show in about 5 minutes.':'It will show after the next scheduled update.'));
  }catch(e){ say(e.message||'Could not reach GitHub. Check your internet and try again.',1); }
}
"""
