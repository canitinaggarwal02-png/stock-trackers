"""The daily job: refresh the share list, gather news and exchange data,
analyse, and send reminders."""
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import alerts, analysis, db, fundamentals, market_data, news, prices, report, symbols
from .portfolio import tracked_symbols

log = logging.getLogger(__name__)


class Run:
    def __init__(self):
        self.steps = []

    def step(self, name, fn, *a, **kw):
        t = time.time()
        log.info("▶ %s", name)
        try:
            res = fn(*a, **kw)
            failed = ((isinstance(res, tuple) and len(res) == 2 and not res[0] and res[1])
                      or (isinstance(res, dict) and res.get("feeds_failed") and not res.get("feeds_ok")))
            self.steps.append(dict(step=name, ok=not failed, result=_short(res), secs=round(time.time() - t, 1)))
            return res
        except Exception as e:
            log.exception("%s failed", name)
            self.steps.append(dict(step=name, ok=False, result=str(e)[:300], secs=round(time.time() - t, 1)))
            return None


def _short(res):
    s = json.dumps(res, default=str)
    return s if len(s) < 400 else s[:400] + "…"


def most_active(con, n):
    d = con.execute("SELECT MAX(date) FROM delivery").fetchone()[0]
    if not d:
        return []
    return [r[0] for r in con.execute(
        "SELECT symbol FROM delivery WHERE date=? ORDER BY turnover_lacs DESC LIMIT ?", (d, n))]


def news_symbols_since(con, hours):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return [r[0] for r in con.execute("""SELECT ns.symbol, SUM(ns.impact_score) s FROM news_symbols ns
        JOIN news n ON n.id=ns.news_id WHERE n.published>=? GROUP BY ns.symbol ORDER BY s DESC""", (since,))]


def deal_symbols_since(con, days):
    return [r[0] for r in con.execute("SELECT DISTINCT symbol FROM deals WHERE date>=?",
                                      ((date.today() - timedelta(days=days)).isoformat(),))]


def _uniq(*lists, limit=None):
    out, seen = [], set()
    for l in lists:
        for s in l:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out[:limit] if limit else out


def full_update(cfg):
    con = db.connect(cfg["db_path"])
    run = Run()
    big = cfg["big_investors"]
    if db.get_meta(con, "symbols_date") != date.today().isoformat():
        if run.step("Refresh NSE/BSE share list", symbols.refresh_symbols, con):
            db.set_meta(con, "symbols_date", date.today().isoformat())
    run.step("Daily traded & delivery quantity (NSE)", market_data.update_delivery, con)
    run.step("Bulk & block deals", market_data.update_deals, con, big)
    run.step("FII / DII activity", market_data.update_fii_dii, con)

    tracked = tracked_symbols(con)
    limit = cfg["news"]["google_news_company_limit"]
    focus = _uniq(tracked, deal_symbols_since(con, 3), most_active(con, limit), limit=max(limit, len(tracked)))
    universe = _uniq(tracked, most_active(con, cfg["deep_analysis_limit"]))
    run.step("News from India & around the world", news.collect_news, con, cfg, focus, universe)

    deep = _uniq(tracked, news_symbols_since(con, cfg["news"]["lookback_hours"]), deal_symbols_since(con, 7),
                 most_active(con, cfg["deep_analysis_limit"]), limit=max(cfg["deep_analysis_limit"], len(tracked)))
    run.step("Price history (Yahoo Finance)", prices.update_prices, con, deep)
    run.step("Financials", fundamentals.update_fundamentals, con, deep)
    run.step("Shareholding (promoter / FII / DII / MF)", market_data.update_shareholding, con,
             [s for s in deep if not s.isdigit()], big)
    run.step("Did the news move the price yet?", analysis.update_news_reactions, con)
    run.step("Learn from past news (price 5 days later)", analysis.update_moves_5d, con)
    run.step("Short & long term scores (saved to history)", analysis.update_signals, con, deep, cfg)
    run.step("Reminders for your shares", lambda: len(alerts.run_alerts(con, cfg)))
    run.step("Recommended shares HTML file", report.write_recommended_html, con, cfg)
    db.set_meta(con, "last_full_update", json.dumps(dict(at=datetime.now().isoformat(timespec="seconds"),
                                                         steps=run.steps)))
    con.close()
    return run.steps


def quick_update(cfg):
    """Intraday: fresh news for your shares and the most active ones, then reminders."""
    con = db.connect(cfg["db_path"])
    run = Run()
    tracked = tracked_symbols(con)
    focus = _uniq(tracked, most_active(con, 30))
    run.step("News", news.collect_news, con, cfg, focus, focus)
    run.step("Price update", prices.update_prices, con, tracked, "5d")
    run.step("Did the news move the price yet?", analysis.update_news_reactions, con, 3)
    run.step("Scores", analysis.update_signals, con,
             _uniq(tracked, news_symbols_since(con, 24))[: cfg["deep_analysis_limit"]], cfg)
    run.step("Reminders for your shares", lambda: len(alerts.run_alerts(con, cfg)))
    db.set_meta(con, "last_quick_update", json.dumps(dict(at=datetime.now().isoformat(timespec="seconds"),
                                                          steps=run.steps)))
    con.close()
    return run.steps


# ---------- automatic daily refresh and the Refresh button ----------

_LOCK = threading.Lock()
IST = timezone(timedelta(hours=5, minutes=30))


def _last(con, key):
    v = db.get_meta(con, key)
    try:
        return datetime.fromisoformat(json.loads(v)["at"]) if v else None
    except (ValueError, KeyError):
        return None


def due(cfg, now=None):
    """'full' when today's daily refresh hasn't run yet and its time has passed (or the
    last one is over a day old), 'quick' for the hourly news check on market days, else None."""
    now = now or datetime.now()
    con = db.connect(cfg["db_path"])
    try:
        if db.get_meta(con, "demo_mode") == "1":
            return None
        s = cfg["schedule"]
        h, m = map(int, s["full_update"].split(":"))
        last_full = _last(con, "last_full_update")
        today_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if last_full is None or (now >= today_time and last_full < today_time) or now - last_full > timedelta(hours=24):
            return "full"
        last_quick = max(filter(None, [_last(con, "last_quick_update"), last_full]))
        every = max(5, int(s.get("intraday_news_every_min") or 60))
        if now.weekday() < 5 and 9 <= now.hour < 16 and now - last_quick >= timedelta(minutes=every):
            return "quick"
        return None
    finally:
        con.close()


def is_running():
    return _LOCK.locked()


def run_now(cfg, kind="full"):
    """Run an update unless one is already running (in this program). Returns the steps or None."""
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        return (full_update if kind == "full" else quick_update)(cfg)
    finally:
        _LOCK.release()


def run_in_background(cfg, kind="full"):
    if is_running():
        return False
    threading.Thread(target=run_now, args=(cfg, kind), daemon=True, name=f"refresh-{kind}").start()
    return True


def auto_loop(cfg, check_every=60):
    """Checks every minute and runs the daily / hourly refresh when due. Used by the
    dashboard (while it is open) and by `run.py scheduler`."""
    while True:
        try:
            kind = due(cfg)
            if kind and not is_running():
                log.info("Automatic %s refresh", kind)
                run_now(cfg, kind)
        except Exception:
            log.exception("automatic refresh failed")
        time.sleep(check_every)
