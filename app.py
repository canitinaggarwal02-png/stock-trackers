"""Dashboard: python run.py dashboard  (or: streamlit run app.py)"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent))
from stocktracker import DISCLAIMER, alerts, analysis, db, pipeline, portfolio, report, sources  # noqa: E402
from stocktracker.config import load_config  # noqa: E402
from stocktracker.nlp import HORIZON_TEXT  # noqa: E402
from stocktracker.prices import history  # noqa: E402

st.set_page_config(page_title="Indian Stock News & Impact Tracker", page_icon="📈", layout="wide")
cfg = load_config()
if os.environ.get("STOCKTRACKER_DB"):
    cfg["db_path"] = os.environ["STOCKTRACKER_DB"]


def con():
    return db.connect(cfg["db_path"])


pw = (cfg.get("dashboard") or {}).get("password")
if pw and not st.session_state.get("authed"):
    st.title("📈 Stock Tracker")
    entered = st.text_input("Password", type="password")
    if entered == pw:
        st.session_state["authed"] = True
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()

# Phone screens: tighter padding, smaller headings, full-width buttons.
st.markdown("""<style>
@media (max-width: 640px) {
  .block-container {padding: 1rem 0.6rem 3rem 0.6rem !important;}
  h1 {font-size: 1.6rem !important;} h2, h3 {font-size: 1.15rem !important;}
  [data-testid="stMetricValue"] {font-size: 1.3rem !important;}
  .stButton button, .stFormSubmitButton button {width: 100%;}
}
</style>""", unsafe_allow_html=True)

c = con()
demo = db.get_meta(c, "demo_mode") == "1"


@st.cache_resource
def start_auto_refresh(db_path):
    """One background checker per dashboard process: runs the daily refresh (and the
    hourly news check on market days) by itself while the dashboard is running."""
    import threading
    t = threading.Thread(target=pipeline.auto_loop, args=(cfg,), daemon=True, name="auto-refresh")
    t.start()
    return t


if cfg["schedule"].get("auto_in_dashboard", True):
    start_auto_refresh(cfg["db_path"])

st.sidebar.title("📈 Stock Tracker")
if demo:
    st.sidebar.warning("DEMO data: everything shown is made up.")
if pipeline.is_running():
    st.sidebar.info("↻ Refreshing data in the background…")
    if st.sidebar.button("Check if finished", use_container_width=True):
        st.rerun()
elif st.sidebar.button("↻ Refresh now", type="primary", use_container_width=True, disabled=demo,
                       help="Collect the latest news and market data, analyse it and update recommendations."):
    pipeline.run_in_background(cfg, "full")
    st.rerun()
_lf = db.get_meta(c, "last_full_update")
if _lf:
    st.sidebar.caption(f"Last refresh: {json.loads(_lf)['at'].replace('T', ' ')[:16]} · "
                       f"daily at {cfg['schedule']['full_update']}")
if "goto" in st.session_state:          # set by a row click; must be applied before the radio is drawn
    st.session_state["page"] = st.session_state.pop("goto")
PAGES = ["Ideas & market", "Share details", "Recommended shares", "My shares & watchlist", "Reminders", "Data & updates"]
page = st.sidebar.radio("Go to", PAGES, key="page")
st.sidebar.caption(DISCLAIMER)

STATUS_ICON = {"not_traded": "⏳ still to come", "pending": "⏳ not yet in price", "partly": "◐ partly in price",
               "priced_in": "✅ already in price", "opposite": "↔ price moved opposite", "neutral": "– neutral",
               "no_price": "? no price data", None: "?"}


def fmt_qty(x):
    if x is None or pd.isna(x):
        return "–"
    x = float(x)
    return f"{x/1e7:.2f} Cr" if x >= 1e7 else f"{x/1e5:.2f} L" if x >= 1e5 else f"{x:,.0f}"


def alert_md(body):
    return "\n".join("- " + sources.md(ln[2:] if ln.startswith("• ") else ln) for ln in (body or "").splitlines() if ln.strip())


def open_share(sym):
    st.session_state["share"] = sym
    st.session_state["goto"] = "Share details"


def news_df(where="", params=(), limit=200):
    return pd.read_sql_query(f"""SELECT n.published, ns.symbol, n.title, n.source, ns.event_type, ns.sentiment,
        ns.horizon, ns.impact_score, ns.reaction_status, ns.reaction_pct, ns.explanation, n.link, ns.move_5d
        FROM news_symbols ns JOIN news n ON n.id=ns.news_id {where} ORDER BY n.published DESC LIMIT {limit}""",
                             c, params=params)


def pick_table(df, score_col, reasons_col, key):
    if df.empty:
        st.info("No shares pass the bar today.")
        return
    show = pd.DataFrame({"Share": df.symbol, "Score": df[score_col],
                         "Why": df[reasons_col].apply(lambda r: " • ".join(sources.plain(x) for x in r[:3]))})
    ev = st.dataframe(show, hide_index=True, use_container_width=True, on_select="rerun",
                      selection_mode="single-row", key=key,
                      column_config={"Score": st.column_config.ProgressColumn(
                          min_value=0 if score_col == "long_score" else -100, max_value=100, format="%.0f")})
    if ev.selection.rows:
        open_share(df.iloc[ev.selection.rows[0]].symbol)
        st.rerun()


def price_chart(px, nd, height=520):
    if px.empty:
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=px.index, open=px.open, high=px.high, low=px.low, close=px.close, name="Price"), 1, 1)
    fig.add_trace(go.Bar(x=px.index, y=px.volume, name="Volume", marker_color="#8aa"), 2, 1)
    for r in nd.itertuples():
        t = pd.to_datetime(r.published).tz_convert("Asia/Kolkata").tz_localize(None).normalize()
        if t >= px.index.min():
            col = "green" if r.sentiment > 0.15 else "red" if r.sentiment < -0.15 else "gray"
            fig.add_trace(go.Scatter(x=[t], y=[px.close.asof(t)], mode="markers", hovertext=r.title, showlegend=False,
                                     marker=dict(size=11, color=col, symbol="diamond")), 1, 1)
    fig.update_layout(height=height, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Diamonds mark news: green positive, red negative.")


def share_report(sym):
    if st.button("← Back to recommended shares"):
        st.session_state.pop("report", None)
        st.rerun()
    rep = report.build(c, cfg, sym)
    st.title(f"{rep['symbol']} — {rep['name']}")
    st.caption(f"Report generated {rep['generated']}")
    for text, tone in rep["verdicts"]:
        {"good": st.success, "bad": st.error}.get(tone, st.info)(text)
    m1, m2 = st.columns(2)
    m1.metric("Short-term score", f"{rep['short']:.0f}", help="-100 (avoid) … +100 (strong)")
    m2.metric("Long-term score", f"{rep['long']:.0f} / 100")
    for i, (title, lines) in enumerate(rep["sections"]):
        st.subheader(title)
        # the verdicts and scores are already shown above as badges
        st.markdown(report.section_markdown(lines[3:] if i == 0 else lines))
        if i == 0:
            price_chart(history(c, sym, 400), news_df("WHERE ns.symbol=?", (sym,), 60), height=420)
    st.download_button("⬇️ Download this report", report.to_html(rep), file_name=f"{sym}_report.html",
                       mime="text/html", use_container_width=True)
    st.divider()
    st.write("**Track this share** (you'll get reminders with reasons)")
    with st.form("track_rep"):
        k1, k2, k3 = st.columns(3)
        kind = k1.selectbox("I…", list(portfolio.KINDS), format_func=portfolio.KINDS.get)
        qty = k2.number_input("Quantity", min_value=0.0, step=1.0)
        price = k3.number_input("Price (bought/sold at, or wanted)", min_value=0.0)
        if st.form_submit_button("Start tracking"):
            want = kind.startswith("want")
            portfolio.add(c, sym, kind, qty or None, None if want else (price or None), (price or None) if want else None)
            st.success("Added.")


# ------------------------------------------------------------------ Ideas
if page == "Ideas & market":
    st.title("Ideas & market")
    st.caption("Click a row to open the share. " + DISCLAIMER)
    short, long = analysis.picks(c)
    a, b = st.columns(2)
    with a:
        st.subheader("Short term (days to weeks)")
        st.caption("Fresh news not yet in the price, buying volume, bulk deals, momentum.")
        pick_table(short, "short_score", "short_reasons", "short")
    with b:
        st.subheader("Long term (months to years)")
        st.caption("Financial strength, long-term news, institutions and big investors adding.")
        pick_table(long, "long_score", "long_reasons", "long")

    st.subheader("Most important news (last 48 hours)")
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    nd = news_df("WHERE n.published>=?", (since,))
    if nd.empty:
        st.info("No news yet. Run an update from 'Data & updates'.")
    else:
        nd = nd.sort_values("impact_score", ascending=False).head(40)
        nd["Effect"] = nd.sentiment.apply(lambda s: "🟢 positive" if s > 0.15 else "🔴 negative" if s < -0.15 else "⚪ neutral")
        nd["Term"] = nd.horizon.map({"short": "short", "long": "long", "both": "short + long"})
        nd["In price?"] = nd.reaction_status.map(STATUS_ICON)
        nd["Move since"] = nd.reaction_pct.apply(lambda x: "" if pd.isna(x) else f"{x:+.1f}%")
        ev = st.dataframe(nd[["symbol", "title", "Effect", "Term", "In price?", "Move since", "source", "link"]],
                          hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                          column_config={"link": st.column_config.LinkColumn("link", display_text="open")}, key="news")
        if ev.selection.rows:
            open_share(nd.iloc[ev.selection.rows[0]].symbol)
            st.rerun()

    a, b = st.columns(2)
    with a:
        st.subheader("FII / DII cash market (₹ crore)")
        fd = pd.read_sql_query("SELECT * FROM fii_dii ORDER BY date DESC LIMIT 40", c)
        if fd.empty:
            st.info("No FII/DII data yet.")
        else:
            fig = go.Figure([go.Bar(x=g.date, y=g.net, name=k) for k, g in fd.groupby("category")])
            fig.update_layout(barmode="group", height=300, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)
            last = fd[fd.date == fd.date.max()]
            st.caption(" | ".join(f"{r.category} net {r.net:+,.0f} cr on {r.date}" for r in last.itertuples()))
    with b:
        st.subheader("Big investors in bulk / block deals (30 days)")
        bd = pd.read_sql_query("SELECT date, symbol, big_investor, client, side, qty, price, kind FROM deals "
                               "WHERE big_investor IS NOT NULL AND date>=? ORDER BY date DESC",
                               c, params=((datetime.now() - timedelta(days=30)).date().isoformat(),))
        if bd.empty:
            st.info("None found. Edit the investor list in config.yaml.")
        else:
            bd["qty"] = bd.qty.apply(fmt_qty)
            st.dataframe(bd, hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ Share details
elif page == "Share details":
    syms = pd.read_sql_query("SELECT symbol, name FROM symbols ORDER BY symbol", c)
    if syms.empty:
        st.info("Share list is empty. Run an update first.")
        st.stop()
    options = (syms.symbol + " — " + syms.name.fillna("")).tolist()
    cur = st.session_state.get("share")
    idx = next((i for i, o in enumerate(options) if o.split(" — ")[0] == cur), 0)
    choice = st.selectbox("Share (type to search)", options, index=idx)
    sym = choice.split(" — ")[0]
    st.session_state["share"] = sym
    st.title(choice)

    f = c.execute("SELECT * FROM fundamentals WHERE symbol=?", (sym,)).fetchone()
    sig = c.execute("SELECT * FROM signals WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)).fetchone()
    if not sig:
        ss, ls, sr, lr = analysis.score_symbol(c, sym, cfg)
    else:
        ss, ls, sr, lr = sig["short_score"], sig["long_score"], json.loads(sig["short_reasons"]), json.loads(sig["long_reasons"])

    st.subheader("Brief")
    st.write(f["brief"] if f else "No financial data yet (it is fetched for shares in the daily deep-analysis list, "
             "or for any share you add to your watchlist).")
    m1, m2, m3, m4 = st.columns(4)
    px = history(c, sym, 400)
    if not px.empty:
        last, prev = px.close.iloc[-1], px.close.iloc[-2] if len(px) > 1 else px.close.iloc[-1]
        m1.metric("Last price", f"₹{last:,.2f}", f"{(last/prev-1)*100:+.2f}%")
    m2.metric("Short-term score", f"{ss:.0f}", help="-100 (avoid) … +100 (strong)")
    m3.metric("Long-term score", f"{ls:.0f} / 100")
    if f:
        m4.metric("Financial health", f"{f['score']:.0f} / 100")
    a, b = st.columns(2)
    a.markdown("**Short term view**\n" + "\n".join(f"- {sources.md(x)}" for x in sr) if sr else "**Short term view**\n- No strong signals")
    b.markdown("**Long term view**\n" + "\n".join(f"- {sources.md(x)}" for x in lr))

    nd = news_df("WHERE ns.symbol=?", (sym,), 60)
    price_chart(px, nd)

    st.subheader("Shares bought & sold daily")
    act = analysis.activity(c, sym)
    if act:
        a1, a2, a3, a4 = st.columns(4)
        a1.metric(f"Traded qty ({act['date']})", fmt_qty(act["traded_qty"]),
                  None if act["qty_change_pct"] is None else f"{act['qty_change_pct']:+.0f}% vs prev day")
        a2.metric("Delivery qty (actually taken home)", fmt_qty(act["delivery_qty"]),
                  None if pd.isna(act["delivery_pct"]) else f"{act['delivery_pct']:.0f}% of traded", delta_color="off")
        a3.metric("Volume vs 20-day avg", "–" if not act["vol_ratio"] else f"{act['vol_ratio']:.1f}x")
        a4.metric("Delivery vs 20-day avg", "–" if not act["del_ratio"] else f"{act['del_ratio']:.1f}x")
        t = act["table"].tail(20)
        fig = go.Figure([go.Bar(x=t.date, y=t.traded_qty, name="Traded"), go.Bar(x=t.date, y=t.delivery_qty, name="Delivery")])
        fig.update_layout(barmode="overlay", height=250, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Every trade has a buyer and a seller, so 'bought' = 'sold' = traded quantity. Rising delivery "
                   "quantity with rising price usually means real buying; heavy volume with falling price means selling pressure.")
    else:
        st.info("No volume data yet.")

    a, b = st.columns(2)
    with a:
        st.subheader("Bulk & block deals (90 days)")
        ds = analysis.deals_summary(c, sym, 90)
        if ds["df"].empty:
            st.write("None.")
        else:
            d = ds["df"][["date", "client", "side", "qty", "price", "kind", "big_investor"]].copy()
            d["qty"] = d.qty.apply(fmt_qty)
            st.dataframe(d, hide_index=True, use_container_width=True)
    with b:
        st.subheader("Who owns it")
        sh = analysis.shareholding_change(c, sym)
        if not sh:
            st.write("No shareholding data yet.")
        else:
            rows = []
            for k, lbl in (("promoter", "Promoters"), ("fii", "FII / FPI"), ("dii", "DII (all domestic institutions)"),
                           ("mutual_funds", "Mutual funds"), ("public", "Public")):
                v = sh.get(k)
                chg = sh.get(k + "_chg")
                rows.append({"Holder": lbl, "%": "–" if v is None else f"{v:.2f}%",
                             "Change vs last quarter": "" if chg is None else f"{chg:+.2f}%"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.caption(f"Quarter ending {sh['period']}.")
            if sh["big_holders"]:
                st.success("Well-known investors in the shareholder list: " + ", ".join(sh["big_holders"]))

    st.subheader("News and its likely effect")
    if nd.empty:
        st.write("No news matched to this share yet.")
    for r in nd.itertuples():
        icon = "🟢" if r.sentiment > 0.15 else "🔴" if r.sentiment < -0.15 else "⚪"
        when = pd.to_datetime(r.published).tz_convert("Asia/Kolkata").strftime("%d %b %H:%M")
        with st.expander(f"{icon} {when} · {r.title}"):
            st.write(r.explanation)
            st.write("**Effect:** " + HORIZON_TEXT.get(r.horizon, r.horizon))
            st.write("**Already in the price?** " + analysis.reaction_text(r.reaction_status, None if pd.isna(r.reaction_pct) else r.reaction_pct, r.horizon))
            if not pd.isna(r.move_5d):
                st.write(f"**5 trading days later:** {r.move_5d:+.1f}%")
            st.markdown(f"Source: [{r.source}]({r.link}) · {when}")

    st.divider()
    with st.form("track"):
        st.write("**Track this share**")
        k1, k2, k3, k4, k5 = st.columns(5)
        kind = k1.selectbox("I…", list(portfolio.KINDS), format_func=portfolio.KINDS.get)
        qty = k2.number_input("Quantity", min_value=0.0, step=1.0)
        price = k3.number_input("Price I bought/sold at", min_value=0.0)
        target = k4.number_input("Target / wanted price", min_value=0.0)
        stop = k5.number_input("Stop loss", min_value=0.0)
        if st.form_submit_button("Start tracking"):
            portfolio.add(c, sym, kind, qty or None, price or None, target or None, stop or None)
            st.success("Added. You'll get reminders when something needs attention.")

# ------------------------------------------------------------------ Recommended
elif page == "Recommended shares":
    if st.session_state.get("report"):
        share_report(st.session_state["report"])
        st.stop()
    st.title("Recommended shares")
    st.caption("Based on today's news and the data gathered (news not yet in the price, buying volume, deals, "
               "big investors, holdings, financials). Tap a share for its full report. " + DISCLAIMER)
    short, long, avoid = report.recommended(c, cfg)
    if not (short.empty and long.empty and avoid.empty):
        st.download_button("⬇️ Download all recommendations + reports (HTML)", report.recommended_html(c, cfg, demo),
                           file_name="recommended_shares.html", mime="text/html", use_container_width=True)
    with st.container(border=True):
        st.markdown("**Track record** (the tracker checks its own past calls against what the price did next)  \n"
                    + "  \n".join(analysis.track_text(analysis.track_record(c))))
    if short.empty and long.empty and avoid.empty:
        st.info("No recommendations yet. Run an update from 'Data & updates'.")
    tabs = st.tabs([f"Short term ({len(short)})", f"Long term ({len(long)})", f"Avoid / careful ({len(avoid)})"])
    for tab, df, col, rc, key in ((tabs[0], short, "short_score", "short_reasons", "s"),
                                  (tabs[1], long, "long_score", "long_reasons", "l"),
                                  (tabs[2], avoid, "short_score", "short_reasons", "a")):
        with tab:
            if df.empty:
                st.write("No shares in this list today.")
            for r in df.itertuples():
                reasons = getattr(r, rc) or getattr(r, "long_reasons")
                label = f"{r.symbol} · {r.name or ''}  —  score {getattr(r, col):.0f}"
                if st.button(label, key=f"{key}_{r.symbol}", use_container_width=True):
                    st.session_state["report"] = r.symbol
                    st.rerun()
                st.caption(" • ".join(sources.plain(x) for x in reasons[:2]))

# ------------------------------------------------------------------ Portfolio
elif page == "My shares & watchlist":
    st.title("My shares & watchlist")
    with st.form("add"):
        st.write("**Tell the tracker what you did or want to do**")
        k0, k1, k2, k3, k4, k5 = st.columns([2, 2, 1, 1, 1, 1])
        share = k0.text_input("Share (symbol or name)")
        kind = k1.selectbox("I…", list(portfolio.KINDS), format_func=portfolio.KINDS.get)
        qty = k2.number_input("Qty", min_value=0.0, step=1.0)
        price = k3.number_input("At price", min_value=0.0)
        target = k4.number_input("Target / wanted price", min_value=0.0)
        stop = k5.number_input("Stop loss", min_value=0.0)
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Add") and share:
            try:
                pid, s = portfolio.add(c, share, kind, qty or None, price or None, target or None, stop or None, note)
                st.success(f"Tracking {s}. Its news, prices and financials are fetched in the next update.")
            except ValueError as e:
                st.error(str(e))
    pos = portfolio.active(c)
    if pos.empty:
        st.info("Nothing tracked yet.")
    else:
        rows = []
        for p in pos.itertuples():
            h = history(c, p.symbol, 2)
            last = h.close.iloc[-1] if not h.empty else None
            pnl = ((last / p.price - 1) * 100 * (-1 if p.kind == "short" else 1)) if last and p.price and p.kind in ("bought", "short") else None
            rows.append({"#": p.id, "Share": p.symbol, "Company": p.name, "Status": portfolio.KINDS[p.kind],
                         "Qty": p.qty, "My price": p.price, "Target": p.target, "Stop": p.stop_loss,
                         "Last": None if last is None else round(float(last), 2), "P&L %": None if pnl is None else round(pnl, 2),
                         "P&L ₹": None if pnl is None or not p.qty else round((last - p.price) * p.qty * (-1 if p.kind == "short" else 1), 0)})
        df = pd.DataFrame(rows)
        ev = st.dataframe(df, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row", key="pos")
        if ev.selection.rows:
            open_share(df.iloc[ev.selection.rows[0]]["Share"])
            st.rerun()
        rm = st.selectbox("Stop tracking entry #", [None] + df["#"].tolist())
        if rm and st.button("Remove"):
            portfolio.remove(c, int(rm))
            st.rerun()
        if st.button("Check reminders now"):
            new = alerts.run_alerts(c, cfg)
            st.success(f"{len(new)} new reminder(s).")
    st.subheader("Latest reminders")
    al = pd.read_sql_query("SELECT created_at, title, body FROM alerts ORDER BY id DESC LIMIT 20", c)
    for r in al.itertuples():
        with st.expander(f"{r.created_at[:16]} · {r.title}"):
            st.markdown(alert_md(r.body))

# ------------------------------------------------------------------ Reminders
elif page == "Reminders":
    st.title("Reminders")
    st.caption(DISCLAIMER)
    al = pd.read_sql_query("SELECT * FROM alerts ORDER BY id DESC LIMIT 300", c)
    if al.empty:
        st.info("No reminders yet.")
    syms = ["All"] + sorted(al.symbol.unique().tolist()) if not al.empty else ["All"]
    flt = st.selectbox("Share", syms)
    for r in al.itertuples():
        if flt != "All" and r.symbol != flt:
            continue
        with st.expander(f"{r.created_at[:16]} · {r.title}"):
            st.markdown(alert_md(r.body))

# ------------------------------------------------------------------ Data
else:
    st.title("Data & updates")
    st.caption(f"The tracker refreshes by itself every day at {cfg['schedule']['full_update']} (or as soon as it is "
               "opened, if that time was missed) and checks news every hour on market days. Change the time in config.yaml.")
    a, b, d = st.columns(3)
    if a.button("↻ Refresh now (full)", disabled=demo or pipeline.is_running()):
        with st.spinner("Updating: share list, news, deals, prices, financials… this can take several minutes."):
            steps = pipeline.run_now(cfg, "full")
        if steps:
            st.dataframe(pd.DataFrame(steps), hide_index=True, use_container_width=True)
    if b.button("Quick news + reminders", disabled=demo or pipeline.is_running()):
        with st.spinner("Checking news…"):
            steps = pipeline.run_now(cfg, "quick")
        if steps:
            st.dataframe(pd.DataFrame(steps), hide_index=True, use_container_width=True)
    if d.button("Send a test notification"):
        from stocktracker import notify
        notify.send(cfg["notifications"], "Test from Stock Tracker", "Notifications are working.")
        st.success("Sent (check desktop / Telegram / e-mail).")
    for key, label in (("last_full_update", "Last full update"), ("last_quick_update", "Last quick update")):
        v = db.get_meta(c, key)
        if v:
            v = json.loads(v)
            st.subheader(f"{label}: {v['at']}")
            st.dataframe(pd.DataFrame(v["steps"]), hide_index=True, use_container_width=True)
    st.subheader("Records kept")
    st.caption("Everything collected stays in the database on this computer, so the analysis can compare today "
               "with the past. Each row shows where the data comes from and when it was last fetched.")
    rec = []
    for table, label, dcol, key in (("news", "News items", "published", None),
                                     ("prices", "Daily prices", "date", "prices"),
                                     ("delivery", "Daily traded & delivery quantity", "date", "delivery"),
                                     ("deals", "Bulk & block deals", "date", "deals"),
                                     ("fii_dii", "FII / DII activity", "date", "fii"),
                                     ("shareholding", "Shareholding by quarter", "period", "shp"),
                                     ("fundamentals", "Financials", "updated_at", "fund"),
                                     ("signals", "Daily scores (history)", "date", "records")):
        n, lo, hi = c.execute(f"SELECT COUNT(*), MIN({dcol}), MAX({dcol}) FROM {table}").fetchone()
        rec.append({"Data": label, "Rows": n, "From": (lo or "")[:10], "To": (hi or "")[:10],
                    "Source": "RSS feeds + Google News (each item links to its publisher)" if key is None
                    else sources.SOURCES[key][0],
                    "Last fetched": "" if key in (None, "records") else (sources.fetched(c, key) or "")})
    st.dataframe(pd.DataFrame(rec), hide_index=True, use_container_width=True)
    st.write("**Download records (CSV)**")
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button("News", pd.read_sql_query(
        "SELECT n.published, ns.symbol, n.title, n.source, n.link, ns.event_type, ns.sentiment, ns.horizon, "
        "ns.reaction_status, ns.reaction_pct, ns.move_5d FROM news_symbols ns JOIN news n ON n.id=ns.news_id "
        "ORDER BY n.published DESC", c).to_csv(index=False), "news.csv", "text/csv", use_container_width=True)
    d2.download_button("Deals", pd.read_sql_query("SELECT * FROM deals ORDER BY date DESC", c).to_csv(index=False),
                       "deals.csv", "text/csv", use_container_width=True)
    d3.download_button("Score history", pd.read_sql_query(
        "SELECT date, symbol, short_score, long_score, price FROM signals ORDER BY date DESC, symbol", c).to_csv(index=False),
        "score_history.csv", "text/csv", use_container_width=True)
    d4.download_button("FII / DII", pd.read_sql_query("SELECT * FROM fii_dii ORDER BY date DESC", c).to_csv(index=False),
                       "fii_dii.csv", "text/csv", use_container_width=True)
