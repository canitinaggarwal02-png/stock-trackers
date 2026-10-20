"""Telegram bot: use the tracker from your phone, from anywhere.

It only answers the chat_id in config.yaml, so nobody else can use it."""
import logging
import threading
import time

import requests

from . import alerts, db, pipeline, portfolio, report, textviews

log = logging.getLogger(__name__)

HELP = """📱 Stock Tracker commands

/picks – short & long term ideas
/share INFY – quick details of a share
/report INFY – full report: recommendation, reasons and all data
/recommended – recommended shares today
/html – recommended shares + full reports as an HTML file
/news – most important news today
/news INFY – news for one share
/my – your shares, watchlist and P&L
/fii – FII / DII buying and selling
/bigdeals – deals by well-known investors

Tell me what you did or want:
/bought INFY 10 1500 [stop] [target]
/sold INFY 1650   (sold at 1650, watch for re-entry)
/short INFY 10 1500 [stop] [target]
/wantbuy INFY 1400   (remind me at ₹1400)
/wantsell INFY 1800
/remove 3   (stop tracking #3)

/alerts – check reminders now
/update – fetch fresh news now
Names work too: /share tata motors"""

KIND_CMD = {"bought": "bought", "buy": "bought", "sold": "sold", "sell": "sold", "short": "short",
            "wantbuy": "want_buy", "wantsell": "want_sell"}


def _nums(parts):
    """Split trailing numbers from the share name: ['tata','motors','10','900'] -> ('tata motors', [10, 900])."""
    nums = []
    while parts:
        try:
            nums.insert(0, float(parts[-1].replace(",", "").replace("₹", "")))
            parts = parts[:-1]
        except ValueError:
            break
    return " ".join(parts), nums


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        t = cfg["notifications"].get("telegram") or {}
        self.token, self.chat_id = t.get("bot_token"), str(t.get("chat_id") or "")
        self.api = f"https://api.telegram.org/bot{self.token}/"
        self.offset = None

    def send(self, chat, text):
        for i in range(0, len(text), 3900):            # Telegram limit is 4096 characters
            requests.post(self.api + "sendMessage", json={"chat_id": chat, "text": text[i:i + 3900],
                                                          "disable_web_page_preview": True}, timeout=20)

    def handle(self, chat, text):
        if not self.chat_id:
            return (f"Hello! Your chat id is {chat}. Put it in config.yaml under notifications → telegram → "
                    f"chat_id, set enabled: true, and restart the tracker.")
        if str(chat) != self.chat_id:
            return None                                # ignore strangers
        parts = text.strip().split()
        if not parts:
            return HELP
        cmd = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1:]
        con = db.connect(self.cfg["db_path"])
        try:
            if cmd in ("start", "help"):
                return HELP
            if cmd == "report" and args:
                return report.to_text(report.build(con, self.cfg, portfolio.resolve(con, " ".join(args))))
            if cmd == "html":
                path = report.write_recommended_html(con, self.cfg, demo=db.get_meta(con, "demo_mode") == "1")
                with open(path, "rb") as fh:
                    requests.post(self.api + "sendDocument", data={"chat_id": chat, "caption": "Recommended shares and reports"},
                                  files={"document": ("recommended_shares.html", fh, "text/html")}, timeout=60)
                return "📄 Sent. Open the file and tap a share name for its full report."
            if cmd in ("picks", "recommended"):
                return textviews.picks(con)
            if cmd == "share" and args:
                return textviews.share(con, self.cfg, " ".join(args))
            if cmd == "news":
                return textviews.news(con, portfolio.resolve(con, " ".join(args)) if args else None)
            if cmd in ("my", "list", "portfolio"):
                return textviews.my(con)
            if cmd == "fii":
                return textviews.fii(con)
            if cmd == "bigdeals":
                return textviews.big_deals(con)
            if cmd == "remove" and args:
                portfolio.remove(con, int(args[0]))
                return f"Stopped tracking #{args[0]}."
            if cmd in KIND_CMD and args:
                kind = KIND_CMD[cmd]
                name, n = _nums(args)
                if kind in ("bought", "short"):
                    if len(n) < 2:
                        return f"Please send quantity and price, e.g. /{cmd} INFY 10 1500"
                    q, price, stop, target = n[0], n[1], (n[2] if len(n) > 2 else None), (n[3] if len(n) > 3 else None)
                elif kind == "sold":
                    if not n:
                        return "Please send the price you sold at, e.g. /sold INFY 1650"
                    q, price, stop, target = (n[0], n[1], None, None) if len(n) > 1 else (None, n[0], None, None)
                else:
                    if not n:
                        return f"Please send your price, e.g. /{cmd} INFY 1400"
                    q, price, stop, target = None, None, None, n[0]
                pid, sym = portfolio.add(con, name, kind, q, price, target, stop, "added from phone")
                return (f"✅ Tracking #{pid}: {sym} ({portfolio.KINDS[kind]}). I'll remind you when something needs "
                        f"attention. Its news and financials are fetched in the next update (or send /update).")
            if cmd == "alerts":
                new = alerts.run_alerts(con, self.cfg)
                return f"{len(new)} new reminder(s)." + ("" if new else " Nothing needs your attention right now.")
            if cmd == "update":
                threading.Thread(target=self._update, args=(chat,), daemon=True).start()
                return "⏳ Fetching fresh news and prices… I'll message you when done."
            return HELP
        except ValueError as e:
            return f"⚠️ {e}"
        except Exception as e:
            log.exception("bot command failed")
            return f"⚠️ Something went wrong: {e}"
        finally:
            con.close()

    def _update(self, chat):
        steps = pipeline.quick_update(self.cfg)
        bad = [s["step"] for s in steps if not s["ok"]]
        self.send(chat, "✅ Update done." + (f" These steps failed: {', '.join(bad)}" if bad else "")
                  + " Send /picks or /my.")

    def run(self):
        if not self.token:
            raise SystemExit("Add your Telegram bot_token to config.yaml first (see README → Phone).")
        log.info("Telegram bot running. Open your bot in Telegram and send /help.")
        while True:
            try:
                r = requests.get(self.api + "getUpdates", params={"timeout": 50, "offset": self.offset}, timeout=60)
                for u in r.json().get("result", []):
                    self.offset = u["update_id"] + 1
                    m = u.get("message") or u.get("edited_message") or {}
                    if "text" not in m:
                        continue
                    reply = self.handle(m["chat"]["id"], m["text"])
                    if reply:
                        self.send(m["chat"]["id"], reply)
            except Exception as e:
                log.warning("Telegram connection problem (%s); retrying", e)
                time.sleep(10)


def start_in_background(cfg):
    t = threading.Thread(target=Bot(cfg).run, daemon=True, name="telegram-bot")
    t.start()
    return t

