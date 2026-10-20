"""Keeps the tracker running: full update every morning and evening, news and
reminders every hour during market days. Leave it running in a terminal, or
start it at login (see README)."""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from . import pipeline

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


def run_forever(cfg):
    s = cfg["schedule"]
    sched = BlockingScheduler(timezone=IST)
    for t in (s["full_update"], s["after_market"]):
        h, m = map(int, t.split(":"))
        sched.add_job(pipeline.run_now, "cron", args=[cfg, "full"], hour=h, minute=m, misfire_grace_time=3600,
                      id=f"full_{t}")
    sched.add_job(pipeline.run_now, "cron", args=[cfg, "quick"], day_of_week="mon-fri", hour="9-16",
                  minute=f"*/{max(5, int(s['intraday_news_every_min']))}" if s["intraday_news_every_min"] < 60 else "15",
                  misfire_grace_time=900, id="quick")
    tg = cfg["notifications"].get("telegram") or {}
    if tg.get("enabled") and tg.get("bot_token"):
        from .bot import start_in_background
        start_in_background(cfg)
        log.info("Telegram bot started: send /help to your bot from your phone.")
    if pipeline.due(cfg) == "full":
        log.info("Scheduler started (India time). Today's refresh hasn't run yet, so running it now; press Ctrl+C to stop.")
        sched.add_job(pipeline.run_now, args=[cfg, "full"], next_run_time=datetime.now(IST), id="first")
    else:
        log.info("Scheduler started (India time). Press Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
